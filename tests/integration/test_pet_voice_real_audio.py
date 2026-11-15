"""桌宠语音真机集成测试 — 真实出声 + 真实识别回环（macOS，需本机语音）

这组测试是 §109 的"防线"：单元测试可以把 `say`/Piper 换成假实现，
但只有真机跑一次，才能确认「桌宠说话」这条链路整条是通的：

1. `tts.synthesize()` 真的产出一段可听见的 WAV（时长 > 0.5s、峰值非 0）
2. 这段 WAV 重采样到 16k 单声道后，Whisper 能转回文字（识别链路可用）

默认 `external` 标记会被 pytest.ini 的 `-m "not external"` 跳过。
手动验证：

    python -m pytest tests/integration/test_pet_voice_real_audio.py -m external -s

环境依赖：macOS + 系统 `say`（本地 TTS 兜底）、`~/.cache/whisper` 有模型。

**缺能力时一律 failed，不用 skip**（§110 教训 7）：这一层存在的意义就是证明
「真机出声/真机能识别」。如果缺后端或缺模型就 skip，`-m external` 会显示一串
skipped 而看起来「跑过了」，等于把防线换成了退出码。要跳过整层，用 marker 不选它。
"""
import re
import sys
import wave
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.external,
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "darwin", reason="依赖 macOS 系统 say 语音"),
]


# 真实 ASR 放到子进程跑：
# - conftest 的 `block_real_native_libs` 会在测试会话里把 torch 置为 None；
#   事后从 sys.modules 里 pop 再 import 会二次注册 TORCH_LIBRARY（triton）直接失败
# - 真 torch + 会话里已加载的 faiss 双 OpenMP 有死锁风险（§27）
# 子进程是干净解释器，既避开两个坑，也验证的正是生产里的那条调用。
_ASR_SCRIPT = r"""
import json, sys, wave
import numpy as np
from modules.desktop_pet.voice.asr import transcribe_pcm

path = sys.argv[1]
with wave.open(path) as wf:
    rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
    raw = wf.readframes(wf.getnframes())
if width != 2:
    print(json.dumps({"error": f"unexpected width {width}"})); raise SystemExit(0)
samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
if channels > 1:
    samples = samples.reshape(-1, channels).mean(axis=1)
if rate != 16000:
    n = int(len(samples) * 16000 / rate)
    samples = np.interp(np.linspace(0, len(samples) - 1, n),
                        np.arange(len(samples)), samples)
pcm = samples.astype(np.int16).tobytes()
print(json.dumps({"text": transcribe_pcm(pcm, sample_rate=16000, language="zh")}))
"""


@pytest.fixture(scope="module")
def voiced_wav(tmp_path_factory):
    """用真实 TTS 链路合成一句话，产物放临时目录（不污染 data/output）"""
    from modules.desktop_pet.voice import tts as pet_tts

    out = tmp_path_factory.mktemp("pet_tts")
    old = pet_tts._output_dir
    pet_tts._output_dir = lambda: out
    try:
        path = pet_tts.synthesize("你好，我是你的桌宠，今天也要开心哦。")
    finally:
        pet_tts._output_dir = old
    assert path, (
        "本机没有可用 TTS 后端，真机出声无法验证"
        f"（active_backend={pet_tts.active_backend()}）；装 Piper 模型或确认系统 say 可用"
    )
    return path


def test_real_tts_produces_audible_speech(voiced_wav):
    from modules.desktop_pet.voice.tts import wav_is_audible

    with wave.open(str(voiced_wav)) as wf:
        seconds = wf.getnframes() / (wf.getframerate() or 1)
    assert 0.5 <= seconds <= 20, f"合成时长异常: {seconds:.2f}s"
    assert wav_is_audible(voiced_wav), "合成结果没有声音（静音/空 WAV）"


