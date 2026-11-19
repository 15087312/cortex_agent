"""桌宠本地 TTS 链路测试 — Piper → say → edge-tts，且必须"真的有声音"

为什么单独一组（§109）：
- `tests/unit/test_tts.py` 把 edge-tts 的 `Communicate.save()` 换成写 12 字节假文件，
  断言"合成成功"。真实 edge-tts 服务端返回 403 时该测试仍然全绿，
  桌宠却完全没有声音。
- 这里不 mock 产物：假后端写真实 WAV（带样本），再用 `wav_is_audible`
  校验时长/峰值；静音、只有 WAV 头、0 帧都必须判失败并落到下一个后端。
"""
import sys
import types
import wave
from array import array
from pathlib import Path

import pytest

from modules.desktop_pet.voice import tts as tts_mod

# 未打补丁的输出目录实现（fixture 会替换 tts_mod._output_dir 做隔离）
_REAL_OUTPUT_DIR = tts_mod._output_dir


def write_wav(path, seconds=0.4, rate=22050, amplitude=3000, channels=1):
    """写一段可听见（或静音）的真实 WAV"""
    frames = int(rate * seconds)
    samples = [amplitude if i % 2 else -amplitude for i in range(frames)] if amplitude else [0] * frames
    data = array("h", samples * channels)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.tobytes())
    return path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离输出目录 / Piper 模型目录 / 模块级语音缓存"""
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(tts_mod, "_PIPER_DIR", tmp_path / "piper")
    monkeypatch.setattr(tts_mod, "_output_dir", lambda: out)
    monkeypatch.setattr(tts_mod, "_VOICE_CACHE", {})
    monkeypatch.setattr(tts_mod, "_SAY_VOICE", None)
    monkeypatch.setattr(tts_mod, "_SAY_VOICE_PROBED", False)
    # 默认：没有 say、没有 edge（各用例按需打开）
    monkeypatch.setattr(tts_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(tts_mod, "_synth_edge", lambda text: None)
    return out


# ── 模型发现 ──

def test_piper_model_requires_config_file():
    model_dir = tts_mod._PIPER_DIR
    model_dir.mkdir(parents=True)
    onnx = model_dir / "zh_CN-huayan-medium.onnx"
    onnx.write_bytes(b"\0" * (6 * 1024 * 1024))
    assert tts_mod.piper_model() is None          # 缺 .onnx.json
    Path(str(onnx) + ".json").write_text("{}", encoding="utf-8")
    assert tts_mod.piper_model() == onnx


def test_piper_model_rejects_incomplete_download():
    """下到一半的模型（远端限速很常见）不能拿去加载"""
    model_dir = tts_mod._PIPER_DIR
    model_dir.mkdir(parents=True)
    onnx = model_dir / "zh_CN-huayan-medium.onnx"
    onnx.write_bytes(b"\0" * 4096)
    Path(str(onnx) + ".json").write_text("{}", encoding="utf-8")
    assert tts_mod.piper_model() is None


# ── 产物校验（本轮测试真正要守住的东西） ──

def test_wav_is_audible_accepts_real_speech_like_file(tmp_path):
    assert tts_mod.wav_is_audible(write_wav(tmp_path / "ok.wav")) is True


def test_wav_is_audible_rejects_silence_and_short_clips(tmp_path):
    assert tts_mod.wav_is_audible(write_wav(tmp_path / "silent.wav", amplitude=0)) is False
    assert tts_mod.wav_is_audible(write_wav(tmp_path / "short.wav", seconds=0.02)) is False
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not a wav")
    assert tts_mod.wav_is_audible(broken) is False


def test_wav_is_audible_rejects_silent_8bit(tmp_path):
    """8bit WAV 的静音是 128 而不是 0（曾经 width != 2 直接判可播）"""
    path = tmp_path / "silent8.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(22050)
        wf.writeframes(bytes([128]) * 22050)
    assert tts_mod.wav_is_audible(path) is False

    loud = tmp_path / "loud8.wav"
    with wave.open(str(loud), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(22050)
        wf.writeframes(bytes([128, 200] * 11025))
    assert tts_mod.wav_is_audible(loud) is True


# ── 合成链路 ──

def _fake_piper(monkeypatch, *, amplitude=3000, seconds=0.4, fail=False):
    class FakeVoice:
        def synthesize_wav(self, text, wf):
            if fail:
                raise RuntimeError("piper crash")
            frames = int(22050 * seconds)
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(array("h", [amplitude, -amplitude] * (frames // 2)).tobytes())

    mod = types.ModuleType("piper")
    mod.PiperVoice = types.SimpleNamespace(load=lambda *a, **k: FakeVoice())
    monkeypatch.setitem(sys.modules, "piper", mod)

    model_dir = tts_mod._PIPER_DIR
    model_dir.mkdir(parents=True, exist_ok=True)
    onnx = model_dir / "zh_CN-huayan-medium.onnx"
    onnx.write_bytes(b"\0" * (6 * 1024 * 1024))
    Path(str(onnx) + ".json").write_text("{}", encoding="utf-8")
    return onnx


def test_synthesize_prefers_piper(monkeypatch, _isolate):
    _fake_piper(monkeypatch)
    path = tts_mod.synthesize("你好呀")
    assert path is not None
    assert Path(path).parent == _isolate
    assert tts_mod.wav_is_audible(path)
    assert tts_mod.active_backend() == "piper"


def test_piper_silent_output_falls_through(monkeypatch, _isolate):
    """Piper 写出时长够但全静音的文件 → 不能当成成功"""
    _fake_piper(monkeypatch, amplitude=0)
    called = []

    def fake_say(text):
        called.append(text)
        return write_wav(_isolate / "say.wav")

    monkeypatch.setattr(tts_mod, "_synth_say", fake_say)
    path = tts_mod.synthesize("你好呀")
    assert called == ["你好呀"]        # 落到 say
    assert path and Path(path).name == "say.wav"


def test_piper_crash_falls_back(monkeypatch, _isolate):
    _fake_piper(monkeypatch, fail=True)
    monkeypatch.setattr(tts_mod, "_synth_say", lambda t: write_wav(_isolate / "say.wav"))
    path = tts_mod.synthesize("你好")
    assert path and Path(path).name == "say.wav"
    # 失败的半成品不能留在输出目录（否则会被前端当成可用音频）
    assert [p.name for p in _isolate.iterdir()] == ["say.wav"]


def test_say_rejects_header_only_and_silent_output(monkeypatch, _isolate):
    """say 返回 0 或静音文件都不能算成功（原来只查文件大小 > 1024）"""
    monkeypatch.setattr(tts_mod, "_say_voice", lambda: "Tingting")

    def fake_run(cmd, **kw):
        target = Path(cmd[cmd.index("-o") + 1])
        write_wav(target, seconds=0.4, amplitude=0)   # 静音但体积足够
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tts_mod.subprocess, "run", fake_run)
    assert tts_mod._synth_say("你好") is None
    assert list(_isolate.iterdir()) == []          # 静音文件被清掉


def test_say_success_returns_audible_wav(monkeypatch, _isolate):
    monkeypatch.setattr(tts_mod.shutil, "which", lambda name: "/usr/bin/say")
    monkeypatch.setattr(tts_mod, "_say_voice", lambda: "Tingting")

    def fake_run(cmd, **kw):
        target = Path(cmd[cmd.index("-o") + 1])
        write_wav(target)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tts_mod.subprocess, "run", fake_run)
    path = tts_mod._synth_say("你好")
    assert path and tts_mod.wav_is_audible(path)


def test_edge_is_last_and_all_failure_returns_none(monkeypatch, _isolate):
    monkeypatch.setattr(tts_mod, "_synth_say", lambda t: None)
    order = []

    def fake_edge(text):
        order.append(text)
        return None

    monkeypatch.setattr(tts_mod, "_synth_edge", fake_edge)
    assert tts_mod.synthesize("你好") is None
    assert order == ["你好"]                       # 三个后端都试过
    assert tts_mod.synthesize("   ") is None       # 空文本不调后端


def test_active_backend_reports_say_when_piper_missing(monkeypatch, _isolate):
    monkeypatch.setattr(tts_mod, "_say_voice", lambda: "Tingting")
    assert tts_mod.active_backend() == "say"


def test_output_dir_is_the_one_mounted_at_audio():
    """合成产物必须落到后端挂 /audio 的目录，否则前端 <audio> 永远 404

    `api/main.py` 用 `modules.output_system.tts.get_tts_output_dir()` 做静态挂载，
    桌宠合成走自己的 `_output_dir()`（相对路径按项目根解析）。两者指向不同就会
    「文件合成成功但没声音」——静默失败，状态里还显示可用。
    """
    from modules.output_system.tts import get_tts_output_dir

    pet_dir = _REAL_OUTPUT_DIR().resolve()
    mounted = Path(get_tts_output_dir()).resolve()
    assert pet_dir == mounted


def test_active_backend_stops_claiming_edge_after_real_failure(monkeypatch, _isolate):
    """edge-tts 服务端 403：验证过一次失败后状态不能再声称 edge 可用"""
    from modules.output_system.tts import TTSEngine

    monkeypatch.setattr(tts_mod, "_say_voice", lambda: None)
    monkeypatch.setattr(tts_mod, "_EDGE_HINT", {"ok": None})
    # edge-tts 是可选依赖（requirements 里不装，CI 机器必然没有）。
    # 这里显式打开「依赖可用」这个前提，测试只验证失败后的状态切换，
    # 否则会因为环境没装 edge-tts 而退化成 'none'（与被测逻辑无关）。
    monkeypatch.setattr(TTSEngine, "available", property(lambda self: True))
    assert tts_mod.active_backend() == "edge"      # 未尝试前：import 成功即视为兜底
    tts_mod._EDGE_HINT["ok"] = False               # 真实请求已失败
    assert tts_mod.active_backend() == "none"
