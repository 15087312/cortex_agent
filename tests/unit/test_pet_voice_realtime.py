"""全双工语音链路单元测试 — VAD / 轮次状态机 / 流式 ASR / 实时管线 / ASR 后端

这一层原来几乎没有测试（`PERCEPTION_VOICE_REALTIME=true` 才启用），
出事时的表现是「桌宠听不见」或「说完一句永远不进入思考」，且没有任何断言能挡住。

测试原则（§109）：不 mock 被测对象的产物。这里用真实 PCM 数据 + 假模型/假后端，
校验的是「状态机真的走到下一步」「音频真的被拼成合法 WAV」「ws 真的收到消息」。
"""
import json
import os
import importlib
import sys
import types
import wave
import time
from io import BytesIO
from unittest.mock import MagicMock

import numpy as np
import pytest

from modules.desktop_pet.voice import asr as asr_mod
from modules.desktop_pet.voice import pipeline as pipeline_mod
from modules.desktop_pet.voice import streaming_asr as streaming_mod
from modules.desktop_pet.voice.pipeline import RealtimeVoicePipeline
from modules.desktop_pet.voice.streaming_asr import StreamingASR
from modules.desktop_pet.voice.turn_manager import TurnManager, VoiceState
from modules.desktop_pet.voice.vad import SileroVAD

PCM_CHUNK = np.zeros(512, dtype=np.int16).tobytes()


def _fake_torch(confidence: float = 0.9, raise_on_call: bool = False):
    """最小 torch 替身：只提供 VAD 用到的 hub.load / from_numpy"""
    mod = types.ModuleType("torch")

    class _Tensor:
        def __init__(self, arr):
            self.arr = arr

        def item(self):
            return confidence

    class _Model:
        def __call__(self, tensor, sample_rate):
            if raise_on_call:
                raise RuntimeError("inference boom")
            return _Tensor(tensor)

    mod.hub = types.SimpleNamespace(
        load=lambda **kw: (_Model(), types.SimpleNamespace(read_audio=None))
    )
    mod.from_numpy = _Tensor
    return mod


# ── SileroVAD ──

def test_vad_without_torch_is_silent_and_does_not_retry(monkeypatch):
    """CI 上没有 torch：必须安静地判「无语音」，且不反复尝试加载"""
    monkeypatch.setitem(sys.modules, "torch", None)
    vad = SileroVAD(threshold=0.5)
    assert vad.is_speech(PCM_CHUNK) is False
    assert vad._load_error                       # 记下失败原因，避免每次重试
    assert vad.is_speech(PCM_CHUNK) is False


def test_vad_maps_confidence_against_threshold(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(confidence=0.9))
    vad = SileroVAD(threshold=0.5)
    assert vad.is_speech(PCM_CHUNK) is True
    vad.threshold = 0.95
    assert vad.is_speech(PCM_CHUNK) is False


def test_vad_inference_error_degrades_to_no_speech(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(raise_on_call=True))
    vad = SileroVAD()
    assert vad.is_speech(PCM_CHUNK) is False


def test_vad_loaded_without_model_reports_no_speech():
    vad = SileroVAD()
    vad._loaded = True
    vad._model = None
    assert vad.is_speech(PCM_CHUNK) is False


def test_vad_threshold_is_clamped():
    vad = SileroVAD()
    vad.threshold = 2.0
    assert vad.threshold == 1.0
    vad.threshold = -1.0
    assert vad.threshold == 0.0


# ── TurnManager ──

def test_turn_manager_walks_the_round_trip():
    states = []
    tm = TurnManager(on_state_change=states.append)

    tm.on_speech_start()
    tm.on_thinking_start()
    tm.on_thinking_end()
    tm.on_speaking_end()

    assert [s.value for s in states] == ["listening", "thinking", "speaking", "idle"]
    assert tm.state is VoiceState.IDLE


