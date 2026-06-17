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
        "截取当前屏幕并调用视觉模型（MLX-VLM）对屏幕内容进行智能理解。"
        "返回结构化的屏幕理解：当前应用、主要文字内容、界面布局。"
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
    """截图 + VLM 视觉理解"""
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
        }
    except Exception as e:
        return {"error": f"屏幕理解失败: {e}"}


def _capture_screen() -> str:
    """截取屏幕，返回 base64 编码的 PNG"""
    from utils.screen_capture import capture_screen
    return capture_screen() or ""


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
            return {"understanding": understanding, "method": analyzer.model_type}
        if "error" in result:
            return {"error": result.get("error", "视觉理解失败")}
        return {"error": "视觉理解返回空内容"}

    except Exception as e:
        logger.error(f"视觉理解失败: {e}")
        return {"error": f"视觉理解失败: {e}"}


@ToolRegistry.register(
    "detect_ui_elements",
    description=(
        "检测当前屏幕上的所有 UI 元素（按钮、输入框、文字、图标等），返回每个元素的类型、"
        "文字标签和精确像素坐标。\n\n"
        "【操作流程】\n"
        "1. detect_ui_elements() → 获取所有元素的坐标\n"
        "2. mouse_click(x=center_x, y=center_y) → 点击目标元素（如输入框）\n"
        "3. keyboard_type(text=\"内容\") → 输入文本\n"
        "4. keyboard_press(key=\"enter\") → 按回车提交\n"
        "5. 可选：再次 detect_ui_elements() 验证操作结果\n\n"
        "坐标说明：返回的 center_x/center_y 可直接传给 mouse_click。"
    ),
    params={
        "focus": {
            "type": "string",
            "description": "可选，关注重点描述，如「关注错误信息」「关注搜索栏」",
        },
        "app": {
            "type": "string",
            "description": "可选，指定应用名（如 Safari、Edge、网易云音乐），为空则自动扫描当前活跃窗口",
        },
    },
    risk_level="LOW",
    category="perception",
    core=True,
    tags=["learning"],
)
def detect_ui_elements(focus: str = "", app: str = "") -> Dict[str, Any]:
    """检测当前屏幕 UI 元素并返回坐标"""
    try:
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector
        detector = TouchpointDetector(fallback_to_screenmonitor=True)
        elements = detector.detect_elements(app=app)

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

