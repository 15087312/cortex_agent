"""
VisionBackend — 截图 + 视觉模型分析

当 touchpoint/cdp 信息不足时，用视觉模型理解屏幕。
适用于所有应用，包括 Chromium 和游戏。
"""
import time

from modules.perception.screen.context import ScreenContext, UIElement
from utils.logger import setup_logger

logger = setup_logger("vision_backend")


class VisionBackend:
    """视觉模型后端"""

    def __init__(self):
        self._analyzer = None

    def is_available(self) -> bool:
        """检查视觉模型是否可用"""
        try:
            from infra.data_process.core.image_analyzer import ImageAnalyzer  # noqa: F401
            return True
        except ImportError:
            return False

    async def detect(self, app: str = "", prompt: str = "") -> ScreenContext:
        """截图 + 视觉模型分析"""
        import base64
        from utils.screen_capture import capture_screen

        t0 = time.time()
        result = ScreenContext(backend="vision", app_name=app)

        # 截图
        screenshot = capture_screen()
        if not screenshot:
            logger.warning("截图失败")
            return result

        # 默认 prompt
        if not prompt:
            prompt = (
                "你是纯视觉描述模块，只负责客观描述屏幕上实际可见的 UI 元素。"
                "不做任何评价（不评判好坏/美丑/设计/加载状态是否正常），"
                "不给任何操作建议或引导（不说'你应该点击…'、不指导下一步）。"
                "列出所有可见的 UI 元素。"
                "对每个元素提供：类型(button/text/input/menu/tab/...)、"
                "文字标签、大致位置(上/中/下/左/右)。"
                "只报告你实际看到的，用 JSON 数组格式返回。"
            )

        # 调用视觉模型
        try:
            from infra.data_process.core.image_analyzer import ImageAnalyzer
            analyzer = ImageAnalyzer(model_type="auto")
            await analyzer.initialize()

            image_data = base64.b64decode(screenshot)
            vision_result = await analyzer.analyze(image_data, prompt=prompt)

            description = vision_result.get("description", "")
            result.visual_description = description

            # 尝试从描述中提取结构化元素
            elements = self._parse_elements_from_description(description)
            result.elements = elements
            result.element_count = len(elements)

        except Exception as e:
            logger.error(f"视觉分析失败: {e}")

        result.elapsed_ms = (time.time() - t0) * 1000
        return result

    def _parse_elements_from_description(self, description: str) -> list:
        """从视觉模型描述中提取 UI 元素（简化版）"""
        import re
        elements = []

        # 尝试匹配常见模式
        # "按钮 'xxx'" 或 "button: xxx"
        button_patterns = [
            r'(?:按钮|button)[：:]\s*["""]?([^"""\n]+)',
            r'["""]([^"""]+)["""]\s*(?:按钮|button)',
        ]
        for pattern in button_patterns:
            for match in re.finditer(pattern, description, re.IGNORECASE):
                label = match.group(1).strip()
                if label and len(label) < 50:
                    elements.append(UIElement(
                        type="button",
                        label=label,
                    ))

        # "输入框" 或 "input"
        input_patterns = [
            r'(?:输入框|input|搜索框)[：:]\s*["""]?([^"""\n]+)',
        ]
        for pattern in input_patterns:
            for match in re.finditer(pattern, description, re.IGNORECASE):
                label = match.group(1).strip()
                if label:
                    elements.append(UIElement(
                        type="text_field",
                        label=label,
                    ))

        # 去重
        seen = set()
        unique = []
        for e in elements:
            key = f"{e.type}:{e.label}"
            if key not in seen:
                seen.add(key)
                unique.append(e)

        return unique
