# -*- coding: utf-8 -*-
"""系统前置自检（doctor）— 打包解决不了的外部条件逐项体检

检测项与修复建议（失败项附 fix 命令/操作指引，前端"系统体检"卡片渲染）：
  - ffmpeg/ffprobe：视频识别硬前置（打包版 > 配置 > PATH > 常见目录，见 video_analyzer）
  - 原生语音依赖：whisper（STT）/ pyaudio（采集）/ pynput（热键）— 可选，缺失即降级
  - 音频解码（SpeechRecognition 需 flac 二进制）
  - 密钥：LLM 主模型（OPENAI/LARGE_MODEL）与视觉（VISION_*）
  - Whisper 权重缓存：~/.cache/whisper 是否已下载过模型
  - 本地 Embedding 模型目录

设计：每个检查项独立 try/except，永不因单项异常拖垮整体；
返回结构 {ok, severity, detail, fix}，severity ∈ required|optional。
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from utils.logger import setup_logger

logger = setup_logger("doctor")


def _check_ffmpeg() -> Dict[str, Any]:
    from infra.data_process.core.video_analyzer import find_ffmpeg_tool

    ffmpeg = find_ffmpeg_tool("ffmpeg")
    ffprobe = find_ffmpeg_tool("ffprobe")
    if ffmpeg and ffprobe:
        ver = ""
        try:
            out = subprocess.run(
                [ffmpeg, "-version"], capture_output=True, text=True, timeout=10
            )
            ver = (out.stdout.splitlines() or [""])[0][:80]
        except Exception:
            pass
        return {"ok": True, "severity": "required", "detail": ver or ffmpeg}
    return {
        "ok": False,
        "severity": "required",
        "detail": "未找到 ffmpeg/ffprobe（视频识别不可用）",
        "fix": "macOS: brew install ffmpeg｜Ubuntu: sudo apt install ffmpeg｜Windows: winget install Gyan.FFmpeg；或设置 VIDEO_FFMPEG_PATH 指向 ffmpeg 所在目录",
    }


def _check_import(import_name: str, pkg_name: str, capability: str) -> Dict[str, Any]:
    try:
        mod = __import__(import_name)
        ver = getattr(mod, "__version__", "")
        return {"ok": True, "severity": "optional", "detail": f"{capability}: {import_name} {ver}".strip()}
    except Exception as e:  # ImportError 等 — 该能力降级，不影响核心
        logger.debug("[doctor] %s 不可用: %s", import_name, e)
        return {
            "ok": False,
            "severity": "optional",
            "detail": f"{capability} 不可用（缺 {pkg_name}，已自动降级）",
            "fix": f"pip install {pkg_name}",
        }


def _check_flac() -> Dict[str, Any]:
    """SpeechRecognition 识别非 wav 音频依赖系统 flac 二进制。"""
    if shutil.which("flac"):
        return {"ok": True, "severity": "optional", "detail": "flac 可用"}
    return {
        "ok": False,
        "severity": "optional",
        "detail": "flac 二进制缺失（部分音频格式识别降级）",
        "fix": "macOS: brew install flac｜Ubuntu: sudo apt install flac",
    }


def _check_llm_keys() -> Dict[str, Any]:
    from config.settings import settings

    if getattr(settings, "LARGE_MODEL_API_KEY", "") or getattr(settings, "OPENAI_API_KEY", ""):
        return {"ok": True, "severity": "required", "detail": "LLM 主模型密钥已配置"}
    return {
        "ok": False,
        "severity": "required",
        "detail": "未配置 LLM 密钥（对话不可用）",
        "fix": "编辑 .env 填入 OPENAI_API_KEY 或 LARGE_MODEL_API_KEY（或设置页 API 密钥栏）",
    }


def _check_vision_keys() -> Dict[str, Any]:
    from config.settings import settings

    if getattr(settings, "VISION_API_KEY", "") and getattr(settings, "VISION_API_URL", ""):
        return {"ok": True, "severity": "required", "detail": "视觉模型密钥已配置（图片/视频识别依赖）"}
    return {
        "ok": False,
        "severity": "required",
        "detail": "未配置视觉密钥（图片/视频识别不可用）",
        "fix": "设置页 → 视觉模型，填 VISION_API_KEY / VISION_API_URL",
    }


def _check_whisper_weights() -> Dict[str, Any]:
    d = Path.home() / ".cache" / "whisper"
    has_weights = d.is_dir() and any(d.glob("*.pt"))
    if has_weights:
        return {"ok": True, "severity": "optional", "detail": f"Whisper 权重已缓存: {d}"}
    return {
        "ok": False,
        "severity": "optional",
        "detail": "Whisper 权重未下载（首次语音识别时自动下载）",
        "fix": "可跳过；或提前手动执行 python -c \"import whisper; whisper.load_model('tiny')\"",
    }


def _embedding_roots() -> List[Path]:
    """Embedding 模型的候选根目录：打包态用 _MEIPASS，开发态用项目根。"""
    meipass = os.environ.get("_MEIPASS") or ""
    if meipass:
        return [Path(meipass)]
    return [Path(__file__).resolve().parents[1]]


def _check_embedding_models() -> Dict[str, Any]:
    for base in _embedding_roots():
        d = base / "data" / "memory" / "embeddings" / "models"
        if d.is_dir() and any(d.iterdir()):
            return {"ok": True, "severity": "required", "detail": str(d)}
    return {
        "ok": False,
        "severity": "required",
        "detail": "Embedding 模型目录为空（记忆检索降级）",
        "fix": "启动联网模式让后端自动下载，或从发布包 data/memory/embeddings/models 恢复",
    }


def run_doctor() -> Dict[str, Any]:
    """执行全部体检，永不抛异常。"""
    checks: List[Dict[str, Any]] = []

    def _add(name: str, fn, **kwargs):
        try:
            item = fn(**kwargs)
        except Exception as e:  # 单项异常不拖垮整体
            logger.debug("[doctor] %s 检查异常: %s", name, e)
            item = {"ok": False, "severity": "optional", "detail": f"检查失败: {e}"}
        checks.append({"name": name, **item})

    _add("ffmpeg", _check_ffmpeg)
    _add("llm_keys", _check_llm_keys)
    _add("vision_keys", _check_vision_keys)
    _add("embedding_models", _check_embedding_models)
    _add("whisper", _check_import, import_name="whisper", pkg_name="openai-whisper", capability="本地语音识别 STT")
    _add("pyaudio", _check_import, import_name="pyaudio", pkg_name="pyaudio", capability="语音输入采集")
    _add("pyautogui", _check_import, import_name="pyautogui", pkg_name="pyautogui", capability="屏幕控制")
    _add("flac", _check_flac)
    _add("whisper_weights", _check_whisper_weights)

    required_failed = [c for c in checks if not c["ok"] and c["severity"] == "required"]
    return {
        "success": True,
        "data": {
            "healthy": not required_failed,
            "checks": checks,
        },
    }
