"""
感知工具 — 模型可调用的感知操作

- transcribe_audio: 语音转文字（上传音频文件）
- understand_screen: 截图 + OCR + LLM 抽象理解
"""
import asyncio
import base64
import io
import os
from typing import Dict, Any

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
    tags=["learning"],
)
def understand_screen(focus: str = "") -> Dict[str, Any]:
    """截图 + 视觉理解（Qwen-VL）+ OCR 兜底"""
    try:
        screenshot_b64 = _capture_screen()
        if not screenshot_b64:
            return {"error": "截图失败：无可用的屏幕捕获方式"}
        window_info = _get_active_window()
        vision_result = _vision_understand(screenshot_b64, window_info, focus)

        if "error" in vision_result:
            return {
                "success": False,
                "error": vision_result["error"],
                "window": window_info,
            }

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
    from utils.screen_capture import capture_screen
    return capture_screen() or ""


def _ocr_screenshot(screenshot_b64: str) -> str:
    """对截图做 OCR，返回识别文字（共享全局 OCR 引擎）"""
    try:
        import base64 as b64
        from PIL import Image
        import io

        img_data = b64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(img_data))

        from utils.ocr_utils import get_ocr_engine
        engine_type, engine = get_ocr_engine()
        if engine is None:
            return "(OCR 引擎不可用)"

        import numpy as np
        img_np = np.array(img)
        logger.debug("OCR 输入: shape=%s dtype=%s", img_np.shape, img_np.dtype)

        # RGBA → RGB（PaddleOCR 不支持 alpha 通道）
        if img_np.ndim == 3 and img_np.shape[2] == 4:
            img_np = img_np[:, :, :3]

        if engine_type == "rapid":
            result, _ = engine(img_np)
            logger.debug("RapidOCR result type=%s", type(result))
            if result:
                return "\n".join(item[1] for item in result if len(item) > 1)
        elif engine_type == "paddle":
            import traceback
            try:
                result = engine.ocr(img_np)
            except Exception as ocr_err:
                logger.error("PaddleOCR.ocr() 内部异常: %s: %s", type(ocr_err).__name__, ocr_err)
                logger.debug("PaddleOCR traceback:\n%s", traceback.format_exc())
                return f"(PaddleOCR 内部错误: {ocr_err})"
            logger.debug("PaddleOCR result type=%s", type(result))
            if result and result[0]:
                texts = result[0].get("rec_texts", [])
                return "\n".join(t for t in texts if t)

        return "(OCR 未识别到文字)"
    except Exception as e:
        logger.error("OCR 异常: %s: %s", type(e).__name__, e)
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


def _vision_understand(
    screenshot_b64: str,
    window_info: str,
    focus: str,
) -> Dict[str, Any]:
    """视觉理解（同步）：使用预加载的 ImageAnalyzer 调用本地 MLX-VLM"""
    try:
        import asyncio
        import base64 as b64
        import time

        from infra.data_process.core.image_analyzer import _default_analyzer

        # 预加载的分析器（api/main.py lifespan 中初始化）
        analyzer = _default_analyzer
        if analyzer is None:
            return {"error": "视觉分析器未初始化"}

        prompt = "描述这个屏幕截图：当前应用、界面布局、可见文字按钮。"
        if focus:
            prompt += f" 重点关注：{focus}"

        logger.info("[视觉理解] 开始分析图像...")
        start = time.time()

        # 同步调用 async analyze：MLX-VLM 推理本质是同步的，
        # asyncio.run() 创建临时事件循环，没有默认 executor 创建，安全关闭
        result = asyncio.run(
            analyzer.analyze(b64.b64decode(screenshot_b64), prompt=prompt)
        )
        logger.info(f"[视觉理解] 分析完成 ({time.time()-start:.2f}s)")

        understanding = result.get("description", "")
        if understanding:
            return {"understanding": understanding, "method": analyzer.model_type, "ocr_text": ""}
        if "error" in result:
            return {"error": result.get("error", "视觉理解失败")}
        return {"error": "视觉理解返回空内容"}

    except Exception as e:
        logger.error(f"视觉理解失败: {e}")
        return {"error": f"视觉理解失败: {e}"}


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


@ToolRegistry.register(
    "detect_ui_elements",
    description=(
        "检测当前屏幕上的所有 UI 元素（按钮、输入框、文字、图标等），返回每个元素的类型、"
        "文字标签和精确像素坐标。之后可使用 mouse_click(x=center_x, y=center_y) 点击相应元素。"
    ),
    params={
        "focus": "可选，关注重点描述，如「关注错误信息」「关注搜索栏」",
    },
    risk_level="LOW",
    category="perception",
    core=True,
    tags=["learning"],
)
def detect_ui_elements(focus: str = "") -> Dict[str, Any]:
    """检测当前屏幕 UI 元素并返回坐标"""
    try:
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector
        detector = TouchpointDetector(fallback_to_screenmonitor=True)
        elements = detector.detect_elements()

        if not elements:
            return {"success": True, "elements": [], "message": "未检测到 UI 元素"}

        result = []
        for elem in elements:
            result.append({
                "element_id": elem.element_id,
                "type": elem.type,
                "label": elem.label,
                "bbox": elem.bbox,
                "center_x": elem.center_x,
                "center_y": elem.center_y,
                "confidence": round(elem.confidence, 2),
            })

        return {
            "success": True,
            "elements": result,
            "count": len(result),
            "backend": detector.backend,
            "precision": detector.precision,
            "hint": "使用 mouse_click(x=center_x, y=center_y) 点击对应元素",
        }
    except Exception as e:
        logger.error(f"UI 元素检测失败: {e}")
        return {"success": False, "error": str(e)}

