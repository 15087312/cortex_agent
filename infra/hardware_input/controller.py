"""
硬件输入控制器 - 基础设施层

提供统一的鼠标键盘硬件控制接口，支持多种后端：
- pyautogui: 桌面自动化
- serial: 单片机串口控制
"""
import time
import random
from typing import Tuple, Optional
from utils.logger import setup_logger

logger = setup_logger("hardware_input")


class HardwareInputController:
    """硬件输入控制器基类"""
    
    def __init__(self):
        self._initialized = False
    
    def move_to(self, x: int, y: int, duration: float = 0.3) -> bool:
        raise NotImplementedError
    
    def click(self, x: int = None, y: int = None, button: str = "left", 
              clicks: int = 1, interval: float = 0.1) -> bool:
        raise NotImplementedError
    
    def scroll(self, clicks: int, x: int = None, y: int = None) -> bool:
        raise NotImplementedError
    
    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, 
             duration: float = 0.5) -> bool:
        raise NotImplementedError
    
    def get_current_position(self) -> Tuple[int, int]:
        raise NotImplementedError
    
    def press_key(self, key: str) -> bool:
        raise NotImplementedError
    
    def type_text(self, text: str, interval: float = 0.05) -> bool:
        raise NotImplementedError
    
    def key_down(self, key: str) -> bool:
        raise NotImplementedError
    
    def key_up(self, key: str) -> bool:
        raise NotImplementedError
    
    def hotkey(self, *keys) -> bool:
        raise NotImplementedError
    
    def screenshot(self, region: Tuple[int, int, int, int] = None) -> Optional[bytes]:
        raise NotImplementedError


class PyAutoGUIController(HardwareInputController):
    """PyAutoGUI 控制器"""
    
    def __init__(self):
        super().__init__()
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.1
            self._controller = pyautogui
            self._initialized = True
            logger.info("PyAutoGUI 控制器初始化成功")
        except ImportError:
            logger.error("pyautogui 未安装")
            self._initialized = False
    
    def _apply_randomness(self, x: int, y: int) -> Tuple[int, int]:
        rx = x + random.randint(-3, 3)
        ry = y + random.randint(-3, 3)
        return rx, ry
    
    def move_to(self, x: int, y: int, duration: float = 0.3) -> bool:
        if not self._initialized:
            return False
        rx, ry = self._apply_randomness(x, y)
        try:
            self._controller.moveTo(rx, ry, duration=duration)
            return True
        except Exception as e:
            logger.error(f"鼠标移动失败: {e}")
            return False
    
    def click(self, x: int = None, y: int = None, button: str = "left",
              clicks: int = 1, interval: float = 0.1) -> bool:
        if not self._initialized:
            return False
        
        if x is None or y is None:
            pos = self.get_current_position()
            x, y = pos
        
        rx, ry = self._apply_randomness(x, y)
        
        try:
            if x is not None and y is not None:
                self._controller.moveTo(rx, ry, duration=0.1)
                time.sleep(0.05)
            self._controller.click(x=rx, y=ry, clicks=clicks, interval=interval, button=button)
            return True
        except Exception as e:
            logger.error(f"鼠标点击失败: {e}")
            return False
    
    def scroll(self, clicks: int, x: int = None, y: int = None) -> bool:
        if not self._initialized:
            return False
        try:
            if x is not None and y is not None:
                self._controller.moveTo(x, y)
            self._controller.scroll(clicks)
            return True
        except Exception as e:
            logger.error(f"滚动失败: {e}")
            return False
    
    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
             duration: float = 0.5) -> bool:
        if not self._initialized:
            return False
        sx, sy = self._apply_randomness(start_x, start_y)
        ex, ey = self._apply_randomness(end_x, end_y)
        
        try:
            self._controller.moveTo(sx, sy)
            time.sleep(0.05)
            self._controller.drag(ex - sx, ey - sy, duration=duration, button="left")
            return True
        except Exception as e:
            logger.error(f"拖拽失败: {e}")
            return False
    
    def get_current_position(self) -> Tuple[int, int]:
        if not self._initialized:
            return (0, 0)
        try:
            return self._controller.position()
        except Exception as e:
            logger.warning(f"获取鼠标位置失败: {e}")
            return (0, 0)
    
    def press_key(self, key: str) -> bool:
        if not self._initialized:
            return False
        try:
            self._controller.press(key)
            return True
        except Exception as e:
            logger.error(f"按键失败: {e}")
            return False
    
    def type_text(self, text: str, interval: float = 0.05) -> bool:
        if not self._initialized:
            return False
        try:
            self._controller.write(text, interval=interval)
            return True
        except Exception as e:
            logger.error(f"文本输入失败: {e}")
            return False
    
    def key_down(self, key: str) -> bool:
        if not self._initialized:
            return False
        try:
            self._controller.keyDown(key)
            return True
        except Exception as e:
            logger.error(f"按下按键失败: {e}")
            return False
    
    def key_up(self, key: str) -> bool:
        if not self._initialized:
            return False
        try:
            self._controller.keyUp(key)
            return True
        except Exception as e:
            logger.error(f"释放按键失败: {e}")
            return False
    
    def hotkey(self, *keys) -> bool:
        if not self._initialized:
            return False
        try:
            self._controller.hotkey(*keys)
            return True
        except Exception as e:
            logger.error(f"组合键失败: {e}")
            return False
    
    def screenshot(self, region: Tuple[int, int, int, int] = None) -> Optional[bytes]:
        if not self._initialized:
            return None
        try:
            import io
            from PIL import Image
            
            if region:
                img = self._controller.screenshot(region=region)
            else:
                img = self._controller.screenshot()
            
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None


