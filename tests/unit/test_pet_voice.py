"""桌宠语音链路测试：<say> 发声标记、冷却唤醒、麦克风 VAD、TTS 选择"""
import asyncio
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.desktop_pet.pet_engine import PetEngine
from modules.desktop_pet.react_loop import CustomLoop
from modules.desktop_pet.speech import clean_for_tts, has_say, split_speech


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    old = PetEngine._instance
    PetEngine._instance = None
    yield
    PetEngine._instance = old


@pytest.fixture(autouse=True)
def _pet_db(tmp_path, monkeypatch):
    """隔离桌宠独立存储（配置/历史）"""
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_voice_test.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    yield store
    store_mod.PetStore._instance = None


# ── <say> 解析 ──

def test_split_speech_only_tagged_part_is_spoken():
    visible, spoken = split_speech("（有点困）<say>我要睡啦，晚安</say>")
    assert spoken == "我要睡啦，晚安"
    assert "say" not in visible
    assert visible == "（有点困）我要睡啦，晚安"


def test_split_speech_multiple_segments_and_no_tags():
    visible, spoken = split_speech("<say>第一句</say> 中间想想 <say>第二句</say>")
    assert spoken == "第一句 第二句"
    assert visible == "第一句 中间想想 第二句"

    visible2, spoken2 = split_speech("纯内心独白")
    assert spoken2 == ""
    assert visible2 == "纯内心独白"
    assert has_say("纯内心独白") is False


def test_clean_for_tts_strips_markup_and_code():
    out = clean_for_tts("**你好** `code` https://x.test/abc\n\n第二行")
    assert "*" not in out and "http" not in out and "`" not in out
    assert "你好 code" in out and "第二行" in out


# ── 退避：用户连续无回复次数（不再按空拍） ──

class _EmptyThenTextClient:
    """按预设序列返回内容；用完后返回空串"""

    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def chat_stream(self, messages=None, max_tokens=None, on_token=None, **kw):
        content = self.outputs.pop(0) if self.outputs else ""
        if on_token and content:
            for ch in content:
                on_token(ch)
        return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))


def _custom_engine(monkeypatch, outputs):
    import modules.desktop_pet.store as store_mod
    store_mod.PetStore.get_instance().update_config({
        "loop_type": "custom", "min_think_interval": 0, "max_loop_rounds": 1,
    })
    e = PetEngine()
    e._client = _EmptyThenTextClient(outputs)
    e._client_cfg = ("", "", "", "", "")
    monkeypatch.setattr(PetEngine, "_capture_screen_vision", AsyncMock(return_value=""))
    return e


@pytest.mark.asyncio
async def test_no_reply_streak_counts_user_turns_only(monkeypatch, _pet_db):
    # _react_cycle 内部最多重试 10 步，每轮都要给够空输出才会整轮无产出
    e = _custom_engine(monkeypatch, [""] * 20 + ["回答"])
    loop = CustomLoop()

    assert await loop.run(e, "第一问") == ""
    assert loop._no_reply_streak == 1          # 用户说话没被回应 → +1

    assert await loop.run(e, "") == ""          # 自己没有产出：不计入
    assert loop._no_reply_streak == 1

    assert await loop.run(e, "第二问") == "回答"
    assert loop._no_reply_streak == 0          # 回复了 → 复位


def test_backoff_multiplier_uses_no_reply_streak():
    assert CustomLoop._idle_backoff_mult(0) == 1
    assert CustomLoop._idle_backoff_mult(1) == 2
    assert CustomLoop._idle_backoff_mult(2) == 4


# ── 冷却唤醒：转写累积合并 ──

@pytest.mark.asyncio
async def test_voice_utterances_merge_and_wake_once(monkeypatch, _pet_db):
    import modules.desktop_pet.store as store_mod
    store_mod.PetStore.get_instance().update_config({"min_think_interval": 0.05})

    e = PetEngine()
    e._running = True
    e._loop_async = asyncio.get_running_loop()

    chats, after = [], []

    async def fake_chat(text):
        chats.append(text)
        return "好呀"

    async def fake_after(reply, user_turn=True):
        after.append((reply, user_turn))

    monkeypatch.setattr(e, "chat", fake_chat)
    monkeypatch.setattr(e, "_after_reply", fake_after)

    e.submit_utterance("第一句")
    e.submit_utterance("第二句")
    await asyncio.sleep(0.03)
    assert chats == []                       # 冷却未到：不调模型
    await asyncio.sleep(0.15)
    assert chats == ["第一句\n第二句"]        # 合并成一条用户消息唤醒一次
    assert after == [("好呀", True)]


