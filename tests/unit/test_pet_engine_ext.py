"""PetEngine 补充测试 — 语音触发/对话/流式/TTS/播放边界分支"""
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


def _engine(monkeypatch):
    monkeypatch.setattr(pe.settings, "DESKTOP_PET_ENABLED", True)
    monkeypatch.setattr(pe.settings, "DESKTOP_PET_SESSION_ID", "pet_main")
    e = PetEngine.__new__(PetEngine)
    e._client = None
    e._event_bus = None
    e._sub_id = ""
    e._running = False
    e._reply_listeners = []
    e.last_reply = None
    e._build_context = AsyncMock(return_value="")
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
    e._ensure_pet_session = lambda: None
    e.start()
    assert e._sub_id == "sub-1"
    assert bus.subscribe.called
    e.stop()
    assert e._sub_id == ""
    assert bus.unsubscribe.called


def test_start_ensure_session_exception(monkeypatch):
    e = _engine(monkeypatch)
    e._ensure_pet_session = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    e.start()  # 不抛异常
    assert e._running is True
    e.stop()


def test_start_disabled(monkeypatch):
    e = _engine(monkeypatch)
    monkeypatch.setattr(pe.settings, "DESKTOP_PET_ENABLED", False)
    e.start()
    assert e._running is False


def test_start_without_event_bus_warns(monkeypatch):
    e = _engine(monkeypatch)
    e._ensure_pet_session = lambda: None
    e._event_bus = None
    e.start()
    assert e._running is True
    e.stop()


def test_stop_not_running(monkeypatch):
    e = _engine(monkeypatch)
    e.stop()  # _running False → 直接返回
    assert e._running is False


def test_stop_unsubscribe_error(monkeypatch):
    e = _engine(monkeypatch)
    bus = MagicMock()
    bus.unsubscribe.side_effect = RuntimeError("boom")
    e._event_bus = bus
    e._sub_id = "sub"
    e._running = True
    e.stop()  # 不抛异常
    assert e._sub_id == ""


def test_ensure_pet_session_happy(monkeypatch):
    e = _engine(monkeypatch)
    repo = MagicMock()
    import modules.database.session_repo as sr_mod
    monkeypatch.setattr(sr_mod, "get_session_repo", lambda: repo)
    e._ensure_pet_session()
    repo.create_session.assert_called_once_with("pet_main")
    repo.set_session_title.assert_called_once_with("pet_main", "桌宠")


def test_ensure_pet_session_exception(monkeypatch):
    e = _engine(monkeypatch)
    import modules.database.session_repo as sr_mod
    monkeypatch.setattr(
        sr_mod, "get_session_repo",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    e._ensure_pet_session()  # 不抛异常


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
    asyncio.run(e._on_speech(_speech_event("你好")))  # 不抛异常


# ── _build_context ────────────────────────────────────────────────────────

class _FakePerception:
    def __init__(self, frag):
        self._frag = frag

    async def collect(self):
        return self._frag


def _ctx_engine(monkeypatch):
    e = _engine(monkeypatch)
    e._build_context = PetEngine._build_context.__get__(e)
    return e


def test_build_context_all_sources(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    import modules.memory.event_retrieval as er_mod
    e = _ctx_engine(monkeypatch)
    frag = MagicMock()
    frag.content = "感知环境信息"
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(frag))
    retr = MagicMock()
    ev = SimpleNamespace(time="2026-01-01 10:00", fact="旧任务")
    retr.retrieve = AsyncMock(return_value=[ev])
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retr)
    out = asyncio.run(e._build_context("q"))
    assert "当前时间" in out and "感知环境信息" in out and "旧任务" in out


def test_build_context_empty_perception_and_events(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    import modules.memory.event_retrieval as er_mod
    e = _ctx_engine(monkeypatch)
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(None))
    retr = MagicMock()
    retr.retrieve = AsyncMock(return_value=[])
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retr)
    out = asyncio.run(e._build_context("q"))
    assert "感知环境信息" not in out
    assert "相关过往记忆" not in out


def test_build_context_perception_raises(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    import modules.memory.event_retrieval as er_mod
    e = _ctx_engine(monkeypatch)
    retr = MagicMock()
    retr.retrieve = AsyncMock(return_value=[])
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retr)
    monkeypatch.setattr(
        ps_mod, "PerceptionSource",
        lambda: (_ for _ in ()).throw(RuntimeError("no camera")),
    )
    out = asyncio.run(e._build_context("q"))
    assert "当前时间" in out  # 其他块不受影响


