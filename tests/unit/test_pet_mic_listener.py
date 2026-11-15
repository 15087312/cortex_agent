"""后端常驻麦克风监听 — 采集循环 / 能量 VAD / 断句 / 识别线程

原来这一层 0 覆盖，且它决定「桌宠到底听不听得见」。用脚本化音频帧直接驱动
`_capture_loop`，断言真实行为：起音去抖、最短语音、静音断句、超长切段、
噪声底自适应，而不是只断言「start() 返回 True」。
"""
import sys
import threading
import time
import types
from unittest.mock import MagicMock

import numpy as np

from modules.desktop_pet.voice import mic_listener as mic_mod
from modules.desktop_pet.voice.mic_listener import BackendMicListener

QUIET = np.zeros(480, dtype=np.int16).tobytes()          # 30ms @16k，rms=0
LOUD = np.full(480, 5000, dtype=np.int16).tobytes()      # 明显高于 300 阈值


class _Clock:
    """可控时钟：每读一帧推进 30ms，让静音断句/超长切段可确定性地触发"""

    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def tick(self):
        self.t += 0.03


class _ScriptedStream:
    """按脚本返回音频帧；脚本用完时把监听器停掉，避免循环空转"""

    def __init__(self, owner, frames, clock=None):
        self._owner = owner
        self._frames = list(frames)
        self._clock = clock
        self.reads = 0
        self.stopped = False
        self.closed = False

    def read(self, n, exception_on_overflow=False):
        self.reads += 1
        if self._clock is not None:
            self._clock.tick()
        if self._frames:
            return self._frames.pop(0)
        self._owner._running = False
        return b""

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


def _listener(**kw):
    kw.setdefault("calibration_ms", 0)
    kw.setdefault("silence_duration", 0.2)
    return BackendMicListener(on_utterance=kw.pop("on_utterance", lambda t: None), **kw)


