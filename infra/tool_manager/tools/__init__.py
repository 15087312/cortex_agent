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

# 记忆/因果探针工具（deep_recall 等）随工具集启动注册，
# 使大模型在默认工具中直接可见（core=True，完整参数 schema）
try:
    import modules.thinking.probes.probe_tools  # noqa: F401  触发 @ToolRegistry.register
    _imported.append("probe_tools")
except Exception as e:
    logger.warning(f"探针工具注册失败: {e}")

# 加载分类记忆工具（位于 modules/memory/tools/）
# 旧架构遗留：classified_memory_tool 已在仓库重构时移除，其功能被
# memory_match / memory_score / memory_batch_filter / event_query 取代。
# 此处不再尝试导入，避免误导性告警日志。

# 启动时恢复 AI 自创工具（从 data/ai_tools.json 持久化存储）
try:
    from .ai_tools import restore_ai_tools
    restore_ai_tools()
except Exception as e:
    logger.warning(f"AI 自创工具恢复失败: {e}")

__all__ = _imported