def test_build_context_retrieval_raises(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    import modules.memory.event_retrieval as er_mod
    e = _ctx_engine(monkeypatch)
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(None))
    retr = MagicMock()
    retr.retrieve = AsyncMock(side_effect=RuntimeError("mem down"))
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retr)
    out = asyncio.run(e._build_context("q"))
    assert "相关过往记忆" not in out


def test_build_context_datetime_raises(monkeypatch):
    import importlib
    import modules.memory.event_retrieval as er_mod
    cs = importlib.import_module("config.settings")
    e = _ctx_engine(monkeypatch)
    retr = MagicMock()
    retr.retrieve = AsyncMock(return_value=[])
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retr)

    class _RaisesAttr:
        def __get__(self, obj, objtype=None):
            raise RuntimeError("clock broken")

    class _BadSettings:
        USER_NAME = _RaisesAttr()

    # datetime 是 C 类型不可 patch；让 _cfg.USER_NAME 访问抛异常 → 触发 except
    monkeypatch.setattr(cs, "settings", _BadSettings())
    out = asyncio.run(e._build_context("q"))
    assert "对话对象" not in out
    assert "当前时间" in out  # 时间行先执行成功，USER_NAME 行抛异常


def test_build_context_event_unknown_date(monkeypatch):
    import modules.thinking.context.sources.perception_source as ps_mod
    import modules.memory.event_retrieval as er_mod
    e = _ctx_engine(monkeypatch)
    monkeypatch.setattr(ps_mod, "PerceptionSource", lambda: _FakePerception(None))
    retr = MagicMock()
    ev = SimpleNamespace(time="", fact="x")
    retr.retrieve = AsyncMock(return_value=[ev])
    monkeypatch.setattr(er_mod, "get_event_retrieval", lambda: retr)
    out = asyncio.run(e._build_context("q"))
    assert "未知日期" in out


# ── _build_messages ───────────────────────────────────────────────────────

def test_build_messages_creates_client(monkeypatch):
    e = _engine(monkeypatch)
    import infra.model.large_model_client as lmc
    fake_client = MagicMock()
    monkeypatch.setattr(lmc, "LargeModelClient", lambda: fake_client)
    msgs = e._build_messages("hi", extra_system="补充语境")  # 覆盖 extra_system 真分支
    assert e._client is fake_client
    assert msgs[-1].content == "hi"
    assert "补充语境" in msgs[0].content


def test_build_messages_skips_non_dialog_roles(monkeypatch):
    e = _engine(monkeypatch)
    e._client = MagicMock()
    import modules.database.session_repo as sr_mod
    repo = MagicMock()
    repo.get_recent_messages.return_value = [
        {"role": "user", "content": "ok"},
        {"role": "system", "content": "跳过"},
        {"role": "tool", "content": "跳过"},
        {"role": "assistant", "content": ""},  # 空内容跳过
    ]
    monkeypatch.setattr(sr_mod, "get_session_repo", lambda: repo)
    msgs = e._build_messages("hi")
    roles = [m.role for m in msgs]
    assert roles == ["system", "user", "user"]


