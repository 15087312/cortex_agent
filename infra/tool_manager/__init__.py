"""
工具管理器模块

提供：
- ToolRegistry: 工具注册中心
- ToolManager: 工具管理器
- 工具装饰器: @ToolRegistry.register
"""
from .tool_registry import ToolRegistry, ToolInfo, register_tool
from .tool_manager import ToolManager, tool_manager, extract_json

# 获取工具管理器单例
_tool_manager_instance = None

def get_tool_manager() -> ToolManager:
    """获取工具管理器单例"""
    global _tool_manager_instance
    if _tool_manager_instance is None:
        _tool_manager_instance = tool_manager  # 使用已创建的实例
    return _tool_manager_instance

__all__ = [
    "ToolRegistry",
    "ToolInfo",
    "register_tool",
    "ToolManager",
    "tool_manager",
    "get_tool_manager",
    "extract_json"
]
