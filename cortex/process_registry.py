"""后端子进程登记 — 实现已下沉 infra.process_registry。

为什么搬家
    `utils/power.py`（防休眠拉起 caffeinate 并登记）需要本模块，而
    `cortex/main.py` 需要 `utils.port_discovery`，于是形成 cortex ⇄ utils
    逻辑循环。本模块是**纯基础设施**：只依赖标准库，与 cortex 无任何关系，
    放在 cortex 下属于历史位置，不是设计意图（消费者另有 api、desktop_pet）。

    下沉 infra 后 utils 不再向上依赖 cortex，循环消失。

此处保留同名转发，既有 `from cortex.process_registry import register` 调用点
无需改动；新增调用方请直接用 infra.process_registry。
"""
from infra.process_registry import (  # noqa: F401
    register,
    unregister,
    snapshot,
    shutdown_all_children,
)

__all__ = ["register", "unregister", "snapshot", "shutdown_all_children"]