def test_real_asr_roundtrip_understands_synthesized_speech(voiced_wav):
    import json
    import subprocess

    from modules.desktop_pet.voice.asr import resolve_whisper_size

    assert resolve_whisper_size(), (
        "本机 ~/.cache/whisper 没有模型，识别链路无法验证；"
        "先跑一次 whisper 下载或换有模型的机器（不要用 skip 冒充通过）"
    )

    proc = subprocess.run(
        [sys.executable, "-c", _ASR_SCRIPT, str(voiced_wav)],
        capture_output=True, text=True, timeout=180, cwd=str(Path.cwd()),
    )
    assert proc.returncode == 0, f"子进程无法加载 Whisper: {proc.stderr[-400:]}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    text = payload.get("text") or ""
    assert text, f"真实音频送进 ASR 后返回空文本（{payload}）"
    # 不逐字比对（口音/模型差异大），但必须证明「认出的是这句话」：
    # 归一化后与原文的字符覆盖率。本机实测 base 模型 0.87（13/15，把「桌宠」
    # 听成「捉蟲」），阈值取 0.6 留出换模型/口音的余量。
    assert _coverage(text, EXPECTED) >= 0.6, f"识别结果偏离太远: {text!r}"


EXPECTED = "你好，我是你的桌宠，今天也要开心哦。"


def _coverage(text: str, expected: str) -> float:
    """归一化后 expected 的字符有多少出现在 text 里（0..1）"""

    def norm(s):
        return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")

    exp, got = norm(expected), norm(text)
    if not exp:
        return 0.0
    return sum(1 for ch in exp if ch in got) / len(exp)


def test_coverage_gate_rejects_unrelated_text():
    """自测：这段断言能真的判错（无关文本必须过不了 0.6 的线）"""
    assert _coverage("今天天气不错，我们出去走走吧", EXPECTED) < 0.6
    assert _coverage("", EXPECTED) == 0.0


def test_mic_listener_can_open_real_device():
    """真机麦克风能不能打开（打不开时 last_error 必须可读，而不是静默哑火）"""
    try:
        import pyaudio  # noqa: F401
    except ImportError:
        pytest.fail("pyaudio 未安装（pip install -r requirements-voice.txt）；真机麦克风无法验证")

    from modules.desktop_pet.voice.mic_listener import BackendMicListener

    listener = BackendMicListener(on_utterance=lambda t: None, calibration_ms=0)
    try:
        started = listener.start()
        assert started, f"真实麦克风打开失败: {listener.last_error}"
        st = listener.status()
        assert st["running"] is True
        assert st["state"] in ("idle", "listening", "transcribing")
    finally:
        listener.stop()


def test_mic_listener_captures_real_audio_and_stops_cleanly():
    """真麦克风采集 1s 真音频：噪声底必须被采样到，stop() 后线程/设备都必须干净退出

    不在断言里要求「必须听到人说话」（CI 里没人对着麦克风讲话），但真采集线程
    跑过一遍能药到两种只能真机复现的缺陷：设备权限/采样率不兼容，以及 §109 的
    stop() 顺序导致的 `Bus error`。
    """
    try:
        import pyaudio  # noqa: F401
    except ImportError:
        pytest.fail("pyaudio 未安装（pip install -r requirements-voice.txt）；真机采集无法验证")

    import time

    from modules.desktop_pet.voice.mic_listener import BackendMicListener

    heard = []
    listener = BackendMicListener(
        on_utterance=lambda t: heard.append(t),
        calibration_ms=700,
    )
    assert listener.start(), f"真实麦克风打开失败: {listener.last_error}"
    try:
        time.sleep(1.2)
        st = listener.status()
        assert st["running"] is True, f"采集线程已死: {listener.last_error}"
        # 校准阶段（700ms）已过，噪声底必须来自真实设备而不是默认值
        assert st["noise_floor"] > 0, f"噪声底未采样到真实音频: {st}"
        assert st["energy_threshold"] >= st["noise_floor"]
    finally:
        listener.stop()

    assert listener.running is False
    assert listener.status()["state"] == "idle", f"停止后状态未归位: {listener.status()}"
    # 听到了就记录一下，不要求一定听到（环境安静时本来就该是空）
    if heard:
        assert all(isinstance(t, str) for t in heard)