def test_turn_manager_speech_during_tts_interrupts_once():
    interrupts = []
    tm = TurnManager(on_interrupt=lambda: interrupts.append(1))
    tm.on_speech_start()
    tm.on_thinking_start()
    tm.on_thinking_end()
    assert tm.state is VoiceState.SPEAKING

    tm.on_speech_start()
    assert interrupts == [1]
    assert tm.state is VoiceState.LISTENING


def test_turn_manager_interrupt_without_callback_still_switches_state():
    tm = TurnManager()
    tm.on_speech_start()
    tm.on_thinking_start()
    tm.on_thinking_end()
    tm.on_speech_start()
    assert tm.state is VoiceState.LISTENING


def test_turn_manager_ignores_repeated_states_and_broken_callbacks():
    calls = []

    def boom(state):
        calls.append(state)
        raise RuntimeError("frontend gone")

    tm = TurnManager(on_state_change=boom)
    tm.on_speech_start()
    tm.on_speech_start()            # 同状态不重复回调
    assert calls == [VoiceState.LISTENING]


def test_turn_manager_silence_only_counts_while_listening():
    tm = TurnManager(silence_duration=1.0)
    assert tm.check_silence() is False        # IDLE 下不算「说完」

    tm.on_speech_start()
    tm._last_speech_time = time.time() - 0.2
    assert tm.check_silence() is False        # 还没到静音阈值
    tm._last_speech_time = time.time() - 1.2
    assert tm.check_silence() is True


def test_turn_manager_reset_clears_timers():
    tm = TurnManager()
    tm.on_speech_start()
    tm.on_thinking_start()
    tm.reset()
    assert tm.state is VoiceState.IDLE
    assert tm._speech_start_time == 0.0 and tm._last_speech_time == 0.0


# ── StreamingASR ──

def test_streaming_asr_ignores_audio_before_start():
    asr = StreamingASR()
    asr.feed(PCM_CHUNK)
    assert asr._get_buffered() == b""


def test_streaming_asr_emits_partial_after_interval(monkeypatch):
    partials = []
    asr = StreamingASR(on_partial=partials.append)
    monkeypatch.setattr(asr, "_transcribe_chunk", lambda data: "前半句" if data else None)
    asr.start()
    monkeypatch.setattr(asr, "_last_partial_time", 0.0)   # 距上次 partial 已超时
    asr.feed(PCM_CHUNK)
    assert partials == ["前半句"]


def test_streaming_asr_finalize_returns_text_and_notifies(monkeypatch):
    finals = []
    asr = StreamingASR(on_final=finals.append)
    monkeypatch.setattr(asr, "_transcribe_chunk", lambda data: "你好呀")
    asr.start()
    asr.feed(PCM_CHUNK)
    assert asr.finalize() == "你好呀"
    assert finals == ["你好呀"]


def test_streaming_asr_finalize_silence_returns_none():
    asr = StreamingASR(on_final=lambda t: pytest.fail("静音不该回调"))
    asr.start()
    assert asr.finalize() is None