def test_build_messages_repo_exception(monkeypatch):
    e = _engine(monkeypatch)
    e._client = MagicMock()
    import modules.database.session_repo as sr_mod
    monkeypatch.setattr(
        sr_mod, "get_session_repo",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    msgs = e._build_messages("hi")  # 历史读取失败 → 空历史
    assert msgs[0].role == "system"
    assert msgs[-1].content == "hi"


def test_build_messages_no_extra_system(monkeypatch):
    e = _engine(monkeypatch)
    e._client = MagicMock()
    import modules.database.session_repo as sr_mod
    repo = MagicMock()
    repo.get_recent_messages.return_value = []
    monkeypatch.setattr(sr_mod, "get_session_repo", lambda: repo)
    msgs = e._build_messages("hi", extra_system="")
    assert msgs[0].role == "system"
    assert "你是桌面上的 AI 桌宠助手" in msgs[0].content
    assert "补充" not in msgs[0].content


# ── _save_pair ────────────────────────────────────────────────────────────

def test_save_pair(monkeypatch):
    e = _engine(monkeypatch)
    repo = MagicMock()
    import modules.database.session_repo as sr_mod
    monkeypatch.setattr(sr_mod, "get_session_repo", lambda: repo)
    e._save_pair("问", "答")
    assert repo.save_message.call_count == 2
    calls = [c.args for c in repo.save_message.call_args_list]
    assert ("pet_main", "user", "问") in calls
    assert ("pet_main", "assistant", "答") in calls


def test_save_pair_exception(monkeypatch):
    e = _engine(monkeypatch)
    import modules.database.session_repo as sr_mod
    monkeypatch.setattr(
        sr_mod, "get_session_repo",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    e._save_pair("问", "答")  # 不抛异常


# ── chat ──────────────────────────────────────────────────────────────────

def test_chat_frontend_unreachable(monkeypatch):
    e = _engine(monkeypatch)
    import modules.thinking.frontend_channel as fc
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda *a, **k: False)
    e._client = AsyncMock()
    assert asyncio.run(e.chat("hi")) == ""
    e._client.chat.assert_not_awaited()


def test_chat_success(monkeypatch):
    fc = _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    resp = SimpleNamespace(message=SimpleNamespace(content="  回复内容  "))
    client = AsyncMock()
    client.chat = AsyncMock(return_value=resp)
    e._client = client
    e._build_messages = MagicMock(return_value=["msg"])
    e._save_pair = MagicMock()
    reply = asyncio.run(e.chat("你好"))
    assert reply == "回复内容"
    client.chat.assert_awaited_once()
    e._save_pair.assert_called_once_with("你好", "回复内容")
    fc.push_content.assert_not_awaited()


def test_chat_empty_reply(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    client = AsyncMock()
    client.chat = AsyncMock(return_value=SimpleNamespace(message=SimpleNamespace(content="")))
    e._client = client
    e._build_messages = MagicMock(return_value=["msg"])
    e._save_pair = MagicMock()
    assert asyncio.run(e.chat("hi")) == ""
    e._save_pair.assert_not_called()


def test_chat_null_response(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    client = AsyncMock()
    client.chat = AsyncMock(return_value=None)
    e._client = client
    e._build_messages = MagicMock(return_value=["msg"])
    assert asyncio.run(e.chat("hi")) == ""


def test_chat_exception(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    e._build_context = AsyncMock(side_effect=RuntimeError("llm down"))
    assert asyncio.run(e.chat("hi")) == ""


# ── stream_chat ───────────────────────────────────────────────────────────

def test_stream_chat_success(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)

    async def fake_chat_stream(messages, on_token):
        on_token("你")
        on_token("好")
        return SimpleNamespace(message=SimpleNamespace(content="你好"))

    client = MagicMock()
    client.chat_stream = fake_chat_stream
    e._client = client
    e._build_messages = MagicMock(return_value=["msg"])
    e._save_pair = MagicMock()

    async def consume():
        return [t async for t in e.stream_chat("你好")]

    tokens = asyncio.run(consume())
    assert tokens == ["你", "好"]
    e._save_pair.assert_called_once_with("你好", "你好")
    assert e.last_reply["text"] == "你好"


def test_stream_chat_frontend_unreachable(monkeypatch):
    e = _engine(monkeypatch)
    import modules.thinking.frontend_channel as fc
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda *a, **k: False)
    client = MagicMock()
    e._client = client
    e._build_messages = MagicMock(return_value=["msg"])

    async def consume():
        return [t async for t in e.stream_chat("hi")]

    assert asyncio.run(consume()) == []
    client.chat_stream.assert_not_called()


def test_stream_chat_task_error_yields_partial(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)

    async def boom(text, on_token, collected, extra_system):
        collected.append("部分内容")
        raise RuntimeError("boom")

    monkeypatch.setattr(e, "_chat_stream_task", boom)
    e._build_messages = MagicMock(return_value=["msg"])

    async def consume():
        return [t async for t in e.stream_chat("hi")]

    tokens = asyncio.run(consume())
    assert tokens == ["部分内容"]


def test_stream_chat_task_error_no_collected(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)

    async def boom(text, on_token, collected, extra_system):
        raise RuntimeError("boom")

    monkeypatch.setattr(e, "_chat_stream_task", boom)
    e._build_messages = MagicMock(return_value=["msg"])

    async def consume():
        return [t async for t in e.stream_chat("hi")]

    assert asyncio.run(consume()) == []


def test_stream_chat_on_token_queue_error(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)

    async def fake_chat_stream(messages, on_token):
        on_token("t")  # put_nowait 抛异常 → on_token 吞掉
        return SimpleNamespace(message=SimpleNamespace(content="t"))

    client = MagicMock()
    client.chat_stream = fake_chat_stream
    e._client = client
    e._build_messages = MagicMock(return_value=["msg"])
    e._save_pair = MagicMock()
    monkeypatch.setattr(asyncio.Queue, "put_nowait", lambda self, t: (_ for _ in ()).throw(RuntimeError("queue full")))

    async def consume():
        return [t async for t in e.stream_chat("hi")]

    assert asyncio.run(consume()) == []
    e._save_pair.assert_called_once_with("hi", "t")


# ── _chat_stream_task ─────────────────────────────────────────────────────

def test_chat_stream_task_success(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    e._client = MagicMock()
    e._client.chat_stream = AsyncMock(return_value=SimpleNamespace(
        message=SimpleNamespace(content="流式回复"),
    ))
    e._build_messages = MagicMock(return_value=["msg"])
    e._save_pair = MagicMock()
    collected = []
    on_token = MagicMock()

    async def run():
        await e._chat_stream_task("hi", on_token, collected, "extra")

    asyncio.run(run())
    assert collected == ["流式回复"]
    e._save_pair.assert_called_once_with("hi", "流式回复")
    assert e.last_reply["text"] == "流式回复"


def test_chat_stream_task_empty_reply(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    e._client = MagicMock()
    e._client.chat_stream = AsyncMock(return_value=SimpleNamespace(
        message=SimpleNamespace(content=""),
    ))
    e._build_messages = MagicMock(return_value=["msg"])
    e._save_pair = MagicMock()
    collected = []

    async def run():
        await e._chat_stream_task("hi", MagicMock(), collected, "")

    asyncio.run(run())
    assert collected == [""]
    e._save_pair.assert_not_called()
    assert e.last_reply is None


def test_chat_stream_task_exception(monkeypatch):
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    e._client = MagicMock()
    e._client.chat_stream = AsyncMock(side_effect=RuntimeError("boom"))
    e._build_messages = MagicMock(return_value=["msg"])
    collected = []

    async def run():
        await e._chat_stream_task("hi", MagicMock(), collected, "")

    asyncio.run(run())  # 不抛异常
    assert collected == []


# ── _after_reply ──────────────────────────────────────────────────────────

def test_after_reply_tts_plays(monkeypatch):
    import modules.output_system.tts as tts_mod
    fc = _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    engine = MagicMock()
    engine.enabled = True
    engine.synthesize_sync.return_value = "/tmp/pet.wav"
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    played = []
    monkeypatch.setattr(pe, "_play_audio", lambda p: played.append(p))
    asyncio.run(e._after_reply("回复"))
    assert played == ["/tmp/pet.wav"]
    assert e.last_reply["text"] == "回复"
    fc.push_content.assert_awaited_once()


def test_after_reply_tts_no_path(monkeypatch):
    import modules.output_system.tts as tts_mod
    fc = _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    engine = MagicMock()
    engine.enabled = True
    engine.synthesize_sync.return_value = None
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    monkeypatch.setattr(pe, "_play_audio", lambda p: pytest.fail("不应播放"))
    asyncio.run(e._after_reply("回复"))
    assert e.last_reply["text"] == "回复"
    fc.push_content.assert_awaited_once()


def test_after_reply_tts_exception(monkeypatch):
    import modules.output_system.tts as tts_mod
    fc = _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: (_ for _ in ()).throw(RuntimeError("tts down")))
    asyncio.run(e._after_reply("回复"))
    assert e.last_reply["text"] == "回复"
    fc.push_content.assert_awaited_once()


def test_after_reply_push_exception(monkeypatch):
    import modules.output_system.tts as tts_mod
    import modules.thinking.frontend_channel as fc
    e = _engine(monkeypatch)
    engine = MagicMock()
    engine.enabled = True
    engine.synthesize_sync.return_value = None
    monkeypatch.setattr(tts_mod, "TTSEngine", lambda: engine)
    monkeypatch.setattr(fc, "push_content", AsyncMock(side_effect=RuntimeError("push down")))
    monkeypatch.setattr(pe, "_play_audio", lambda p: None)
    asyncio.run(e._after_reply("回复"))  # 不抛异常
    assert e.last_reply["text"] == "回复"


# ── is_active ─────────────────────────────────────────────────────────────

def test_is_active(monkeypatch):
    e = _engine(monkeypatch)
    assert e.is_active() is False
    e._running = True
    assert e.is_active() is True


# ── _play_audio ───────────────────────────────────────────────────────────

def test_play_audio_no_path(monkeypatch):
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    pe._play_audio("")  # 空路径
    pe._play_audio("/nope.mp3")  # 不存在


def test_play_audio_posix(monkeypatch):
    import os
    import subprocess
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "name", "posix")
    run = MagicMock()
    monkeypatch.setattr(subprocess, "run", run)
    pe._play_audio("/tmp/a.wav")
    run.assert_called_once()
    assert run.call_args[0][0] == ["afplay", "/tmp/a.wav"]


def test_play_audio_windows(monkeypatch):
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "name", "nt")
    startfile = MagicMock()
    monkeypatch.setattr(os, "startfile", startfile, raising=False)
    pe._play_audio("C:/a.wav")
    startfile.assert_called_once_with("C:/a.wav")


def test_play_audio_exception(monkeypatch):
    import os
    import subprocess
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("afplay", 60)),
    )
    pe._play_audio("/tmp/a.wav")  # 不抛异常
