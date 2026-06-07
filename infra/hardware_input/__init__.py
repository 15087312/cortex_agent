"""
硬件输入控制基础设施

提供鼠标键盘硬件控制的统一接口
"""
from infra.hardware_input.controller import (
    HardwareInputController,
    PyAutoGUIController,
    SerialController
)

__all__ = [
    "HardwareInputController",
    "PyAutoGUIController",
    "SerialController"
]