@pytest.mark.asyncio
async def test_new_utterance_restarts_cooldown(monkeypatch, _pet_db):
    import modules.desktop_pet.store as store_mod
    store_mod.PetStore.get_instance().update_config({"min_think_interval": 0.12})

    e = PetEngine()
    e._running = True
    e._loop_async = asyncio.get_running_loop()
    chats = []

    async def fake_chat(text):
        chats.append(text)
        return ""

    monkeypatch.setattr(e, "chat", fake_chat)

    e.submit_utterance("第一句")
    await asyncio.sleep(0.08)      # 还没到 0.12
    e.submit_utterance("第二句")    # 重新计时
    await asyncio.sleep(0.06)
    assert chats == []             # 若没重新计时，这里已经唤醒了
    await asyncio.sleep(0.15)
    assert chats == ["第一句\n第二句"]


@pytest.mark.asyncio
async def test_voice_status_endpoint_does_not_create_engine(_pet_db):
    """高频轮询接口不能造出没有事件总线的引擎单例"""
    from modules.desktop_pet import api as pet_api

    resp = await pet_api.pet_voice_status()
    assert resp["data"]["running"] is False
    assert resp["data"]["tts_backend"]
    assert PetEngine._instance is None


@pytest.mark.asyncio
async def test_voice_status_reports_cooldown(monkeypatch, _pet_db):
    import modules.desktop_pet.store as store_mod
    store_mod.PetStore.get_instance().update_config({"min_think_interval": 7})
    e = PetEngine()
    st = e.voice_status()
    assert st["cooldown"] == 7
    assert st["running"] is False
    assert st["pending"] == 0


# ── 发声：只有 <say> 被朗读 ──

def _speech_engine(monkeypatch):
    e = PetEngine()
    e.last_reply = None
    spoken, played = [], []

    from modules.desktop_pet.voice import tts as pet_tts
    monkeypatch.setattr(
        pet_tts, "synthesize",
        lambda t: (spoken.append(t), "/tmp/tts.wav")[1],
    )
    e._player = SimpleNamespace(
        play_async=lambda p: played.append(p),
        stop=lambda: None,
        playing=False,
    )
    monkeypatch.setattr(PetEngine, "emit_reply", AsyncMock())
    return e, spoken, played


@pytest.mark.asyncio
async def test_after_reply_speaks_only_say_content(monkeypatch, _pet_db):
    e, spoken, played = _speech_engine(monkeypatch)
    await e._after_reply("（观察：他在打字）<say>歇一会儿吧</say>", user_turn=False)
    assert spoken == ["歇一会儿吧"]
    assert played == ["/tmp/tts.wav"]
    assert "say" not in e.last_reply["text"]
    assert "观察" in e.last_reply["text"]


@pytest.mark.asyncio
async def test_after_reply_user_turn_without_tags_is_spoken(monkeypatch, _pet_db):
    e, spoken, _ = _speech_engine(monkeypatch)
    await e._after_reply("好呀，我在。", user_turn=True)
    assert spoken == ["好呀，我在。"]


@pytest.mark.asyncio
async def test_after_reply_self_thought_without_tags_is_silent(monkeypatch, _pet_db):
    e, spoken, played = _speech_engine(monkeypatch)
    await e._after_reply("（他今天好像很累）", user_turn=False)
    assert spoken == []
    assert played == []


# ── 麦克风监听（假 pyaudio + 假 ASR） ──

def _frame(amplitude: int, samples: int = 480) -> bytes:
    import struct
    return struct.pack("<" + "h" * samples, *([amplitude] * samples))


class _FakeStream:
    def __init__(self, frames):
        self._frames = list(frames)

    def read(self, n, exception_on_overflow=False):
        if self._frames:
            return self._frames.pop(0)
        return _frame(0)

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePyAudio:
    paInt16 = 8

    def __init__(self, frames):
        self._frames = frames

    def open(self, **kwargs):
        return _FakeStream(self._frames)

    def terminate(self):
        pass


