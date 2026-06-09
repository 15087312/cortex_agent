"""
感知工具 — 模型可调用的感知操作

- transcribe_audio: 语音转文字（上传音频文件）
- understand_screen: 截图 + OCR + LLM 抽象理解
"""
import base64
import io
import tempfile
import os
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("perception_tools")


@ToolRegistry.register(
    "transcribe_audio",
    description="将音频文件转为文字。支持 WAV/MP3/FLAC/OGG/WebM 格式。使用 Whisper 本地模型识别。",
    params={
        "audio_base64": "音频文件的 base64 编码（与 file_path 二选一）",
        "file_path": "音频文件路径（与 audio_base64 二选一）",
        "language": "可选，语言代码（zh/en/ja 等，默认自动检测）",
    },
    risk_level="LOW",
    category="perception",
    core=True,
)
async def transcribe_audio(
    audio_base64: str = "",
    file_path: str = "",
    language: str = "",
) -> Dict[str, Any]:
    """语音转文字"""
    try:
        from infra.data_process.core.speech_recognizer import SpeechRecognizer

        recognizer = SpeechRecognizer(model_name="tiny", language="auto")
        await recognizer.initialize()

        if file_path:
            if not os.path.exists(file_path):
                return {"error": f"文件不存在: {file_path}"}
            result = await recognizer.recognize_file(file_path, language=language or None)
        elif audio_base64:
            audio_bytes = base64.b64decode(audio_base64)
            result = await recognizer.recognize(audio_bytes, language=language or None)
        else:
            return {"error": "请提供 audio_base64 或 file_path"}

        return {
            "success": True,
            "text": result.get("text", ""),
            "language": result.get("language", ""),
            "duration": result.get("duration", 0),
        }
    except Exception as e:
        return {"error": f"语音识别失败: {e}"}


@ToolRegistry.register(
    "understand_screen",
    description=(
        "截取当前屏幕并进行智能理解。"
        "先截图，再 OCR 识别文字，最后用 LLM 对屏幕内容进行抽象总结。"
        "返回结构化的屏幕理解：当前应用、主要文字内容、UI 元素、操作建议。"
    ),
    params={
        "focus": "可选，关注重点（如「关注错误信息」「关注表格数据」）",
    },
    risk_level="LOW",
    category="perception",
    core=True,
)
async def understand_screen(focus: str = "") -> Dict[str, Any]:
    """截图 + 视觉理解（Qwen-VL）+ OCR 兜底"""
    try:
        # Step 1: 截图
        screenshot_b64 = _capture_screen()
        if not screenshot_b64:
            return {"error": "截图失败：无可用的屏幕捕获方式"}

        # Step 2: 获取窗口信息
        window_info = _get_active_window()

        # Step 3: 视觉理解（优先 Qwen-VL，降级到 OCR + 文本 LLM）
        vision_result = await _vision_understand(screenshot_b64, window_info, focus)

        return {
            "success": True,
            "window": window_info,
            "understanding": vision_result.get("understanding", ""),
            "method": vision_result.get("method", "unknown"),
            "ocr_text": vision_result.get("ocr_text", ""),
        }
    except Exception as e:
        return {"error": f"屏幕理解失败: {e}"}


def _capture_screen() -> str:
    """截取屏幕，返回 base64 编码的 PNG"""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # 主显示器
            screenshot = sct.grab(monitor)
            from PIL import Image
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            # 缩小到 1280 宽度以减少 token
            w, h = img.size
            if w > 1280:
                ratio = 1280 / w
                img = img.resize((1280, int(h * ratio)))
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        pass

    # PIL.ImageGrab fallback (Windows / macOS)
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        w, h = img.size
        if w > 1280:
            ratio = 1280 / w
            img = img.resize((1280, int(h * ratio)))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass

    # macOS screencapture fallback
    try:
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        subprocess.run(["screencapture", "-x", tmp_path], timeout=5, check=True)
        with open(tmp_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        os.unlink(tmp_path)
        return data
    except Exception:
        return ""


def _ocr_screenshot(screenshot_b64: str) -> str:
    """对截图做 OCR，返回识别文字"""
    try:
        import base64 as b64
        from PIL import Image
        import io

        img_data = b64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(img_data))

        # 优先用 RapidOCR
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            import numpy as np
            img_np = np.array(img)
            result, _ = ocr(img_np)
            if result:
                lines = [item[1] for item in result if len(item) > 1]
                return "\n".join(lines)
        except ImportError:
            pass

        # 降级：返回空
        return "(OCR 引擎不可用)"
    except Exception as e:
        return f"(OCR 失败: {e})"


