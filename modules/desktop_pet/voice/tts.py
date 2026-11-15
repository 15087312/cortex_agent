"""桌宠本地 TTS — Piper（开源、离线）→ macOS say → edge-tts 兜底

背景：原 `output_system.tts` 走 edge-tts，微软侧已返回 403，桌宠完全没声音。
这里给桌宠一条独立的本地合成链路，优先级：

1. **Piper**（开源神经 TTS，CPU 实时）：模型放在 `data/models/piper/*.onnx`
   找到就用，进程内缓存 voice 实例
2. **macOS say**：没有 Piper 模型时用系统中文语音，零依赖立刻能出声
3. **edge-tts**：仅作最后兜底（当前服务端 403，可能仍不可用）

合成产物统一写 `OUTPUT_TTS_OUTPUT_DIR`（默认 data/output，已由后端挂到 /audio）。
"""
import shutil
import subprocess
import time
import uuid
import wave
from array import array
from pathlib import Path
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("pet_tts")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PIPER_DIR = _PROJECT_ROOT / "data" / "models" / "piper"

_VOICE_CACHE: dict = {}
_SAY_VOICE: Optional[str] = None
_SAY_VOICE_PROBED = False
# edge-tts 只能靠真实请求验证（当前服务端稳定 403）：记录上一次结果，
# 失败过的就不再在状态里声称 edge 可用，避免"显示有 TTS 其实没声音"
_EDGE_HINT: dict = {"ok": None}
_SAY_HINTS = ("Tingting", "Meijia", "Sinji", "Eddy", "Flo", "Ting-Ting")


def _output_dir() -> Path:
    try:
        from config.settings import settings
        p = Path(getattr(settings, "OUTPUT_TTS_OUTPUT_DIR", "data/output"))
    except Exception:
        p = Path("data/output")
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _new_wav_path() -> Path:
    d = _output_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"pet_tts_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.wav"


def piper_model() -> Optional[Path]:
    """查找可用的 Piper 中文模型（.onnx + 同名 .onnx.json）"""
    try:
        models = sorted(_PIPER_DIR.glob("*.onnx"))
    except OSError:
        return None
    for m in models:
        if Path(str(m) + ".json").is_file():
            # 完整性粗检：huayan-medium 约 63MB，明显过小的当作未下完
            try:
                if m.stat().st_size < 5 * 1024 * 1024:
                    logger.warning(f"Piper 模型疑似未下载完整（{m.stat().st_size} bytes）: {m}")
                    continue
            except OSError:
                continue
            return m
    return None


def _piper_voice():
    model = piper_model()
    if model is None:
        return None
    key = str(model)
    if key in _VOICE_CACHE:
        return _VOICE_CACHE[key]
    try:
        from piper import PiperVoice
        voice = PiperVoice.load(str(model), config_path=str(model) + ".json")
        _VOICE_CACHE[key] = voice
        logger.info(f"Piper 语音已加载: {model.name}")
        return voice
    except Exception as e:
        logger.warning(f"Piper 语音加载失败: {e}")
        return None


def _say_voice() -> Optional[str]:
    """挑一个系统中文语音（避免念英文腔），结果缓存"""
    global _SAY_VOICE, _SAY_VOICE_PROBED
    if _SAY_VOICE_PROBED:
        return _SAY_VOICE
    _SAY_VOICE_PROBED = True
    if not shutil.which("say"):
        return None
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    # 每行形如："Meijia (中文（中国大陆）)   zh_CN    # 你好！我叫 Meijia。"
    names = []
    for line in out.splitlines():
        if "zh_CN" not in line:
            continue
        name = line.split("(", 1)[0].strip()
        if name:
            names.append(name)
    if not names:
        return None
    for hint in _SAY_HINTS:
        for name in names:
            if name.lower().startswith(hint.lower()):
                _SAY_VOICE = name
                return _SAY_VOICE
    _SAY_VOICE = names[0]
    return _SAY_VOICE


def wav_is_audible(path, min_seconds: float = 0.15, min_peak: int = 8) -> bool:
    """音频文件是否真的有声音（而不是空壳/静音）

    只看文件大小会被"44 字节 WAV 头 + padding"骗过；edge-tts 403 时代
    的测试恰恰只断言了"文件存在"，等于没测。这里校验时长 + 峰值。
    """
    try:
        with wave.open(str(path)) as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            width = wf.getsampwidth()
            if frames / rate < min_seconds:
                return False
            raw = wf.readframes(min(frames, rate * 5))
        if width == 1:
            # 8bit WAV 是无符号的，静音是 128 而不是 0（曾经直接 return True，
            # 静音文件会被当成有声，桌宠「说」了一段空气也不算失败）
            return bool(raw) and max(abs(b - 128) for b in raw) >= min_peak
        if width != 2:
            return True   # 非 8/16bit（少见）：时长够就算可播
        samples = array("h")
        samples.frombytes(raw)
        return bool(samples) and max(abs(s) for s in samples) >= min_peak
    except Exception:
        return False


def _synth_piper(text: str) -> Optional[str]:
    voice = _piper_voice()
    if voice is None:
        return None
    path = _new_wav_path()
    try:
        with wave.open(str(path), "wb") as wf:
            voice.synthesize_wav(text, wf)
        if path.stat().st_size > 1024 and wav_is_audible(path):
            return str(path)
    except Exception as e:
        logger.warning(f"Piper 合成失败: {e}")
    try:
        path.unlink()
    except OSError:
        pass
    return None


def _synth_say(text: str) -> Optional[str]:
    name = _say_voice()
    if name is None:
        return None
    path = _new_wav_path()
    try:
        r = subprocess.run(
            ["say", "-v", name, "--data-format=LEI16@22050", "-r", "190", "-o", str(path), text],
            capture_output=True, text=True, timeout=60,
        )
        if (
            r.returncode == 0
            and path.is_file()
            and path.stat().st_size > 1024
            and wav_is_audible(path)
        ):
            return str(path)
        logger.debug(
            f"say 合成失败: rc={r.returncode} size={path.stat().st_size if path.is_file() else 0} "
            f"err={r.stderr[:200]}"
        )
    except Exception as e:
        logger.debug(f"say 合成异常: {e}")
    try:
        path.unlink()
    except OSError:
        pass
    return None


def _synth_edge(text: str) -> Optional[str]:
    try:
        from modules.output_system.tts import TTSEngine
        path = TTSEngine().synthesize_sync(text)
        _EDGE_HINT["ok"] = path is not None
        return path
    except Exception as e:
        _EDGE_HINT["ok"] = False
        logger.debug(f"edge-tts 兜底失败: {e}")
        return None


def active_backend() -> str:
    """当前实际会用的合成后端（给前端显示状态）"""
    if piper_model() is not None and _piper_voice() is not None:
        return "piper"
    if _say_voice():
        return "say"
    try:
        from modules.output_system.tts import TTSEngine
        if TTSEngine().available and _EDGE_HINT["ok"] is not False:
            return "edge"
    except Exception:
        pass
    return "none"


def synthesize(text: str) -> Optional[str]:
    """文本 → 本地音频文件路径（失败返回 None）"""
    text = (text or "").strip()
    if not text:
        return None
    for fn in (_synth_piper, _synth_say, _synth_edge):
        path = fn(text)
        if path:
            return path
    logger.warning("所有 TTS 后端均不可用（Piper 模型缺失 / say 不可用 / edge-tts 失败）")
    return None
