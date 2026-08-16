"""PetEngine 测试（此前 19% 覆盖）：启用开关、消息构建、会话 id"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import pytest

from config.settings import settings
from modules.desktop_pet.pet_engine import PetEngine


def _engine():
    pe = PetEngine.__new__(PetEngine)
    pe._client = None
    pe._event_bus = None
    pe._sub_id = ""
    pe._running = False
    pe._reply_listeners = []
    pe.last_reply = None
    pe._build_context = AsyncMock(return_value="")
    return pe


@pytest.fixture
def pet_repo(tmp_path, monkeypatch):
    """真实 SessionRepository（临时 SQLite）"""
    import modules.database.connection as conn
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "pet.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    conn.get_db_manager().initialize()
    repo = SessionRepository()
    repo.create_session("pet_main")
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    return repo


def _real_client():
    from infra.model.large_model_client import LargeModelClient
    return LargeModelClient(api_key="t", api_url="http://localhost:1/v1")


def test_enabled_property(monkeypatch):
    pe = _engine()
    monkeypatch.setattr(settings, "DESKTOP_PET_ENABLED", True)
    assert pe.enabled is True
    monkeypatch.setattr(settings, "DESKTOP_PET_ENABLED", False)
    assert pe.enabled is False


def test_pet_session_id_default(monkeypatch):
    pe = _engine()
    monkeypatch.setattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main")
    assert pe.pet_session_id == "pet_main"


def test_build_messages(monkeypatch, pet_repo):
    """真实 repo：历史消息注入 + 系统人设 + 用户消息"""
    pe = _engine()
    pe._client = _real_client()
    pet_repo.save_message("pet_main", "user", "旧消息")
    pet_repo.save_message("pet_main", "assistant", "旧回复")
    msgs = pe._build_messages("你好")
    assert msgs[0].role == "system"
    assert "桌宠" in msgs[0].content
    assert any(m.role == "user" and m.content == "旧消息" for m in msgs)
    assert msgs[-1].role == "user"
    assert msgs[-1].content == "你好"


def test_build_messages_extra_system(monkeypatch, pet_repo):
    """真实 repo 空历史 + extra_system 注入"""
    pe = _engine()
    pe._client = _real_client()
    msgs = pe._build_messages("嗨", extra_system="当前心情很好")
    assert "当前心情很好" in msgs[0].content
    assert msgs[-1].content == "嗨"


def _pe(monkeypatch):
    from modules.desktop_pet.pet_engine import PetEngine
    import modules.desktop_pet.pet_engine as mod
    monkeypatch.setattr(mod.settings, "DESKTOP_PET_ENABLED", True)
    pe = PetEngine.__new__(PetEngine)
    pe._client = None
    pe._event_bus = None
    pe._sub_id = ""
    pe._running = False
    pe._reply_listeners = []
    pe.last_reply = None
    pe._build_context = AsyncMock(return_value="")
    return pe


def test_start_stop(monkeypatch):
    e = _pe(monkeypatch)
    e._ensure_pet_session = lambda: None
    e.start()
    assert e._running is True
    e.start()  # 幂等
    assert e._running is True
    e.stop()
    assert e._running is False


def test_start_with_event_bus(monkeypatch):
    e = _pe(monkeypatch)
    bus = MagicMock()
    bus.subscribe.return_value = "sub"
    e._event_bus = bus
    e._ensure_pet_session = lambda: None
    e.start()
    assert e._sub_id == "sub"
    e.stop()
    assert e._sub_id == ""


def test_ensure_pet_session(pet_repo):
    from modules.desktop_pet.pet_engine import PetEngine
    e = PetEngine(event_bus=None)  # 真实构造
    e._ensure_pet_session()
    assert pet_repo.get_all_sessions()  # 会话已创建


def test_is_active(monkeypatch):
    e = _pe(monkeypatch)
    assert e.is_active() is False
    e._running = True
    assert e.is_active() is True


def test_build_messages(monkeypatch):
    e = _pe(monkeypatch)
    e._client = _real_client()
    msgs = e._build_messages("你好", extra_system="补充")
    assert msgs[0].role == "system"
    assert "补充" in msgs[0].content
    assert msgs[-1].content == "你好"


def test_save_pair(pet_repo):
    from modules.desktop_pet.pet_engine import PetEngine
    e = PetEngine(event_bus=None)  # 真实构造
    e._save_pair("问", "答")
    msgs = pet_repo.get_recent_messages("pet_main", limit=10)
    assert [m["content"] for m in msgs] == ["问", "答"]


def test_after_reply(monkeypatch):
    e = _pe(monkeypatch)
    e.last_reply = None
    import modules.output_system.tts as tts_mod
    engine = MagicMock()
    engine.synthesize_sync.return_value = "/tmp/pet.mp3"
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    import modules.thinking.api_stream as stream_mod
    cm = MagicMock()
    cm.active_connections = {}
    monkeypatch.setattr(stream_mod, "connection_manager", cm)
    monkeypatch.setattr(stream_mod, "_build_event", lambda **kw: {"event": "pet_reply"})
    import modules.desktop_pet.pet_engine as mod
    monkeypatch.setattr(mod, "_play_audio", lambda p: None)
    asyncio.run(e._after_reply("回复内容"))
    assert e.last_reply["text"] == "回复内容"


def test_build_context(monkeypatch):
    from modules.desktop_pet.pet_engine import PetEngine
    e = _pe(monkeypatch)
    e._build_context = PetEngine._build_context.__get__(e)  # 恢复真实实现
    frag = MagicMock()
    frag.content = "感知信息"
    class FakePS:
        async def collect(self):
            return frag
    import modules.thinking.context.sources.perception_source as ps_mod
    monkeypatch.setattr(ps_mod, "PerceptionSource", FakePS)
    import modules.memory.event_retrieval as er_mod
    retrieval = MagicMock()
    ev = type("E", (), {"time": "2025-01-01 10:00", "fact": "过去的任务"})()
    retrieval.retrieve = AsyncMock(return_value=[ev])
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retrieval)
    out = asyncio.run(e._build_context("查询"))
    assert "感知信息" in out


def test_pet_engine_real_init():
    """PetEngine 真实构造（不 mock __init__）"""
    from modules.desktop_pet.pet_engine import PetEngine
    pe = PetEngine(event_bus=None)  # 真实 __init__
    assert pe._running is False
    assert pe._client is None
    assert pe.last_reply is None
    assert pe._sub_id == ""
    pe2 = PetEngine(event_bus=MagicMock())
    assert pe2._event_bus is not None


def test_build_messages_rebuild_on_config_change(monkeypatch, pet_repo):
    """模型配置变更 → _build_messages 按配置指纹重建 client（§53/§46）"""
    created = []
    def _make(*a, **k):
        c = MagicMock()
        created.append(c)
        return c
    # pet_engine._build_messages 函数内 from ... import LargeModelClient → patch 源模块
    monkeypatch.setattr("infra.model.large_model_client.LargeModelClient", _make)
    pe = _engine()
    pe._client = MagicMock()
    pe._client_cfg = ("http://old/v1", "old-key", "old-name", "")
    monkeypatch.setattr(settings, "LARGE_MODEL_API_URL", "http://changed/v1")
    pe._build_messages("hi")
    assert len(created) == 1  # 配置变化 → 重建新 client
    assert pe._client is created[0]


def test_build_messages_keeps_injected_client(monkeypatch, pet_repo):
    """显式注入的 client（_client_cfg 未记录）不重建，仅记录指纹（§53）"""
    pe = _engine()
    injected = MagicMock()
    pe._client = injected
    pe._build_messages("hi")
    assert pe._client is injected  # 不重建
    assert pe._client_cfg is not None  # 记录了当前指纹
