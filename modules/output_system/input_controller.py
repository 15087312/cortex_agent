"""
输入控制器 - 输出系统的硬件控制抽象层

功能：
- 提供统一的鼠标键盘控制接口
- 委托给基础设施层的硬件控制器执行
- 不包含具体实现，仅作为模块间调用的桥梁
"""
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from utils.logger import setup_logger
from infra.hardware_input import PyAutoGUIController, HardwareInputController

logger = setup_logger("input_controller")


@dataclass
class Point:
    """坐标点"""
    x: int
    y: int
    
    def offset(self, dx: int = 0, dy: int = 0) -> 'Point':
        return Point(self.x + dx, self.y + dy)


class InputController:
    """
    输入控制器
    
    委托给基础设施层的硬件控制器执行具体操作。
    默认使用 PyAutoGUI 控制器。
    """
    
    def __init__(self, force: bool = False):
        if hasattr(self, '_initialized') and self._initialized and not force:
            return
        
        self._controller: HardwareInputController = PyAutoGUIController()
        self._paused = False
        
        logger.info("输入控制器初始化完成")
        self._initialized = True
    
    # ========== 鼠标操作 ==========
    
    def move_to(self, x: int, y: int, duration: float = 0.3) -> bool:
        """移动鼠标到指定位置"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.move_to(x, y, duration)
    
    def click(
        self,
        x: int = None,
        y: int = None,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1
    ) -> bool:
        """点击鼠标"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.click(x, y, button, clicks, interval)
    
    def double_click(self, x: int = None, y: int = None, button: str = "left") -> bool:
        """双击"""
        return self.click(x, y, button=button, clicks=2)
    
    def right_click(self, x: int = None, y: int = None) -> bool:
        """右键点击"""
        return self.click(x, y, button="right")
    
    def middle_click(self, x: int = None, y: int = None) -> bool:
        """中键点击"""
        return self.click(x, y, button="middle")
    
    def scroll(self, clicks: int, x: int = None, y: int = None) -> bool:
        """滚动鼠标"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.scroll(clicks, x, y)
    
    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, 
             duration: float = 0.5) -> bool:
        """拖拽"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.drag(start_x, start_y, end_x, end_y, duration)
    
    def get_current_position(self) -> Tuple[int, int]:
        """获取当前鼠标位置"""
        return self._controller.get_current_position()
    
    # ========== 键盘操作 ==========
    
    def press(self, key: str) -> bool:
        """按下按键"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.press_key(key)
    
    def typewrite(self, text: str, interval: float = 0.05) -> bool:
        """输入文本"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.type_text(text, interval)
    
    def key_down(self, key: str) -> bool:
        """按住按键"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.key_down(key)
    
    def key_up(self, key: str) -> bool:
        """释放按键"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.key_up(key)
    
    def hotkey(self, *keys) -> bool:
        """组合键"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return False
        return self._controller.hotkey(*keys)
    
    def screenshot(self, region: Tuple[int, int, int, int] = None) -> Optional[bytes]:
        """截图"""
        if self._paused:
            logger.warning("输入控制已暂停，操作被忽略")
            return None
        return self._controller.screenshot(region)
    
    # ========== 控制 ==========
    
    def pause(self) -> None:
        """暂停所有操作"""
        self._paused = True
        logger.info("输入控制已暂停")
    
    def resume(self) -> None:
        """恢复操作"""
        self._paused = False
        logger.info("输入控制已恢复")
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        pos = self.get_current_position()
        
        return {
            "paused": self._paused,
            "mouse_position": {"x": pos[0], "y": pos[1]},
            "controller_available": self._controller._initialized
        }


# CONC-7: Use lazy factory instead of module-level singleton
# Avoid initializing hardware at import time (breaks CI/headless environments)
_input_controller_instance = None

def get_input_controller() -> InputController:
    """Get or create input controller instance (lazy factory)"""
    global _input_controller_instance
    if _input_controller_instance is None:
        _input_controller_instance = InputController()
    return _input_controller_instance

# Backwards compatibility: module-level access via property
class _InputControllerProxy:
    def __getattr__(self, name):
        return getattr(get_input_controller(), name)

input_controller = _InputControllerProxy()
