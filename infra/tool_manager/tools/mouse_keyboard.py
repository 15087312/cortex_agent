"""
鼠标键盘工具 - 调用基础设施层的硬件控制器

通过 ToolRegistry 注册为可调用的工具
具体实现委托给 infra.hardware_input 模块
"""
from infra.tool_manager.tool_registry import ToolRegistry
from infra.hardware_input import PyAutoGUIController
from utils.logger import setup_logger

logger = setup_logger("mouse_keyboard")

# 全局控制器实例
_controller = PyAutoGUIController()


@ToolRegistry.register(
    "mouse_move",
    description="移动鼠标到指定坐标",
    params={"x": "X坐标", "y": "Y坐标", "duration": "移动持续时间(秒)"},
    core=True,
)
def mouse_move(x: int, y: int, duration: float = 0.3) -> str:
    """移动鼠标"""
    success = _controller.move_to(x, y, duration)
    if success:
        return f"鼠标移动到 ({x}, {y})"
    return "鼠标移动失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "mouse_click",
    description="点击鼠标。结合 detect_ui_elements 使用：先用 detect_ui_elements 获取元素坐标，再用 mouse_click(x=center_x, y=center_y) 点击",
    params={
        "x": "X坐标(可选)",
        "y": "Y坐标(可选)",
        "button": "按键(left/right/middle)",
        "clicks": "点击次数"
    },
    core=True,
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
    params={"x": "X坐标(可选)", "y": "Y坐标(可选)", "button": "按键(left/right)"},
    core=True,
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
    params={"clicks": "滚动单位(正数向上，负数向下)", "x": "X坐标(可选)", "y": "Y坐标(可选)"},
    core=True,
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
    },
    core=True,
)
def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> str:
    """拖拽鼠标"""
    success = _controller.drag(start_x, start_y, end_x, end_y, duration)
    if success:
        return f"鼠标拖拽: ({start_x},{start_y}) → ({end_x},{end_y})"
    return "拖拽失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "keyboard_type",
    description="输入文本。先用 mouse_click 聚焦输入框，再用此工具输入。中文自动使用剪贴板粘贴。",
    params={"text": "要输入的文本", "interval": "字符间隔时间(秒)"},
    core=True,
)
def keyboard_type(text: str, interval: float = 0.05) -> str:
    """键盘输入

    非 ASCII 文本（中文/日文/符号）使用剪贴板+Cmd+V，
    因为 pyautogui.write() 无法正确输入非英文字符。
    """
    if not text:
        return "[错误] 请输入要输入的文本"

    if any(ord(c) > 127 for c in text):
        return _type_via_clipboard(text)
    else:
        success = _controller.type_text(text, interval)
        if success:
            preview = text[:50] + "..." if len(text) > 50 else text
            return f"键盘输入: {preview}"
        return "文本输入失败，请检查硬件控制器状态"


def _type_via_clipboard(text: str) -> str:
    """通过剪贴板输入文本：复制到剪贴板 → 粘贴快捷键

    适用于中文/日文等非 ASCII 文本，避免依赖系统输入法。
    """
    import sys as _sys
    try:
        import subprocess
        if _sys.platform == "win32":
            proc = subprocess.run(
                ["clip"], input=text.encode("utf-16-le") + b"\x00\x00",
                capture_output=True, timeout=5,
            )
            modifier = "ctrl"
        elif _sys.platform == "darwin":
            proc = subprocess.run(
                ["pbcopy"], input=text.encode("utf-8"), capture_output=True, timeout=5
            )
            modifier = "command"
        else:
            try:
                proc = subprocess.run(
                    ["xclip", "-selection", "clipboard"], input=text.encode("utf-8"),
                    capture_output=True, timeout=5,
                )
            except FileNotFoundError:
                proc = subprocess.run(
                    ["xsel", "-b", "-i"], input=text.encode("utf-8"),
                    capture_output=True, timeout=5,
                )
            modifier = "ctrl"

        if proc.returncode != 0:
            success = _controller.type_text(text, interval=0.1)
            if success:
                return f"键盘输入: {text[:50]}"
            return "文本输入失败"

        _controller.hotkey(modifier, "v")
        preview = text[:50] + "..." if len(text) > 50 else text
        return f"键盘输入(剪贴板): {preview}"
    except Exception as e:
        logger.error(f"剪贴板输入失败: {e}")
        success = _controller.type_text(text, interval=0.1)
        if success:
            return f"键盘输入: {text[:50]}"
        return "文本输入失败"


@ToolRegistry.register(
    "keyboard_press",
    description="按下键盘按键，如 enter、tab、escape 等",
    params={"key": "按键名称(如 enter, tab, escape, ctrl, alt, shift等)"},
    core=True,
)
def keyboard_press(key: str = None, keys: list = None) -> str:
    """键盘按键

    接受 key 或 keys 参数，与其他键盘工具保持一致。
    - key='enter' — 单个键
    - keys=['enter'] — 等效
    """
    if key is None and keys is not None:
        if isinstance(keys, list) and len(keys) > 0:
            key = keys[0]
        elif isinstance(keys, str):
            key = keys
    if not key:
        return "[错误] 请指定按键，如 keyboard_press(key='enter')"
    success = _controller.press_key(key)
    if success:
        return f"按键: {key}"
    return f"按键 {key} 失败，请检查硬件控制器状态"


@ToolRegistry.register(
    "keyboard_hotkey",
    description="按下组合键，如 keyboard_hotkey(keys=['command', 'c']) 复制、['command', 'v'] 粘贴",
    params={"keys": "按键列表(如 ['ctrl', 'c']，或单个键如 'enter')"},
    core=True,
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
    params={},
    core=True,
)
def get_mouse_position() -> str:
    """获取鼠标位置"""
    x, y = _controller.get_current_position()
    return f"当前鼠标位置: ({x}, {y})"
