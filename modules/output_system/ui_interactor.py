"""
UI交互器 - 基于图像分析的UI元素检测和点击

功能：
1. 屏幕截图
2. UI元素检测（按钮、输入框等）
3. 基于元素位置点击
4. 随机偏移（模拟人类行为）
"""
import time
import random
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from utils.logger import setup_logger

logger = setup_logger("ui_interactor")


@dataclass
class UIElement:
    """UI元素"""
    element_type: str
    text: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    confidence: float = 0.0
    center_x: int = 0
    center_y: int = 0
    
    def random_offset(self, offset_range: int = 5) -> Tuple[int, int]:
        """生成随机偏移的点击坐标"""
        cx = self.center_x + random.randint(-offset_range, offset_range)
        cy = self.center_y + random.randint(-offset_range, offset_range)
        return cx, cy


@dataclass
class ClickResult:
    """点击结果"""
    success: bool
    element: str
    x: int
    y: int
    mode: str
    message: str = ""


class UIInteractor:
    """
    UI交互器
    
    基于图像分析的UI自动化，支持：
    - 截图
    - UI元素检测
    - 智能点击
    """
    
    def __init__(
        self,
        controller=None,
        image_analyzer=None,
        confidence_threshold: float = 0.7
    ):
        self.controller = controller
        self.image_analyzer = image_analyzer
        self.confidence_threshold = confidence_threshold
        
        self._init_dependencies()
    
    def _init_dependencies(self) -> None:
        """初始化依赖"""
        if self.controller is None:
            try:
                from modules.output_system.input_controller import InputController
                self.controller = InputController()
            except Exception as e:
                logger.warning(f"无法初始化输入控制器: {e}")
        
        if self.image_analyzer is None:
            try:
                from infra.data_process.core.image_analyzer import ImageAnalyzer
                self.image_analyzer = ImageAnalyzer()
            except Exception as e:
                logger.warning(f"无法初始化图像分析器: {e}")
    
    def capture_screen(self, region: Tuple[int, int, int, int] = None) -> Optional[bytes]:
        """截图"""
        if self.controller:
            return self.controller.screenshot(region)
        
        try:
            import pyautogui
            import io
            from PIL import Image
            
            if region:
                img = pyautogui.screenshot(region=region)
            else:
                img = pyautogui.screenshot()
            
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
        except Exception as e:
            logger.error(f"截图失败: {e}")
        return None
    
    def find_element_by_image(
        self,
        template_path: str,
        confidence: float = 0.8
    ) -> Optional[Tuple[int, int]]:
        """通过模板图像查找元素"""
        try:
            import pyautogui
            
            location = pyautogui.locateOnScreen(template_path, confidence=confidence)
            
            if location:
                center = pyautogui.center(location)
                return (center.x, center.y)
        except Exception as e:
            logger.error(f"查找图像失败: {e}")
        return None
    
    def find_all_elements_by_image(
        self,
        template_path: str,
        confidence: float = 0.8
    ) -> List[Tuple[int, int]]:
        """查找所有匹配的图像"""
        results = []
        try:
            import pyautogui
            
            for location in pyautogui.locateAllOnScreen(template_path, confidence=confidence):
                center = pyautogui.center(location)
                results.append((center.x, center.y))
        except Exception as e:
            logger.error(f"查找图像失败: {e}")
        return results
    
    async def detect_ui_elements(
        self,
        image_data: bytes = None,
        element_types: List[str] = None
    ) -> List[UIElement]:
        """检测UI元素"""
        if image_data is None:
            image_data = self.capture_screen()
        
        if image_data is None or self.image_analyzer is None:
            return []
        
        try:
            from infra.data_process.core.image_analyzer import ImageAnalyzer
            analyzer = ImageAnalyzer()
            await analyzer.initialize()
            
            result = await analyzer.detect_ui_elements(image_data, element_types)
            
            elements = []
            for item in result.get("elements", []):
                bounds = item.get("bounds", {})
                x = bounds.get("x", 0)
                y = bounds.get("y", 0)
                w = bounds.get("width", 0)
                h = bounds.get("height", 0)
                
                element = UIElement(
                    element_type=item.get("type", "unknown"),
                    text=item.get("text", ""),
                    x=x, y=y, width=w, height=h,
                    confidence=item.get("confidence", 0.0),
                    center_x=x + w // 2,
                    center_y=y + h // 2
                )
                elements.append(element)
            
            return elements
        except Exception as e:
            logger.error(f"检测UI元素失败: {e}")
        return []
    
    def click_element(
        self,
        element: UIElement,
        random_offset: bool = True,
        offset_range: int = 5
    ) -> ClickResult:
        """点击UI元素"""
        if self.controller is None:
            return ClickResult(
                success=False,
                element=element.element_type,
                x=0, y=0,
                mode="unknown",
                message="控制器未初始化"
            )
        
        if random_offset:
            x, y = element.random_offset(offset_range)
        else:
            x, y = element.center_x, element.center_y
        
        success = self.controller.click(x, y)
        
        return ClickResult(
            success=success,
            element=f"{element.element_type}: {element.text}",
            x=x, y=y,
            mode="real",
            message="" if success else "点击失败"
        )
    
    def click_at_position(
        self,
        x: int,
        y: int,
        random_offset: bool = True,
        offset_range: int = 5
    ) -> ClickResult:
        """点击指定位置"""
        if self.controller is None:
            return ClickResult(
                success=False,
                element="position",
                x=0, y=0,
                mode="unknown",
                message="控制器未初始化"
            )
        
        if random_offset:
            x += random.randint(-offset_range, offset_range)
            y += random.randint(-offset_range, offset_range)
        
        success = self.controller.click(x, y)
        
        return ClickResult(
            success=success,
            element=f"position",
            x=x, y=y,
            mode="real",
            message="" if success else "点击失败"
        )
    
    def find_and_click(
        self,
        text: str,
        element_type: str = None,
        wait_time: float = 1.0
    ) -> ClickResult:
        """查找包含文本的元素并点击（基于图像）"""
        logger.info(f"查找并点击: {text}")
        
        time.sleep(wait_time)
        
        return ClickResult(
            success=False,
            element=text,
            x=0, y=0,
            mode="mock",
            message="基于图像的UI元素查找功能需接入视觉模型（如Qwen-VL、LLaVA）"
        )
    
    def type_at_element(
        self,
        element: UIElement,
        text: str,
        clear_first: bool = False
    ) -> bool:
        """在元素位置输入文本"""
        if self.controller is None:
            return False
        
        x, y = element.center_x, element.center_y
        
        self.controller.click(x, y)
        time.sleep(0.1)
        
        if clear_first:
            self.controller.hotkey('cmd', 'a')
            time.sleep(0.05)
            self.controller.press('backspace')
            time.sleep(0.05)
        
        return self.controller.typewrite(text)
    
    def hover_element(self, element: UIElement) -> bool:
        """悬停到元素"""
        if self.controller is None:
            return False
        
        x, y = element.center_x, element.center_y
        result = self.controller.move_to(x, y, duration=0.2)
        return result.success
    
    def scroll_at_element(
        self,
        element: UIElement,
        clicks: int
    ) -> bool:
        """在元素位置滚动"""
        if self.controller is None:
            return False
        
        return self.controller.scroll(clicks, element.center_x, element.center_y)


ui_interactor = UIInteractor()
