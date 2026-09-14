"""防残留 — 父进程/后端进程存活监控

背景: `cortex` 用 subprocess 拉起 uvicorn 后端 / 前端服务等子进程。
若 cortex 被强杀（kill -9 / 崩溃），SIGINT/SIGTERM 处理器不会执行，
子进程会变成孤儿持续占用内存和端口。

原理（两层）:
1. 父进程监控: cortex 启动子进程时注入 CORTEX_PARENT_PID 环境变量（指向自己）。
   各子进程导入本模块后，启动一个守护线程定期用 os.kill(pid, 0) 探测
   父进程是否存活；父进程消失则 os._exit(0) 自杀。
2. 后端锚定: 后端启动时把自身 PID 写入 ~/.cortex/backend_pid.json，
   所有卫星进程（前端代理/Qt/桌宠/截图 daemon/MCP server 等）通过
   CORTEX_BACKEND_PID 环境变量或 PID 文件绑定后端；后端消失则自动退出。
   这样无论从哪里启动（cortex / Qt / 手动 / 开机自启），只要后端死了，
   所有卫星进程都会跟着退出，不会残留。

后端锚定的优先级: CORTEX_BACKEND_PID 环境变量 > ~/.cortex/backend_pid.json。
- 环境变量由启动方（cortex / Qt）在拉起子进程时注入；
- PID 文件由后端 lifespan 启动时写入、退出时删除 —— 后端被 kill -9 时
  文件会残留，因此绑定线程在探测到「进程不存在」时才自杀，探测到
  「PID 被别的进程复用」时重新读取文件（后端正常重启的场景）。

特性: 两者都不可用时自动禁用（手动独立运行不受影响）。
"""
import json
import os
import sys
import threading
import time

_ENV_KEY = "CORTEX_PARENT_PID"
_BACKEND_ENV_KEY = "CORTEX_BACKEND_PID"
_BACKEND_PID_FILE = os.path.join(os.path.expanduser("~"), ".cortex", "backend_pid.json")
_DEFAULT_INTERVAL = 2.0

_started = False
_bound_lock = threading.Lock()
_bound = False


def _parent_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _read_backend_pid_file() -> int:
    """读 ~/.cortex/backend_pid.json，返回后端 PID（缺失/损坏返回 0）"""
    try:
        with open(_BACKEND_PID_FILE, "r", encoding="utf-8") as f:
            return int(json.load(f).get("pid") or 0)
    except Exception:
        return 0


def write_backend_pid_file(pid: int) -> None:
    """后端启动时调用：把自身 PID 写入发现文件"""
    try:
        os.makedirs(os.path.dirname(_BACKEND_PID_FILE), exist_ok=True)
        with open(_BACKEND_PID_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": int(pid), "ts": time.time()}, f)
    except Exception:
        pass


def clear_backend_pid_file(pid: int = 0) -> None:
    """后端正常退出时调用：删除发现文件（仅当文件里的 PID 仍是自己时）"""
    try:
        current = _read_backend_pid_file()
        if pid and current and current != pid:
            return  # 文件已被新后端接管，不删
        os.unlink(_BACKEND_PID_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _resolve_backend_pid() -> int:
    """解析要绑定的后端 PID：环境变量优先，其次 PID 文件。0 = 不可用"""
    raw = os.environ.get(_BACKEND_ENV_KEY, "").strip()
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return _read_backend_pid_file()


def _bind_watch(backend_pid: int, interval: float) -> None:
    """绑定线程主体：后端死亡 → 自杀；PID 被复用 → 跟随新 PID"""
    current = backend_pid
    while True:
        time.sleep(interval)
        if _parent_alive(current):
            continue
        # 探测的 PID 已不存在。若是环境变量绑定的，直接自杀（精确）。
        if os.environ.get(_BACKEND_ENV_KEY, "").strip():
            os._exit(0)
            return  # 测试中 os._exit 被 mock 时由此退出循环
        # PID 文件绑定的：可能是后端被强杀（残留文件），也可能后端已正常
        # 重启到新 PID（文件被新后端覆盖）。重读文件跟随新后端。
        new_pid = _read_backend_pid_file()
        if new_pid and new_pid != current:
            current = new_pid
            continue
        os._exit(0)
        return  # 同上，仅为可测试性


def bind_backend(interval: float = _DEFAULT_INTERVAL, fail_after: float = 30.0) -> bool:
    """卫星进程调用：绑定后端进程，后端退出/被强杀时自动退出。

    优先级: CORTEX_BACKEND_PID 环境变量 > ~/.cortex/backend_pid.json。
    两者都不可用（后端未运行且无环境变量）时:
    - fail_after > 0: 等待后端启动，超时后退出（给「先后端后卫星」的启动顺序兜底）
    - fail_after <= 0: 不启用绑定（保持现状，独立运行）
    已绑定则直接返回。
    """
    global _bound
    with _bound_lock:
        if _bound:
            return True
        backend_pid = _resolve_backend_pid()
        if backend_pid <= 1 or backend_pid == os.getpid():
            if fail_after <= 0:
                return False
            # 等待后端启动（定期重读文件），超时放弃
            deadline = time.time() + fail_after
            while time.time() < deadline:
                time.sleep(0.5)
                backend_pid = _resolve_backend_pid()
                if backend_pid > 1:
                    break
            if backend_pid <= 1 or backend_pid == os.getpid():
                return False
        _bound = True
    threading.Thread(
        target=_bind_watch, args=(backend_pid, interval), daemon=True, name="backend-watchdog"
    ).start()
    return True


def enable(interval: float = _DEFAULT_INTERVAL) -> bool:
    """按 CORTEX_PARENT_PID 环境变量启用父进程监控。已启用则直接返回。"""
    global _started
    if _started:
        return True
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        return False
    try:
        ppid = int(raw)
    except (TypeError, ValueError):
        return False
    if ppid <= 1 or ppid == os.getpid():
        return False

    def _watch() -> None:
        while True:
            time.sleep(interval)
            if not _parent_alive(ppid):
                # 静默退出，不打印（避免 Ctrl+C 停止时多出日志）
                os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="orphan-watchdog").start()
    _started = True
    return True


def require_cortex_sys_path() -> bool:
    """脚本直跑时把项目根加进 sys.path（让 `import cortex` / `import utils` 可用）。

    server.py / main.py / pet_launch.py 等以 `python frontend/xxx.py` 直跑时
    sys.path[0] 是 frontend/，项目根不在路径上 —— 之前的 watchdog import
    被 try/except 静默吞掉，防残留从未生效。返回是否修改了 sys.path。
    """
    if "cortex.watchdog" in sys.modules:
        return False
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        if root not in sys.path:
            sys.path.insert(0, root)
        return True
    except Exception:
        return False
