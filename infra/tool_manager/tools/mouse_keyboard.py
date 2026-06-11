"""
鼠标键盘工具 - 调用基础设施层的硬件控制器

通过 ToolRegistry 注册为可调用的工具
具体实现委托给 infra.hardware_input 模块
"""
from infra.tool_manager.tool_registry import ToolRegistry
from infra.hardware_input import PyAutoGUIController

# 全局控制器实例
_controller = PyAutoGUIController()


@ToolRegistry.register(
    "mouse_move",
    description="移动鼠标到指定坐标",
    params={"x": "X坐标", "y": "Y坐标", "duration": "移动持续时间(秒)"}
)
def mouse_move(x: int, y: int, duration: float = 0.3) -> str:
    """移动鼠标"""
    success = _controller.move_to(x, y, duration)
    if success:
        return f"鼠标移动到 ({x}, {y})"
    return "鼠标移动失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "mouse_click",
    description="点击鼠标",
    params={
        "x": "X坐标(可选)", 
        "y": "Y坐标(可选)",
        "button": "按键(left/right/middle)",
        "clicks": "点击次数"
    }
)
def mouse_click(x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> str:
    """鼠标点击"""
    success = _controller.click(x, y, button, clicks)
    if success:
        pos_str = f"({x}, {y})" if x is not None and y is not None else "当前位置"
        return f"鼠标 {button} 键点击 {clicks} 次 at {pos_str}"
    return "鼠标点击失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "mouse_double_click",
    description="双击鼠标",
    params={"x": "X坐标(可选)", "y": "Y坐标(可选)", "button": "按键(left/right)"}
)
def mouse_double_click(x: int = None, y: int = None, button: str = "left") -> str:
    """双击鼠标"""
    success = _controller.click(x, y, button, 2)
    if success:
        pos_str = f"({x}, {y})" if x is not None and y is not None else "当前位置"
        return f"鼠标 {button} 键双击 at {pos_str}"
    return "双击失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "mouse_scroll",
    description="滚动鼠标滚轮",
    params={"clicks": "滚动单位(正数向上，负数向下)", "x": "X坐标(可选)", "y": "Y坐标(可选)"}
)
def mouse_scroll(clicks: int, x: int = None, y: int = None) -> str:
    """滚动鼠标"""
    success = _controller.scroll(clicks, x, y)
    if success:
        direction = "向上" if clicks > 0 else "向下"
        return f"鼠标滚轮{direction}滚动 {abs(clicks)} 单位"
    return "滚动失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "mouse_drag",
    description="拖拽鼠标",
    params={
        "start_x": "起始X坐标",
        "start_y": "起始Y坐标",
        "end_x": "结束X坐标",
        "end_y": "结束Y坐标",
        "duration": "拖拽持续时间(秒)"
    }
)
def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> str:
    """拖拽鼠标"""
    success = _controller.drag(start_x, start_y, end_x, end_y, duration)
    if success:
        return f"鼠标拖拽: ({start_x},{start_y}) → ({end_x},{end_y})"
    return "拖拽失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "keyboard_type",
    description="输入文本",
    params={"text": "要输入的文本", "interval": "字符间隔时间(秒)"}
)
def keyboard_type(text: str, interval: float = 0.05) -> str:
    """键盘输入"""
    success = _controller.type_text(text, interval)
    if success:
        preview = text[:50] + "..." if len(text) > 50 else text
        return f"键盘输入: {preview}"
    return "文本输入失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "keyboard_press",
    description="按下键盘按键",
    params={"key": "按键名称(如 enter, tab, escape, ctrl, alt, shift等)"}
)
def keyboard_press(key: str) -> str:
    """键盘按键"""
    success = _controller.press_key(key)
    if success:
        return f"按键: {key}"
    return f"按键 {key} 失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "keyboard_hotkey",
    description="按下组合键",
    params={"keys": "按键列表(如 ['ctrl', 'c']，或单个键如 'enter')"}
)
def keyboard_hotkey(keys: list = None, key: str = None) -> str:
    """组合键

    接受 keys 或 key 参数，兼容模型传错参数名的情况。
    - keys=['command', 'l'] — 组合键
    - key='enter' — 单个键（自动转为 [key]）
    """
    if keys is None and key is not None:
        keys = [key]
    if not isinstance(keys, list):
        keys = [keys]
    if not keys or (len(keys) == 1 and keys[0] is None):
        return "[错误] 请指定按键，如 keyboard_hotkey(keys=['enter']) 或 keyboard_hotkey(key='enter')"
    success = _controller.hotkey(*keys)
    if success:
        return f"组合键: {'+'.join(keys)}"
    return f"组合键 {'+'.join(keys)} 失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "get_mouse_position",
    description="获取当前鼠标位置",
    params={}
)
def get_mouse_position() -> str:
    """获取鼠标位置"""
    x, y = _controller.get_current_position()
    return f"当前鼠标位置: ({x}, {y})"
