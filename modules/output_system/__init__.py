"""
输出系统 - 统一对外入口

提供：
- OutputSystem: 统一输出流水线
- InputController: 鼠标键盘控制
- UIInteractor: UI元素检测和交互
- OutputDistributor: 输出分发
"""
from .core import OutputSystem
from .input_controller import InputController, input_controller
from .ui_interactor import UIInteractor, ui_interactor
from .distributor import OutputDistributor

# 安全符号不在此转出（原 `from modules.security_system import SecurityLevel, ...`）。
# 该转出无任何消费方（全仓均直接 import modules.security_system.*），却让
# output_system → security_system 形成一条纯转出式的实依赖，L3 记为 E302。
# 需要这些符号请直接 `from modules.security_system... import ...`。

__all__ = [
    # 核心
    "OutputSystem",
    # 输入控制
    "InputController",
    "input_controller",
    # UI交互
    "UIInteractor",
    "ui_interactor",
    # 分发
    "OutputDistributor",
]
