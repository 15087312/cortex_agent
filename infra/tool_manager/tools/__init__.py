"""
内置工具集 — 自动扫描并导入本目录下所有模块，触发 @ToolRegistry.register 装饰器。

设计意图：
  新增工具只需在 tools/ 下创建 .py 文件并使用 @ToolRegistry.register 装饰器，
  无需手动 import，自动发现并注册。
"""
import importlib
import pkgutil
from pathlib import Path

from utils.logger import get_logger
logger = get_logger(__name__)

_package_dir = Path(__file__).parent

_imported = []
for _module_info in pkgutil.iter_modules([str(_package_dir)]):
    if _module_info.name.startswith("_"):
        continue
    importlib.import_module(f".{_module_info.name}", package=__name__)
    _imported.append(_module_info.name)

# 加载分类记忆工具（位于 modules/memory/tools/）
try:
    importlib.import_module("modules.memory.tools.classified_memory_tool")
except Exception as e:
    logger.warning(f"分类记忆工具加载失败: {e}")

# 启动时恢复 AI 自创工具（从 data/ai_tools.json 持久化存储）
try:
    from .ai_tools import restore_ai_tools
    restore_ai_tools()
except Exception as e:
    logger.warning(f"AI 自创工具恢复失败: {e}")

__all__ = _imported