def test_mic_listener_detects_utterance(monkeypatch):
    from modules.desktop_pet.voice import mic_listener as ml_mod

    # 静音 → 说话（响，够 ONSET_FRAMES + MIN_VOICED_FRAMES）→ 静音
    frames = [_frame(10)] * 5 + [_frame(3000)] * 12 + [_frame(10)] * 40
    monkeypatch.setitem(sys.modules, "pyaudio", SimpleNamespace(
        paInt16=8, PyAudio=lambda: _FakePyAudio(frames),
    ))

    got, starts = [], []

    monkeypatch.setattr(
        "modules.desktop_pet.voice.asr.transcribe_pcm",
        lambda pcm, **kw: "你好呀" if pcm else None,
    )

    listener = ml_mod.BackendMicListener(
        on_utterance=lambda t: got.append(t),
        on_speech_start=lambda: starts.append(time.time()),
        energy_threshold=500,
        silence_duration=0.15,
        calibration_ms=0,   # 校准时序单独控制，测试里跳过启动采样
    )
    try:
        assert listener.start() is True
        deadline = time.time() + 3
        while not got and time.time() < deadline:
            time.sleep(0.02)
    finally:
        listener.stop()

    assert starts, "检测到人声时应触发打断回调"
    assert got and got[0] == "你好呀"


def test_mic_listener_ignores_steady_ambient_noise(monkeypatch):
    """回归（§109）：固定阈值 300 低于真实环境底噪（本机中位 ~375）→ 一直"正在听"

    自适应噪声底（校准 + 非语音帧跟踪）必须让恒定的环境噪声判为 idle。
    """
    from modules.desktop_pet.voice import mic_listener as ml_mod

    class _SteadyStream(_FakeStream):
        """持续 375 RMS 的环境噪声（假流不限速，不能靠预生成帧数凑时长）"""

        def read(self, n, exception_on_overflow=False):
            return _frame(375)

    class _SteadyPyAudio(_FakePyAudio):
        def open(self, **kwargs):
            return _SteadyStream([])

    monkeypatch.setitem(sys.modules, "pyaudio", SimpleNamespace(
        paInt16=8, PyAudio=lambda: _SteadyPyAudio([]),
    ))
    got = []
    listener = ml_mod.BackendMicListener(
        on_utterance=lambda t: got.append(t),
        energy_threshold=300,
        silence_duration=0.1,
        calibration_ms=0,
    )
    try:
        assert listener.start() is True
        time.sleep(0.4)
        st = listener.status()
    finally:
        listener.stop()

    assert got == []
    assert st["state"] == "idle"
    assert st["noise_floor"] > 300
    assert st["energy_threshold"] > 375


def test_mic_listener_stop_waits_for_capture_thread(monkeypatch):
    """回归（§109）：先关流/terminate 会让仍在 read() 的线程踩野指针（实测 Bus error）"""
    from modules.desktop_pet.voice import mic_listener as ml_mod

    class _RecordingPyAudio(_FakePyAudio):
        def __init__(self, frames):
            super().__init__(frames)
            self.alive_at_terminate = None

        def terminate(self):
            import threading
            self.alive_at_terminate = any(
                t.name == "pet-mic" and t.is_alive() for t in threading.enumerate()
            )

    frames = [_frame(10)] * 5000
    pa = _RecordingPyAudio(frames)
    monkeypatch.setitem(sys.modules, "pyaudio", SimpleNamespace(
        paInt16=8, PyAudio=lambda: pa,
    ))
    monkeypatch.setattr("modules.desktop_pet.voice.asr.transcribe_pcm", lambda pcm, **kw: None)

    listener = ml_mod.BackendMicListener(on_utterance=lambda t: None, calibration_ms=0)
    assert listener.start() is True
    time.sleep(0.1)
    listener.stop()

    assert pa.alive_at_terminate is False, "采集线程还在 read() 时就 terminate 了设备"
    assert listener.running is False


def test_pet_window_does_not_grab_microphone():
    """桌宠透明窗只展示后端监听状态：QWebEngine 里拿麦克风必然被拒（§109）"""
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "frontend" / "pet" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "getUserMedia" not in html
    assert "new WebSocket" not in html
    assert "/stream/pet/voice/status" in html


def test_mic_listener_reports_error_without_pyaudio(monkeypatch):
    from modules.desktop_pet.voice import mic_listener as ml_mod

    monkeypatch.setitem(sys.modules, "pyaudio", None)  # import 会抛 ImportError
    listener = ml_mod.BackendMicListener(on_utterance=lambda t: None)
    assert listener.start() is False
    assert "pyaudio" in listener.last_error
