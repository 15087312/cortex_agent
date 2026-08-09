"""PetEngine 测试（此前 19% 覆盖）：启用开关、消息构建、会话 id"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

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


def test_build_messages(monkeypatch):
    pe = _engine()
    pe._client = MagicMock()

    class FakeRepo:
        @staticmethod
        def get_recent_messages(sid, limit=20):
            return [{"role": "user", "content": "旧消息"}, {"role": "assistant", "content": "旧回复"}]

    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: FakeRepo())
    msgs = pe._build_messages("你好")
    assert msgs[0].role == "system"
    assert "桌宠" in msgs[0].content
    # 历史消息加入
    assert any(m.role == "user" and m.content == "旧消息" for m in msgs)
    assert msgs[-1].role == "user"
    assert msgs[-1].content == "你好"


def test_build_messages_extra_system(monkeypatch):
    pe = _engine()
    pe._client = MagicMock()

    class EmptyRepo:
        @staticmethod
        def get_recent_messages(sid, limit=20):
            return []

    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: EmptyRepo())
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


def test_ensure_pet_session(monkeypatch):
    e = _pe(monkeypatch)
    import modules.database.session_repo as sr
    repo = MagicMock()
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    e._ensure_pet_session()
    repo.create_session.assert_called_once()
    repo.set_session_title.assert_called_once()


def test_is_active(monkeypatch):
    e = _pe(monkeypatch)
    assert e.is_active() is False
    e._running = True
    assert e.is_active() is True


def test_build_messages(monkeypatch):
    e = _pe(monkeypatch)
    msgs = e._build_messages("你好", extra_system="补充")
    assert msgs[0].role == "system"
    assert "补充" in msgs[0].content
    assert msgs[-1].content == "你好"


def test_save_pair(monkeypatch):
    e = _pe(monkeypatch)
    import modules.database.session_repo as sr
    repo = MagicMock()
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    e._save_pair("问", "答")
    assert repo.save_message.call_count == 2


def test_chat_success(monkeypatch):
    e = _pe(monkeypatch)
    client = MagicMock()
    resp = MagicMock()
    resp.message.content = "你好呀"
    client.chat = AsyncMock(return_value=resp)
    e._client = client
    e._save_pair = lambda *a: None
    out = asyncio.run(e.chat("hi"))
    assert out == "你好呀"


def test_chat_empty_and_error(monkeypatch):
    e = _pe(monkeypatch)
    client = MagicMock()
    resp = MagicMock()
    resp.message = None
    client.chat = AsyncMock(return_value=resp)
    e._client = client
    assert asyncio.run(e.chat("hi")) == ""
    client.chat = MagicMock(side_effect=RuntimeError)
    assert asyncio.run(e.chat("hi")) == ""


def test_on_speech(monkeypatch):
    e = _pe(monkeypatch)
    ev = type("E", (), {"payload": {"text": "陪我聊聊"}})()
    e.chat = AsyncMock(return_value="好的")
    calls = []
    e._after_reply = MagicMock(side_effect=lambda r: calls.append(r))
    asyncio.run(e._on_speech(ev))
    assert calls == ["好的"]


def test_on_speech_empty(monkeypatch):
    e = _pe(monkeypatch)
    ev = type("E", (), {"payload": {}})()
    e.chat = MagicMock(return_value="x")
    asyncio.run(e._on_speech(ev))
    e.chat.assert_not_called()


def test_chat_stream_task(monkeypatch):
    e = _pe(monkeypatch)
    client = MagicMock()
    resp = MagicMock()
    resp.message.content = "流式回复"
    client.chat_stream = AsyncMock(return_value=resp)
    e._client = client
    e._save_pair = lambda *a: None
    collected = []
    asyncio.run(e._chat_stream_task("hi", lambda t: None, collected))
    assert collected == ["流式回复"]
    assert e.last_reply["text"] == "流式回复"


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