def _get_active_window() -> str:
    """获取当前活动窗口信息（跨平台）"""
    import sys

    # macOS
    if sys.platform == "darwin":
        try:
            import subprocess
            script = 'tell application "System Events" to get name of first application process whose frontmost is true'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

    # Windows
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value
        except Exception:
            pass

    # Linux
    if sys.platform.startswith("linux"):
        try:
            import subprocess
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

    return "未知"


async def _vision_understand(
    screenshot_b64: str,
    window_info: str,
    focus: str,
) -> Dict[str, Any]:
    """视觉理解：优先 Qwen-VL（看图），降级到 OCR + 文本 LLM（看文字）"""

    # ── 尝试 Qwen-VL 真正的视觉理解 ──
    try:
        from infra.data_process.core.image_analyzer import ImageAnalyzer
        import base64 as b64

        analyzer = ImageAnalyzer(model_type="auto")
        await analyzer.initialize()

        if analyzer.model_type in ("qwen_vl", "mlx_vlm"):
            prompt = "请详细描述这个屏幕截图的内容。包括：当前应用、界面布局、可见的文字、按钮、错误信息等。"
            if focus:
                prompt += f"\n特别关注：{focus}"

            result = await analyzer.analyze(b64.b64decode(screenshot_b64), prompt=prompt)
            understanding = result.get("description", "")
            if understanding:
                return {"understanding": understanding, "method": analyzer.model_type, "ocr_text": ""}
    except Exception as e:
        logger.debug(f"Qwen-VL 视觉理解失败，降级: {e}")

    # ── 降级：OCR + 文本 LLM ──
    ocr_text = _ocr_screenshot(screenshot_b64)
    try:
        from modules.thinking.experts.pre_gen_experts import _get_lite_model
        model = _get_lite_model()
        if model:
            prompt = (
                f"你是一个屏幕内容分析专家。请对以下屏幕信息进行结构化总结。\n\n"
                f"当前应用: {window_info}\n"
                f"OCR 识别文字:\n{ocr_text[:2000]}\n\n"
            )
            if focus:
                prompt += f"用户关注重点: {focus}\n\n"
            prompt += (
                "请用简洁的结构化格式总结：\n"
                "1. 当前在做什么（一句话）\n"
                "2. 关键信息（列表）\n"
                "3. 可能需要的操作建议（如有）\n"
                "用中文回答，总共不超过 200 字。"
            )
            result = await model.generate(prompt, max_tokens=300, temperature=0.3)
            return {"understanding": result.strip(), "method": "ocr+llm", "ocr_text": ocr_text[:3000]}
    except Exception as e:
        logger.debug(f"文本 LLM 理解失败: {e}")

    # ── 最终兜底：简单总结 ──
    return {"understanding": _simple_summarize(ocr_text, window_info), "method": "ocr_only", "ocr_text": ocr_text[:3000]}


def _simple_summarize(ocr_text: str, window_info: str) -> str:
    """无 LLM 时的简单总结"""
    lines = [l.strip() for l in ocr_text.split("\n") if l.strip()]
    summary = f"应用: {window_info}\n"
    if lines:
        summary += f"屏幕文字 ({len(lines)} 行):\n"
        for line in lines[:10]:
            summary += f"  - {line[:80]}\n"
        if len(lines) > 10:
            summary += f"  ... 还有 {len(lines) - 10} 行\n"
    else:
        summary += "屏幕无明显文字内容\n"
    return summary