def _run(listener, frames, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(mic_mod, "time", clock)
    listener._stream = _ScriptedStream(listener, frames, clock)
    listener._running = True
    listener._capture_loop()


def _queued(listener):
    out = []
    while not listener._asr_queue.empty():
        out.append(listener._asr_queue.get_nowait())
    return out


# ── 采集 / 断句 ──

SILENCE_TAIL = [QUIET] * 8      # 0.2s 断句阈值 → 7 帧（每帧 30ms）足够


def test_utterance_is_queued_after_onset_and_silence(monkeypatch):
    heard = []
    listener = _listener(on_utterance=heard.append)
    _run(listener, [QUIET] + [LOUD] * 11 + SILENCE_TAIL, monkeypatch)

    pcm = _queued(listener)
    assert len(pcm) == 1
    # 前导帧不能丢（第一个字被吃掉是典型 bug）
    assert len(pcm[0]) > 11 * len(LOUD)
    assert listener.state == "idle"


def test_short_noise_burst_is_dropped(monkeypatch):
    listener = _listener()
    _run(listener, [QUIET] + [LOUD] * 5 + SILENCE_TAIL, monkeypatch)
    assert _queued(listener) == []


def test_onset_needs_three_consecutive_frames(monkeypatch):
    starts = []
    listener = _listener(on_speech_start=lambda: starts.append(1))
    # 断续的两帧响 → 不算开口
    _run(
        listener,
        [QUIET, LOUD, QUIET, LOUD, QUIET] + [LOUD] * 11 + SILENCE_TAIL,
        monkeypatch,
    )
    assert starts == [1]


def test_speech_start_callback_error_does_not_kill_capture(monkeypatch):
    def boom():
        raise RuntimeError("TTS 打断失败")

    listener = _listener(on_speech_start=boom)
    _run(listener, [QUIET] + [LOUD] * 11 + SILENCE_TAIL, monkeypatch)
    assert len(_queued(listener)) == 1


def test_max_utterance_forces_cut(monkeypatch):
    listener = _listener(max_utterance_seconds=0.25)
    # 一直有声、永不停顿：只能靠超长切段收尾（0.25s = 9 帧，已越过最短语音 8 帧）
    _run(listener, [QUIET] + [LOUD] * 16, monkeypatch)
    assert len(_queued(listener)) == 1


def test_full_asr_queue_drops_audio_instead_of_blocking(monkeypatch):
    listener = _listener()
    while not listener._asr_queue.full():
        listener._asr_queue.put_nowait(b"x")
    _run(listener, [QUIET] + [LOUD] * 11 + SILENCE_TAIL, monkeypatch)
    assert listener._asr_queue.full()          # 没抛异常，也没卡死采集线程


def test_short_read_is_padded_to_frame(monkeypatch):
    listener = _listener()
    partial = np.full(240, 5000, dtype=np.int16).tobytes()   # 只有半帧
    _run(listener, [QUIET] + [partial] * 11 + SILENCE_TAIL, monkeypatch)
    pcm = _queued(listener)
    assert pcm and len(pcm[0]) % 960 == 0        # 每帧都是完整 30ms


# ── 能量 / 噪声底 ──

def test_threshold_uses_configured_floor_without_noise_floor():
    listener = _listener(energy_threshold=300)
    assert listener._threshold() == 300


def test_threshold_adapts_to_noise_but_is_capped():
    listener = _listener(energy_threshold=300)
    listener._noise_floor = 500
    assert listener._threshold() == 500 * 2.2
    listener._noise_floor = 100000
    assert listener._threshold() == 8000        # 上限防「开局有人说话」把阈值锁死


def test_noise_floor_follows_quiet_frames(monkeypatch):
    listener = _listener()
    hum = np.full(480, 200, dtype=np.int16).tobytes()    # 低于阈值但非绝对静音
    _run(listener, [QUIET] * 2 + [hum], monkeypatch)
    assert listener._noise_floor > 1.0
    assert listener._noise_floor < 400          # EMA 缓慢跟踪，不会一帧跳到 400


def test_rms_of_silence_and_noise():
    listener = _listener()
    assert listener._rms(QUIET) == 0.0
    assert listener._rms(b"") == 0.0
    assert 4990 < listener._rms(LOUD) < 5010


def test_capture_loop_exits_when_stream_missing():
    listener = _listener()
    listener._running = True
    listener._stream = None
    listener._capture_loop()                    # 不抛异常、不空转
    assert listener._running is True


def test_read_error_is_retried_not_fatal():
    class FlakyStream:
        def __init__(self, owner):
            self.owner = owner
            self.calls = 0

        def read(self, n, exception_on_overflow=False):
            self.calls += 1
            if self.calls == 1:
                raise OSError("device unplugged")
            self.owner._running = False
            return b""

    listener = _listener()
    listener._stream = FlakyStream(listener)
    listener._running = True
    listener._capture_loop()
    assert listener._stream.calls == 2


# ── 生命周期 ──

def test_start_without_pyaudio_reports_reason(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyaudio", None)
    listener = _listener()
    assert listener.start() is False
    assert "pyaudio" in listener.last_error
    assert listener.running is False


def _fake_pyaudio(open_error=None):
    stream = MagicMock(name="stream")
    pa = MagicMock(name="PyAudio-instance")
    if open_error:
        pa.open.side_effect = open_error
    else:
        pa.open.return_value = stream
    mod = types.ModuleType("pyaudio")
    mod.paInt16 = 8
    mod.PyAudio = lambda: pa
    return mod, pa, stream


def test_start_opens_stream_and_spawns_worker_threads(monkeypatch):
    mod, pa, stream = _fake_pyaudio()
    monkeypatch.setitem(sys.modules, "pyaudio", mod)
    started = []

    class _NoopThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.target, self.name, self.daemon = target, name, daemon
            started.append(name)

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(mic_mod.threading, "Thread", _NoopThread)
    listener = _listener()
    assert listener.start() is True
    assert listener.running is True
    assert listener.state == "idle"
    assert listener.last_error == ""
    assert started == ["pet-mic", "pet-mic-asr"]
    kwargs = pa.open.call_args.kwargs
    assert kwargs["channels"] == 1 and kwargs["rate"] == 16000 and kwargs["input"] is True

    listener.stop()
    assert listener.running is False
    stream.close.assert_called_once()
    pa.terminate.assert_called_once()


def test_start_failure_terminates_pyaudio(monkeypatch):
    mod, pa, _ = _fake_pyaudio(open_error=OSError("no default input device"))
    monkeypatch.setitem(sys.modules, "pyaudio", mod)
    listener = _listener()
    assert listener.start() is False
    assert "麦克风打开失败" in listener.last_error
    pa.terminate.assert_called_once()


def test_start_is_idempotent(monkeypatch):
    mod, pa, _ = _fake_pyaudio()
    monkeypatch.setitem(sys.modules, "pyaudio", mod)
    listener = _listener()
    listener._running = True
    assert listener.start() is True
    pa.open.assert_not_called()


def test_stop_without_start_is_noop():
    listener = _listener()
    listener.stop()
    assert listener.running is False


def test_stop_releases_stuck_stream(monkeypatch):
    """线程卡在 read() 时只能靠 stop_stream 打断，且不能关设备（会踩已释放对象）"""
    listener = _listener()
    listener._running = True
    listener._stream = MagicMock()
    alive = {"n": 0}

    class _StuckThread:
        def is_alive(self):
            alive["n"] += 1
            return alive["n"] <= 3          # join 两次仍在 read() 里 → 走打断+跳过 close

        def join(self, timeout=None):
            pass

    stream = listener._stream
    listener._thread = _StuckThread()
    listener._pa = MagicMock()
    listener.stop()

    stream.stop_stream.assert_called_once()
    stream.close.assert_not_called()               # 卡死时绝不 close
    assert listener._stream is None and listener._pa is None
    assert listener.state == "idle"


def test_status_reports_calibration_and_energy(monkeypatch):
    listener = _listener(energy_threshold=250)
    listener._calib_rms = [10.0, 20.0]
    status = listener.status()
    assert status["calibrating"] is True
    assert status["energy_threshold"] == 250
    assert status["heard_anything"] is False
    assert status["running"] is False


def test_constructor_falls_back_when_settings_unavailable(monkeypatch):
    """配置读取失败时用默认值启动，而不是让桌宠整个起不来"""
    monkeypatch.setitem(sys.modules, "config.settings", None)
    listener = BackendMicListener(on_utterance=lambda t: None)
    assert listener._silence_duration == 1.5
    assert listener._max_utterance == 60


# ── 识别线程 ──

def _asr_listener():
    listener = _listener()
    listener._running = True
    return listener


def test_asr_loop_transcribes_and_reports(monkeypatch):
    heard = []
    listener = _asr_listener()
    listener._on_utterance = heard.append
    monkeypatch.setattr(
        "modules.desktop_pet.voice.asr.transcribe_pcm", lambda pcm, **kw: "把灯关掉"
    )

    listener._asr_queue.put_nowait(b"pcm")
    listener._asr_queue.put_nowait(b"")          # 空串 = 退出信号
    listener._asr_loop()

    assert heard == ["把灯关掉"]
    assert listener._heard_anything is True
    assert listener.state == "idle"


def test_asr_loop_swallows_backend_errors(monkeypatch):
    heard = []
    listener = _asr_listener()
    listener._on_utterance = heard.append

    def boom(pcm, **kw):
        raise RuntimeError("whisper down")

    monkeypatch.setattr("modules.desktop_pet.voice.asr.transcribe_pcm", boom)
    listener._asr_queue.put_nowait(b"pcm")
    listener._asr_queue.put_nowait(b"")
    listener._asr_loop()
    assert heard == []
    assert listener.state == "idle"


def test_asr_loop_survives_callback_error(monkeypatch):
    listener = _asr_listener()

    def boom(text):
        raise RuntimeError("engine gone")

    listener._on_utterance = boom
    monkeypatch.setattr(
        "modules.desktop_pet.voice.asr.transcribe_pcm", lambda pcm, **kw: "嗯"
    )
    listener._asr_queue.put_nowait(b"pcm")
    listener._asr_queue.put_nowait(b"")
    listener._asr_loop()
    assert listener._heard_anything is True


def test_asr_loop_drains_queue_before_exit(monkeypatch):
    """_running=False 后不能立刻丢队列里剩下的音频"""
    listener = _asr_listener()
    calls = []
    monkeypatch.setattr(
        "modules.desktop_pet.voice.asr.transcribe_pcm",
        lambda pcm, **kw: calls.append(pcm) or None,
    )
    listener._asr_queue.put_nowait(b"leftover")
    thread = threading.Thread(target=listener._asr_loop, daemon=True)
    thread.start()
    time.sleep(0.05)
    listener._running = False
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert calls == [b"leftover"]
