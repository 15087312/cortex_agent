"""卫星进程看门狗 — 实现已下沉 infra.watchdog。

为什么搬家
    本模块是**纯基础设施**（只依赖标准库，零项目内 import），但实际消费者是
    api、infra.mcp、infra.screen_capture_daemon、frontend —— cortex 自身并不用。
    它留在 cortex 下会让 infra → cortex 出现反向依赖，与 cortex → infra
    构成逻辑循环（infra 应当是依赖图的最底层）。

    下沉后 infra 内部自洽，cortex 只经转发壳取用。

此处保留同名转发，既有 `from cortex.watchdog import ...` 调用点无需改动；
新增调用方请直接用 infra.watchdog。
"""
from infra.watchdog import (  # noqa: F401
    bind_backend,
    clear_backend_pid_file,
    enable,
    write_backend_pid_file,
)

__all__ = ["bind_backend", "clear_backend_pid_file", "enable", "write_backend_pid_file"]