class SerialController(HardwareInputController):
    """单片机串口控制器"""
    
    def __init__(self, port: str = 'COM3', baudrate: int = 115200):
        super().__init__()
        self._port = port
        self._baudrate = baudrate
        self._serial = None
        try:
            import serial
            self._serial = serial.Serial(port, baudrate, timeout=1)
            self._initialized = True
            logger.info(f"串口控制器初始化成功: {port}")
        except ImportError:
            logger.error("pyserial 未安装")
        except Exception as e:
            logger.error(f"串口初始化失败: {e}")
    
    def _send_command(self, command: str) -> bool:
        if not self._serial:
            return False
        try:
            self._serial.write(f"{command}\n".encode())
            return True
        except Exception as e:
            logger.error(f"发送命令失败: {e}")
            return False
    
    def move_to(self, x: int, y: int, duration: float = 0.3) -> bool:
        return self._send_command(f"MOVE:{x},{y}")
    
    def click(self, x: int = None, y: int = None, button: str = "left",
              clicks: int = 1, interval: float = 0.1) -> bool:
        return self._send_command(f"CLICK:{button}")
    
    def scroll(self, clicks: int, x: int = None, y: int = None) -> bool:
        return self._send_command(f"SCROLL:{clicks}")
    
    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
             duration: float = 0.5) -> bool:
        return self._send_command(f"DRAG:{start_x},{start_y},{end_x},{end_y}")
    
    def get_current_position(self) -> Tuple[int, int]:
        return (0, 0)
    
    def press_key(self, key: str) -> bool:
        return self._send_command(f"KEY:{key}")
    
    def type_text(self, text: str, interval: float = 0.05) -> bool:
        return self._send_command(f"TYPE:{text}")
    
    def key_down(self, key: str) -> bool:
        return self._send_command(f"KEYDOWN:{key}")
    
    def key_up(self, key: str) -> bool:
        return self._send_command(f"KEYUP:{key}")
    
    def hotkey(self, *keys) -> bool:
        keys_str = "+".join(keys)
        return self._send_command(f"HOTKEY:{keys_str}")
    
    def screenshot(self, region: Tuple[int, int, int, int] = None) -> Optional[bytes]:
        return None
