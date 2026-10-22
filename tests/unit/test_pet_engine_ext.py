"""PetEngine 补充测试（独立页面形态）— 语音触发/对话/流式/上下文分支"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.desktop_pet.pet_engine as pe
from modules.desktop_pet.pet_engine import PetEngine


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    old = PetEngine._instance
    PetEngine._instance = None
    yield
    PetEngine._instance = old


@pytest.fixture(autouse=True)
def _pet_db(tmp_path, monkeypatch):
    """隔离桌宠独立存储"""
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_test.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    yield store
    store_mod.PetStore._instance = None


def _engine(monkeypatch):
    monkeypatch.setattr(pe.settings, "DESKTOP_PET_ENABLED", True)
    e = PetEngine.__new__(PetEngine)
    e._client = None
    e._client_cfg = None
    e._event_bus = None
    e._sub_id = ""
    e._running = False
    e._reply_listeners = []
    e._perception_sub_ids = []  # 感知订阅 id（start/stop 增删，测试桩补齐）
    e.last_reply = None
    e._loop = None
    # 输出前视觉采集不走真实网络（单测里固定为空，聚焦上下文组装逻辑）
    monkeypatch.setattr(PetEngine, "_capture_screen_vision", AsyncMock(return_value=""))
    return e


def _patch_frontend(monkeypatch):
    import modules.thinking.frontend_channel as fc
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda *a, **k: True)
    monkeypatch.setattr(fc, "push_content", AsyncMock(return_value=None))
    return fc


# ── 单例 / 生命周期 ───────────────────────────────────────────────────────

def test_get_instance_singleton(monkeypatch):
    e1 = PetEngine.get_instance()
    e2 = PetEngine.get_instance()
    assert e1 is e2


def test_start_with_event_bus_subscribes(monkeypatch):
    e = _engine(monkeypatch)
    bus = MagicMock()
    bus.subscribe.return_value = "sub-1"
    e._event_bus = bus
    e.start()
    assert e._sub_id == "sub-1"
    assert bus.subscribe.called
    e.stop()
    assert e._sub_id == ""
    assert bus.unsubscribe.called


def test_start_disabled(monkeypatch):
    e = _engine(monkeypatch)
    monkeypatch.setattr(pe.settings, "DESKTOP_PET_ENABLED", False)
    e.start()
    assert e._running is False


def test_start_without_event_bus_warns(monkeypatch):
    e = _engine(monkeypatch)
    e._event_bus = None
    e.start()
    assert e._running is True
    e.stop()


def test_stop_not_running(monkeypatch):
    e = _engine(monkeypatch)
    e.stop()
    assert e._running is False


def test_stop_unsubscribe_error(monkeypatch):
    e = _engine(monkeypatch)
    bus = MagicMock()
    bus.unsubscribe.side_effect = RuntimeError("boom")
    e._event_bus = bus
    e._sub_id = "sub"
    e._running = True
    e.stop()
    assert e._sub_id == ""


# ── 语音触发 ──────────────────────────────────────────────────────────────

def _speech_event(text="你好"):
    return SimpleNamespace(payload={"text": text})


def test_on_speech_disabled(monkeypatch):
    e = _engine(monkeypatch)
    monkeypatch.setattr(pe.settings, "DESKTOP_PET_ENABLED", False)
    e.chat = AsyncMock(return_value="x")
    asyncio.run(e._on_speech(_speech_event()))
    e.chat.assert_not_awaited()


def test_on_speech_no_text(monkeypatch):
    e = _engine(monkeypatch)
    e.chat = AsyncMock(return_value="x")
    asyncio.run(e._on_speech(_speech_event("   ")))
    asyncio.run(e._on_speech(_speech_event("")))
    e.chat.assert_not_awaited()


def test_on_speech_no_payload(monkeypatch):
    e = _engine(monkeypatch)
    e.chat = AsyncMock(return_value="x")
    asyncio.run(e._on_speech(SimpleNamespace(payload=None)))
    asyncio.run(e._on_speech(SimpleNamespace()))
    e.chat.assert_not_awaited()


def test_on_speech_happy_path(monkeypatch):
    e = _engine(monkeypatch)
    e.chat = AsyncMock(return_value="回复")
    e._after_reply = AsyncMock()
    asyncio.run(e._on_speech(_speech_event("你好")))
    e.chat.assert_awaited_once_with("你好")
    e._after_reply.assert_awaited_once_with("回复")


def test_on_speech_empty_reply(monkeypatch):
    e = _engine(monkeypatch)
    e.chat = AsyncMock(return_value="")
    e._after_reply = AsyncMock()
    asyncio.run(e._on_speech(_speech_event("你好")))
    e._after_reply.assert_not_awaited()


def test_on_speech_error(monkeypatch):
    e = _engine(monkeypatch)
    e.chat = AsyncMock(side_effect=RuntimeError("llm down"))
    asyncio.run(e._on_speech(_speech_event("你好")))


# ── _build_context（独立记忆，不再用全局事件库） ──────────────────────────

class _FakePerception:
    def __init__(self, frag):
        self._frag = frag

    async def collect(self):
        return self._frag


def _ctx_engine(monkeypatch):
    e = _engine(monkeypatch)
    e._build_context = PetEngine.build_context.__get__(e)
    return e


def _patch_pet_memory(monkeypatch, mems):
    import modules.desktop_pet.memory as mem_mod
    fake_mem = MagicMock()
    fake_mem.retrieve = AsyncMock(return_value=mems)
    monkeypatch.setattr(mem_mod.PetMemory, "get_instance", lambda: fake_mem)
    return fake_mem


def test_build_context_all_sources(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    e = _ctx_engine(monkeypatch)
    frag = MagicMock()
    frag.content = "感知环境信息"
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(frag))
    _patch_pet_memory(monkeypatch, [{"created_at": "1700000000", "text": "用户喜欢猫"}])
    out = asyncio.run(e._build_context("q"))
    assert "当前时间" in out and "感知环境信息" in out and "用户喜欢猫" in out


def test_build_context_empty_perception_and_memory(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    e = _ctx_engine(monkeypatch)
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(None))
    _patch_pet_memory(monkeypatch, [])
    out = asyncio.run(e._build_context("q"))
    assert "感知环境信息" not in out
    assert "桌宠记忆" not in out


def test_build_context_perception_raises(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    e = _ctx_engine(monkeypatch)
    _patch_pet_memory(monkeypatch, [])
    monkeypatch.setattr(
        ps_mod, "PerceptionSource",
        lambda: (_ for _ in ()).throw(RuntimeError("no camera")),
    )
    out = asyncio.run(e._build_context("q"))
    assert "当前时间" in out


def test_build_context_memory_raises(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    e = _ctx_engine(monkeypatch)
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(None))
    import modules.desktop_pet.memory as mem_mod
    fake_mem = MagicMock()
    fake_mem.retrieve = AsyncMock(side_effect=RuntimeError("mem down"))
    monkeypatch.setattr(mem_mod.PetMemory, "get_instance", lambda: fake_mem)
    out = asyncio.run(e._build_context("q"))
    assert "桌宠记忆" not in out


def test_build_context_memory_unknown_date(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    e = _ctx_engine(monkeypatch)
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(None))
    _patch_pet_memory(monkeypatch, [{"created_at": "abc", "text": "x"}])
    out = asyncio.run(e._build_context("q"))
    assert "未知日期" in out


# ── build_messages（PetStore 历史 + 配置人设） ────────────────────────────

def test_build_messages_extra_system_injected(monkeypatch, _pet_db):
    e = _engine(monkeypatch)
    e._client = MagicMock()
    msgs = e.build_messages("hi", extra_system="补充语境")
    assert msgs[-1].content == "hi"
    assert "补充语境" in msgs[0].content


def test_build_messages_skips_non_dialog_roles(monkeypatch, _pet_db):
    _pet_db.add_history("user", "ok")
    _pet_db.add_history("system", "跳过")
    _pet_db.add_history("tool", "跳过")
    _pet_db.add_history("assistant", "")
    e = _engine(monkeypatch)
    e._client = MagicMock()
    msgs = e.build_messages("hi")
    roles = [m.role for m in msgs]
    assert roles == ["system", "user", "user"]
    assert msgs[-1].content == "hi"


def test_build_messages_store_exception(monkeypatch, _pet_db):
    import modules.desktop_pet.store as store_mod
    e = _engine(monkeypatch)
    e._client = MagicMock()
    monkeypatch.setattr(store_mod.PetStore, "get_instance",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    msgs = e.build_messages("hi")
    assert msgs[0].role == "system"
    assert msgs[-1].content == "hi"


def test_build_messages_no_extra_system(monkeypatch, _pet_db):
    e = _engine(monkeypatch)
    e._client = MagicMock()
    msgs = e.build_messages("hi", extra_system="")
    assert msgs[0].role == "system"
    assert "你是桌面上的 AI 桌宠助手" in msgs[0].content
    assert "补充" not in msgs[0].content


def test_build_messages_uses_configured_persona(monkeypatch, _pet_db):
    _pet_db.update_config({"persona": "高冷桌宠"})
    e = _engine(monkeypatch)
    e._client = MagicMock()
    msgs = e.build_messages("hi")
    assert "高冷桌宠" in msgs[0].content


# ── save_pair（PetStore） ─────────────────────────────────────────────────

def test_save_pair(monkeypatch, _pet_db):
    e = _engine(monkeypatch)
    e.save_pair("问", "答")
    hist = _pet_db.get_history(limit=10)
    assert [m["content"] for m in hist] == ["问", "答"]


def test_save_pair_exception(monkeypatch, _pet_db):
    import modules.desktop_pet.store as store_mod
    e = _engine(monkeypatch)
    monkeypatch.setattr(store_mod.PetStore, "get_instance",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    e.save_pair("问", "答")


# ── chat（走 ReAct 循环） ─────────────────────────────────────────────────

def _loop_engine(monkeypatch, reply="回复内容"):
    e = _engine(monkeypatch)
    loop = MagicMock()
    loop.name = "direct"
    loop.run = AsyncMock(return_value=reply)
    e._loop = loop
    return e, loop


def test_chat_frontend_unreachable(monkeypatch):
    # 桌宠独立页面形态：SSE 请求即连接存在，不再做 WS 握手检查——即使主界面
    # 前端连接不可达也照常走循环（旧契约"不可达即静默丢弃"已删除）
    e = _engine(monkeypatch)
    import modules.thinking.frontend_channel as fc
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda *a, **k: False)
    e._loop = MagicMock()
    e._loop.name = "direct"
    e._loop.run = AsyncMock(return_value="x")
    assert asyncio.run(e.chat("hi")) == "x"
    e._loop.run.assert_awaited_once()


def test_chat_success(monkeypatch):
    _patch_frontend(monkeypatch)
    e, loop = _loop_engine(monkeypatch)
    reply = asyncio.run(e.chat("你好"))
    assert reply == "回复内容"
    loop.run.assert_awaited_once()
    assert loop.run.await_args.args[1] == "你好"


def test_chat_exception(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    loop = MagicMock()
    loop.name = "direct"
    loop.run = AsyncMock(side_effect=RuntimeError("llm down"))
    e._loop = loop
    assert asyncio.run(e.chat("hi")) == ""


# ── stream_chat ───────────────────────────────────────────────────────────

def test_stream_chat_success(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    loop = MagicMock()
    loop.name = "direct"

    async def fake_run(engine, text, on_token=None, on_reasoning=None, on_message=None, extra_system=""):
        on_token("你")
        on_token("好")
        if on_message:
            on_message("你好")
        return "你好"

    loop.run = fake_run
    e._loop = loop

    async def consume():
        return [t async for t in e.stream_chat("你好")]

    tokens = asyncio.run(consume())
    # 过滤轮次完成标记（on_message 后注入）
    assert [t for t in tokens if t != "\x00ROUND_DONE\x00"] == ["你", "好"]
    assert e.last_reply["text"] == "你好"


def test_stream_chat_frontend_unreachable(monkeypatch):
    # 同上：SSE 即连接，不再静默跳过
    e = _engine(monkeypatch)
    import modules.thinking.frontend_channel as fc
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda *a, **k: False)
    loop = MagicMock()
    loop.name = "direct"
    loop.run = AsyncMock(return_value="x")
    e._loop = loop

    async def consume():
        return [t async for t in e.stream_chat("hi")]

    assert asyncio.run(consume()) == []
    loop.run.assert_awaited_once()
