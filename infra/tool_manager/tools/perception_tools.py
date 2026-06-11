"""
感知工具 — 模型可调用的感知操作

- transcribe_audio: 语音转文字（上传音频文件）
- understand_screen: 截图 + OCR + LLM 抽象理解
"""
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
)
async def understand_screen(focus: str = "") -> Dict[str, Any]:
    """截图 + 视觉理解（Qwen-VL）+ OCR 兜底"""
    try:
        # Step 1: 截图（在线程池中执行，避免阻塞事件循环）
        import asyncio
        screenshot_b64 = await asyncio.to_thread(_capture_screen)
        if not screenshot_b64:
            return {"error": "截图失败：无可用的屏幕捕获方式"}

        # Step 2: 获取窗口信息
        window_info = await asyncio.to_thread(_get_active_window)

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
    from utils.screen_capture import capture_screen
    return capture_screen() or ""


_cached_ocr_engine = None


def _ocr_screenshot(screenshot_b64: str) -> str:
    """对截图做 OCR，返回识别文字（OCR 引擎缓存复用）"""
    global _cached_ocr_engine
    try:
        import base64 as b64
        from PIL import Image
        import io

        img_data = b64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(img_data))

        # 懒初始化 OCR 引擎（只创建一次）
        if _cached_ocr_engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                _cached_ocr_engine = ("rapid", RapidOCR())
                logger.debug("OCR 引擎初始化: RapidOCR")
            except ImportError:
                try:
                    from paddleocr import PaddleOCR
                    _cached_ocr_engine = ("paddle", PaddleOCR(lang="ch"))
                    logger.debug("OCR 引擎初始化: PaddleOCR")
                except ImportError:
                    _cached_ocr_engine = ("none", None)

        engine_type, engine = _cached_ocr_engine
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
                # PaddleOCR 3.6+ 返回 dict 格式
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
    import asyncio
    ocr_text = await asyncio.to_thread(_ocr_screenshot, screenshot_b64)
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
)
async def detect_ui_elements(focus: str = "") -> Dict[str, Any]:
    """检测当前屏幕 UI 元素并返回坐标"""
    try:
        # 1. 截图
        from utils.screen_capture import capture_screen_base64
        screenshot_b64 = capture_screen_base64()
        if not screenshot_b64:
            return {"success": False, "error": "截图失败"}

        import base64
        image_bytes = base64.b64decode(screenshot_b64)

        # 2. OmniParser 检测
        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        detector = OmniParserDetector()
        elements = detector.detect_elements(image_bytes)

        if not elements:
            return {"success": True, "elements": [], "message": "未检测到 UI 元素"}

        # 3. 返回结构化元素列表
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
            "backend": detector.backend if hasattr(detector, 'backend') else detector._backend,
            "precision": detector.precision,
            "hint": "使用 mouse_click(x=center_x, y=center_y) 点击对应元素",
        }
    except Exception as e:
        logger.error(f"UI 元素检测失败: {e}")
        return {"success": False, "error": str(e)}