def test_streaming_asr_pcm_to_wav_is_a_real_16k_mono_wav():
    pcm = np.arange(3200, dtype=np.int16).tobytes()
    wav = StreamingASR._pcm_to_wav(pcm, 16000)
    with wave.open(BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.readframes(wf.getnframes()) == pcm


def test_streaming_asr_transcribe_failure_is_swallowed(monkeypatch):
    asr = StreamingASR()
    monkeypatch.setattr(
        streaming_mod.StreamingASR, "_pcm_to_wav",
        staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad pcm"))),
    )
    assert asr._transcribe_chunk(b"\x00\x00") is None


def test_streaming_asr_stop_and_reset_clear_state(monkeypatch):
    asr = StreamingASR()
    asr.start()
    asr.feed(PCM_CHUNK)
    assert asr._get_buffered() == PCM_CHUNK

    asr.reset()
    assert asr._get_buffered() == b""

    asr.stop()
    asr.feed(PCM_CHUNK)
    assert asr._running is False
    assert asr._get_buffered() == b""


def test_streaming_asr_backend_failure_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("whisper exploded")

    monkeypatch.setattr("modules.desktop_pet.voice.asr.transcribe_pcm", boom)
    asr = StreamingASR()
    wav = StreamingASR._pcm_to_wav(PCM_CHUNK)
    assert asr._transcribe_wav(wav) is None
    assert asr._transcribe_chunk(PCM_CHUNK) is None


def test_streaming_asr_wav_uses_cached_asr(monkeypatch):
    """转写走 asr.transcribe_pcm（带模型缓存），不是每次重新 load_model"""
    seen = {}

    def fake_transcribe(pcm, sample_rate=16000, language=None):
        seen["sr"], seen["lang"], seen["pcm"] = sample_rate, language, pcm
        return "嗯"

    monkeypatch.setattr("modules.desktop_pet.voice.asr.transcribe_pcm", fake_transcribe)
    asr = StreamingASR(sample_rate=8000, language="en")
    pcm = np.zeros(160, dtype=np.int16).tobytes()
    assert asr._transcribe_wav(StreamingASR._pcm_to_wav(pcm, 8000)) == "嗯"
    assert seen == {"sr": 8000, "lang": "en", "pcm": pcm}


# ── RealtimeVoicePipeline ──

class _FakeASR:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.reset_count = 0
        self.finalized = 0
        self.fed = []
        self.on_final = None

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def reset(self):
        self.reset_count += 1

    def feed(self, chunk):
        self.fed.append(chunk)

    def finalize(self):
        self.finalized += 1
        if self.on_final:
            self.on_final("我说话啦")
        return "我说话啦"


@pytest.fixture
def pipeline(monkeypatch):
    """管线 + 可控 VAD/ASR + 假事件总线，ws_send 收集所有下行消息"""
    bus = MagicMock()
    monkeypatch.setattr(
        "modules.perception.events.bus.get_event_bus", lambda: bus
    )

    sent = []
    p = RealtimeVoicePipeline(silence_duration=0.0)
    p._asr = _FakeASR()
    p._asr.on_final = p._on_final
    p._vad = types.SimpleNamespace(is_speech=lambda chunk: bool(chunk), threshold=0.5)
    p.start(ws_send=sent.append)
    p.done = bus
    p.sent = sent
    yield p
    p.stop()


def test_pipeline_start_wires_asr_and_bus(pipeline):
    assert pipeline._running is True
    assert pipeline._asr.started is True


def test_pipeline_speech_then_silence_runs_one_turn(pipeline):
    pipeline.feed_audio(b"voice")
    assert pipeline.state is VoiceState.LISTENING
    assert pipeline._asr.fed == [b"voice"]

    pipeline.feed_audio(b"")        # VAD 判无语音 → 静音超阈值 → finalize
    assert pipeline._asr.finalized == 1
    assert pipeline.state is VoiceState.THINKING

    payloads = [m for m in pipeline.sent if "state" in m]
    assert [m["state"] for m in payloads] == ["listening", "thinking"]
    assert {"final_text": "我说话啦"} in pipeline.sent


def test_pipeline_final_publishes_speech_detected(pipeline):
    pipeline._on_final("打开音乐")
    event = pipeline.done.publish.call_args[0][0]
    assert event.source == "realtime_voice"
    assert event.payload["text"] == "打开音乐"
    assert event.payload["language"] == "zh"


def test_pipeline_broken_ws_does_not_break_the_turn(pipeline):
    pipeline._ws_send = MagicMock(side_effect=RuntimeError("socket closed"))
    pipeline._on_partial("半句")
    pipeline._on_state_change(VoiceState.LISTENING)
    pipeline._on_interrupt()
    pipeline.notify_tts_ready("http://x/y.mp3")
    assert pipeline.state is VoiceState.IDLE


def test_pipeline_ignores_audio_when_stopped(pipeline):
    pipeline.stop()
    pipeline.feed_audio(b"voice")
    assert pipeline._asr.fed == []
    assert pipeline._asr.stopped is True
    assert pipeline.state is VoiceState.IDLE


def test_pipeline_tts_ready_and_finished(pipeline):
    pipeline.notify_tts_ready("http://x/a.mp3")
    assert {"tts_url": "http://x/a.mp3"} in pipeline.sent
    pipeline._turn_manager.on_thinking_end()
    pipeline.notify_tts_finished()
    assert pipeline.state is VoiceState.IDLE


# ── VoiceWebSocketHandler ──

class _FakeWS:
    def __init__(self, messages, *, disconnect_exc=None):
        self._messages = list(messages)
        self._disconnect_exc = disconnect_exc
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if self._disconnect_exc:
            raise self._disconnect_exc
        if not self._messages:
            return {"type": "websocket.disconnect"}
        item = self._messages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def send_text(self, text):
        self.sent.append(text)


class _FakePipeline:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started_with = "unset"
        self.stopped = False
        self.fed = []
        self._asr = types.SimpleNamespace(reset=MagicMock())
        self._turn_manager = types.SimpleNamespace(reset=MagicMock())
        _FakePipeline.instances.append(self)

    def start(self, ws_send=None):
        self.started_with = ws_send

    def stop(self):
        self.stopped = True

    def feed_audio(self, chunk):
        self.fed.append(chunk)


@pytest.fixture
def fake_handler_pipeline(monkeypatch):
    _FakePipeline.instances = []
    monkeypatch.setattr(pipeline_mod, "RealtimeVoicePipeline", _FakePipeline)
    return _FakePipeline


def _handler(messages, **kw):
    from modules.desktop_pet.voice.ws_handler import VoiceWebSocketHandler

    ws = _FakeWS(messages, **kw)
    return VoiceWebSocketHandler(ws), ws


@pytest.mark.asyncio
async def test_ws_handler_feeds_bytes_and_stops_on_disconnect(fake_handler_pipeline):
    handler, ws = _handler([{"type": "websocket.receive", "bytes": b"pcm"}])
    await handler.handle()

    assert ws.accepted is True
    p = fake_handler_pipeline.instances[0]
    assert p.fed == [b"pcm"]
    assert callable(p.started_with)          # 下行推送已接上
    assert p.stopped is True and handler._running is False


@pytest.mark.asyncio
async def test_ws_handler_reset_command_clears_buffers(fake_handler_pipeline):
    handler, ws = _handler([
        {"type": "websocket.receive", "text": json.dumps({"command": "reset"})},
    ])
    await handler.handle()
    p = fake_handler_pipeline.instances[0]
    p._asr.reset.assert_called_once()
    p._turn_manager.reset.assert_called_once()
    assert p.stopped is True


@pytest.mark.asyncio
async def test_ws_handler_stop_command_ends_session(fake_handler_pipeline):
    handler, ws = _handler([
        {"type": "websocket.receive", "text": json.dumps({"command": "stop"})},
        {"type": "websocket.receive", "bytes": b"ignored"},
    ])
    await handler.handle()
    assert fake_handler_pipeline.instances[0].fed == []   # stop 之后的音频不再处理
    assert fake_handler_pipeline.instances[0].stopped is True


@pytest.mark.asyncio
async def test_ws_handler_survives_bad_json_and_unknown_commands(fake_handler_pipeline):
    handler, ws = _handler([
        {"type": "websocket.receive", "text": "{not json"},
        {"type": "websocket.receive", "text": json.dumps({"command": "nope"})},
        {"type": "websocket.receive", "text": json.dumps("not-a-dict")},
        {"type": "websocket.receive", "bytes": b"after"},
    ])
    await handler.handle()
    assert fake_handler_pipeline.instances[0].fed == [b"after"]


@pytest.mark.asyncio
async def test_ws_handler_breaks_on_disconnect_and_receive_errors(fake_handler_pipeline):
    from fastapi import WebSocketDisconnect

    handler, _ = _handler([], disconnect_exc=WebSocketDisconnect())
    await handler.handle()
    assert fake_handler_pipeline.instances[0].stopped is True

    handler2, _ = _handler([RuntimeError("transport reset")])
    await handler2.handle()
    assert fake_handler_pipeline.instances[1].stopped is True


@pytest.mark.asyncio
async def test_ws_handler_startup_failure_is_logged_not_raised(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("VAD 模型下载失败")

    monkeypatch.setattr(pipeline_mod, "RealtimeVoicePipeline", boom)
    handler, ws = _handler([])
    await handler.handle()                 # 不能把异常抛给 FastAPI（否则连接直接 500）
    assert handler._running is False
    assert handler._pipeline is None


def test_pipeline_without_ws_send_or_bus():
    """没有前端连接、没有事件总线时，回调必须全部安全空转"""
    p = RealtimeVoicePipeline()
    p.start()
    p._on_final("孤立的一句话")
    p._on_partial("x")
    p._on_interrupt()
    assert p.state is VoiceState.THINKING


# ── ASR 模型选择 / 后端 ──

@pytest.fixture
def whisper_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_mod, "_whisper_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(asr_mod, "_MODEL_CACHE", {})
    return tmp_path


def _touch_models(root, *sizes):
    for size in sizes:
        (root / f"{size}.pt").write_bytes(b"0")


def test_resolve_whisper_size_prefers_configured(whisper_dir):
    _touch_models(whisper_dir, "tiny", "base")
    assert asr_mod.resolve_whisper_size("base") == "base"


def test_resolve_whisper_size_falls_back_to_cached(whisper_dir):
    _touch_models(whisper_dir, "tiny")
    assert asr_mod.resolve_whisper_size("large-v3") == "tiny"
    assert asr_mod.resolve_whisper_size(None) == "tiny"


def test_resolve_whisper_size_none_when_cache_empty(whisper_dir):
    assert asr_mod.resolve_whisper_size("base") is None


def test_resolve_whisper_size_accepts_any_cached_model_as_last_resort(whisper_dir):
    _touch_models(whisper_dir, "small")
    assert asr_mod.resolve_whisper_size("large-v3") == "small"


def test_default_whisper_cache_dir_is_under_home():
    assert asr_mod._whisper_cache_dir().endswith(os.path.join(".cache", "whisper"))


def test_resolve_whisper_size_survives_missing_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_mod, "_whisper_cache_dir", lambda: str(tmp_path / "nope"))
    assert asr_mod._cached_model_sizes() == []


def test_load_whisper_caches_model(whisper_dir, monkeypatch):
    _touch_models(whisper_dir, "base")
    loads = []

    def fake_load_model(size):
        loads.append(size)
        return MagicMock(name=f"model-{size}")

    monkeypatch.setitem(sys.modules, "whisper", types.SimpleNamespace(load_model=fake_load_model))
    first = asr_mod.load_whisper("base")
    second = asr_mod.load_whisper("base")
    assert first is second
    assert loads == ["base"]


def test_load_whisper_returns_none_without_models(whisper_dir):
    assert asr_mod.load_whisper("base") is None


def test_load_whisper_returns_none_when_load_fails(whisper_dir, monkeypatch):
    _touch_models(whisper_dir, "base")

    def boom(size):
        raise RuntimeError("cuda oom")

    monkeypatch.setitem(sys.modules, "whisper", types.SimpleNamespace(load_model=boom))
    assert asr_mod.load_whisper("base") is None


def test_transcribe_pcm_rejects_empty_audio():
    assert asr_mod.transcribe_pcm(b"") is None


def test_transcribe_pcm_local_writes_and_removes_temp_wav(whisper_dir, monkeypatch):
    _touch_models(whisper_dir, "base")
    captured = {}

    class FakeModel:
        def transcribe(self, path, language=None):
            captured["path"] = path
            captured["lang"] = language
            with wave.open(path, "rb") as wf:
                captured["frames"] = wf.getnframes()
            return {"text": "  你好  "}

    monkeypatch.setattr(asr_mod, "load_whisper", lambda size: FakeModel())
    pcm = np.zeros(1600, dtype=np.int16).tobytes()
    assert asr_mod.transcribe_pcm(pcm, language="en") == "你好"
    assert captured["frames"] == 1600
    assert captured["lang"] == "en"
    # 临时 wav 必须清理掉，否则 /tmp 会随每次说话堆积
    assert not os.path.exists(captured["path"])


def test_transcribe_pcm_tolerates_temp_file_already_gone(whisper_dir, monkeypatch):
    """临时文件被系统清理掉时，unlink 的 OSError 不能把转写结果吞掉"""
    _touch_models(whisper_dir, "base")
    monkeypatch.setattr(asr_mod, "load_whisper", lambda size: MagicMock(
        transcribe=lambda path, language=None: {"text": "还行"}
    ))
    monkeypatch.setattr(
        asr_mod, "os",
        types.SimpleNamespace(unlink=MagicMock(side_effect=OSError("gone"))),
    )
    assert asr_mod.transcribe_pcm(b"\x00\x00") == "还行"


def test_transcribe_pcm_local_without_model_returns_none(monkeypatch, whisper_dir):
    monkeypatch.setattr(asr_mod, "load_whisper", lambda size: None)
    assert asr_mod.transcribe_pcm(b"\x00\x00") is None


def _fake_settings(**kw):
    base = {
        "PERCEPTION_VOICE_BACKEND": "local",
        "PERCEPTION_VOICE_LANGUAGE": "zh",
        "PERCEPTION_VOICE_MODEL": "base",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_transcribe_pcm_api_backend_strips_text(monkeypatch):
    monkeypatch.setattr(
        importlib.import_module("config.settings"), "settings",
        _fake_settings(PERCEPTION_VOICE_BACKEND="api"),
    )
    monkeypatch.setattr(
        "infra.data_process.core.speech_recognizer.transcribe_with_api",
        lambda wav, lang, sr: "  接口结果\n",
    )
    assert asr_mod.transcribe_pcm(b"\x00\x00") == "接口结果"


def test_transcribe_pcm_backend_exception_returns_none(monkeypatch):
    monkeypatch.setattr(importlib.import_module("config.settings"), "settings", _fake_settings())
    monkeypatch.setattr(
        asr_mod, "load_whisper",
        lambda size: (_ for _ in ()).throw(RuntimeError("no torch")),
    )
    assert asr_mod.transcribe_pcm(b"\x00\x00") is None


def test_pipeline_survives_missing_event_bus(monkeypatch):
    def boom():
        raise RuntimeError("bus not ready")

    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", boom)
    p = RealtimeVoicePipeline()
    p.start()
    assert p._event_bus is None
    p._on_final("没总线也得能说话")
    assert p.state is VoiceState.THINKING


def test_pipeline_continuous_speech_keeps_single_round(pipeline):
    pipeline.feed_audio(b"a")
    pipeline.feed_audio(b"b")
    # 第二块语音不能重复触发 on_speech_start（否则会重置刚累积的 ASR 缓冲）
    assert pipeline._asr.reset_count == 1
    assert pipeline._asr.fed == [b"a", b"b"]


def test_pipeline_final_swallows_ws_and_publish_errors(pipeline):
    pipeline._ws_send = MagicMock(side_effect=RuntimeError("socket closed"))
    pipeline.done.publish.side_effect = RuntimeError("bus down")
    pipeline._on_final("这句话不能丢")
    assert pipeline.state is VoiceState.THINKING


def test_audio_url_is_json_serialisable(pipeline):
    """前端契约：下行消息必须是可 JSON 序列化的 dict（不是 dataclass/枚举）"""
    pipeline.notify_tts_ready("http://x/a.mp3")
    for msg in pipeline.sent:
        assert isinstance(json.loads(json.dumps(msg)), dict)
