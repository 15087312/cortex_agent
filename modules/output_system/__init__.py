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
from modules.security_system import SecurityLevel
from modules.security_system.validators import CoreValidator, ContentValidator, OutputValidator

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
    # 安全
    "SecurityLevel",
    "CoreValidator",
    "ContentValidator",
    "OutputValidator"
]
