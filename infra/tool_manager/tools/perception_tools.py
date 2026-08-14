"""
感知工具 — 模型可调用的感知操作

- transcribe_audio: 语音转文字（上传音频文件）
- understand_screen: 截图 + OCR + LLM 抽象理解
- detect_ui_elements: 无障碍 API 检测 UI 元素
"""
import base64
import os
from typing import Dict, Any

from infra.tool_manager.tool_registry import ToolRegistry
from infra.tool_manager.service_registry import get_capability
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
async def understand_screen(focus: str = "") -> Dict[str, Any]:
    """截图 + VLM 视觉理解（async 确保与 ToolRegistry.call_tool 兼容）"""
    try:
        screenshot_b64 = _capture_screen()
        if not screenshot_b64:
            return {"error": "截图失败：无可用的屏幕捕获方式"}
        window_info = _get_active_window()
        vision_result = await _vision_understand(screenshot_b64, window_info, focus)

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
    """视觉理解（async）：使用 ImageAnalyzer 调用本地 MLX-VLM"""
    try:
        import base64 as b64
        import time

        from infra.data_process.core.image_analyzer import get_default_analyzer

        analyzer = await get_default_analyzer()

        prompt = (
            "你是纯视觉描述模块，只负责客观描述屏幕上实际可见的视觉内容，你只是用于描述视觉效果，不具备人格。"
            "不给任何操作建议或引导（不说'你应该…'、不指导下一步、不提醒等待）。"
            "请用一句话客观概括当前屏幕：什么应用、主要界面、可见的关键元素。"
        )
        if focus:
            prompt += f" 重点描述：{focus}"

        logger.info("[视觉理解] 开始分析图像...")
        start = time.time()

        result = await analyzer.analyze(b64.b64decode(screenshot_b64), prompt=prompt)
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
        "通过 macOS 无障碍 API 检测当前屏幕上的所有 UI 元素（按钮、输入框、文字、图标等），"
        "返回每个元素的类型、文字标签和精确像素坐标。\n\n"
        "支持深度控制：depth 越大，获取的元素层级越深。\n"
        "  depth=1: 顶层容器（8个左右）\n"
        "  depth=2: 包含子面板（50个左右）\n"
        "  depth=3: 包含按钮等控件（75个左右）\n"
        "  depth=0: 全部元素（400+个）\n\n"
        "【操作流程】\n"
        "1. detect_ui_elements() → 获取所有元素的坐标\n"
        "2. mouse_click(x=center_x, y=center_y) → 点击目标元素（如输入框）\n"
        "3. keyboard_type(text=\"内容\") → 输入文本\n"
        "4. keyboard_press(key=\"enter\") → 按回车提交\n"
        "5. 可选：再次 detect_ui_elements() 验证操作结果\n\n"
        "坐标说明：返回的 center_x/center_y 可直接传给 mouse_click。"
    ),
    params={
        "depth": {
            "type": "integer",
            "description": "可选，检测深度（1=顶层容器，2=子面板，3=按钮控件，0=全部，默认 3）",
        },
        "role_filter": {
            "type": "string",
            "description": "可选，只返回指定角色的元素（如 button/text_field/text），为空则返回全部",
        },
        "named_only": {
            "type": "boolean",
            "description": "可选，是否只返回有名字的元素（默认 true，过滤无意义元素）",
        },
        "app": {
            "type": "string",
            "description": "可选，指定应用名（如 Safari、PyCharm），为空则自动扫描当前活跃窗口",
        },
    },
    risk_level="LOW",
    category="perception",
    core=True,
    tags=["learning"],
)
def detect_ui_elements(
    depth: int = 3,
    role_filter: str = "",
    named_only: bool = True,
    app: str = "",
) -> Dict[str, Any]:
    """检测 UI 元素 — 自动选择最佳检测后端"""
    try:
        factory = get_capability("detector_router")
        if factory is None:
            return {"success": False, "error": "感知服务未注册", "elements": []}
        router = factory()
        ctx = router.detect(app=app, depth=depth)

        # 角色过滤
        elements = ctx.elements
        if role_filter:
            elements = [e for e in elements if e.type == role_filter.lower()]

        # 格式化输出
        formatted = [
            {
                "element_id": e.element_id,
                "type": e.type,
                "label": e.label,
                "bbox": e.bbox,
                "center_x": e.center_x,
                "center_y": e.center_y,
                "actions": e.actions,
            }
            for e in elements
        ]

        return {
            "success": True,
            "app": ctx.app_name,
            "depth": ctx.depth,
            "elements": formatted,
            "count": len(formatted),
            "role_summary": ctx.role_summary,
            "elapsed_ms": round(ctx.elapsed_ms),
            "backend": ctx.backend,
            "visual_description": ctx.visual_description[:300] if ctx.visual_description else "",
            "hint": "使用 mouse_click(x=center_x, y=center_y) 点击对应元素",
        }
    except Exception as e:
        logger.error(f"UI 元素检测失败: {e}")
        return {"success": False, "error": str(e)}

