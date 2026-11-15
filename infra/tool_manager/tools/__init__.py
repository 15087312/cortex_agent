"""
内置工具集 — 自动扫描并导入本目录下所有模块，触发 @ToolRegistry.register 装饰器。

设计意图：
  新增工具只需在 tools/ 下创建 .py 文件并使用 @ToolRegistry.register 装饰器，
  无需手动 import，自动发现并注册。

这里是 import 期注册的**唯一**允许范围：内置工具声明自己拥有的槽，随模块 import
一次生效、进程生命周期内不换。凡是「持久化的 / 模型自己写的 / 需要能卸掉」的
注册都不许写在这里——import 期的副作用不可撤销，见文件末尾两条迁移记录。
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

# 记忆/因果探针工具（deep_recall 等）注册已迁移至 bootstrap.register_business_capabilities()
# （装配层触发 import，使 infra 不再依赖 modules.thinking.probes）。

# 加载分类记忆工具（位于 modules/memory/tools/）
# 旧架构遗留：classified_memory_tool 已在仓库重构时移除，其功能被
# memory_match / memory_score / memory_batch_filter / event_query 取代。
# 此处不再尝试导入，避免误导性告警日志。

# AI 自创工具的恢复（读 data/ai_tools.json 并 exec 模型自己写的源码）同样已移到
# bootstrap.register_business_capabilities()：import 期做的注册不可撤销，而且它把
# 「谁覆盖了谁」交给了一次不保证顺序的副作用。

__all__ = _imported
