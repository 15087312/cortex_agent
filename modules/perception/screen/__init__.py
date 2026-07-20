"""
屏幕理解模块 — 统一 UI 元素检测

提供自动检测和路由：
- 原生应用 → touchpoint 无障碍 API
- Chromium 应用 → CDP 或视觉模型
- 信息不足 → 视觉模型补充
"""
from modules.perception.screen.context import ScreenContext, UIElement
from modules.perception.screen.router import DetectorRouter, get_detector_router

__all__ = [
    "ScreenContext",
    "UIElement",
    "DetectorRouter",
    "get_detector_router",
]
