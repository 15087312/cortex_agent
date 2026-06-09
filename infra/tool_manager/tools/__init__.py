"""
内置工具集 — 自动扫描并导入本目录下所有模块，触发 @ToolRegistry.register 装饰器。

新增工具只需在 tools/ 下创建 .py 文件并使用 @ToolRegistry.register 装饰器，无需手动 import。
"""
import importlib
import pkgutil
from pathlib import Path

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
except Exception:
    pass

__all__ = _imported
