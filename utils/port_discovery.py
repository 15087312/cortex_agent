"""端口发现与自动回退 — 后端启动时选择端口并落盘，消费方据此连接。

背景：8080 可能被残留进程占用，写死端口会导致 "address already in use" 崩溃。
方案：
- 显式指定 SERVER_PORT 且空闲 → 用指定端口；
- 否则交给操作系统分配任意空闲端口（bind 0，无固定范围）。
实际端口写入 ~/.cortex/backend_port.json；前端代理(server.py)/WebSocket/pet 读取。
"""
import json
import os
import socket
import urllib.request

_PORT_FILE = os.path.join(os.path.expanduser("~"), ".cortex", "backend_port.json")
_HEALTH_TIMEOUT = 0.6


def _is_free(port: int) -> bool:
    """端口是否可绑定（空闲）"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def _pick_ephemeral() -> int:
    """向 OS 申请一个任意空闲端口（bind 0 → 内核分配，无固定范围）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pick_free_port(preferred: int = 8080) -> int:
    """首选端口空闲则用之；否则交给 OS 分配任意空闲端口"""
    if preferred and _is_free(preferred):
        return preferred
    return _pick_ephemeral()


def save_backend_port(port: int) -> None:
    """把当前后端端口写入发现文件（供前端代理/WS/pet 读取）"""
    try:
        os.makedirs(os.path.dirname(_PORT_FILE), exist_ok=True)
        with open(_PORT_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": int(port)}, f)
    except Exception:
        pass


def read_backend_port(default: int = 8080) -> int:
    """从发现文件读取后端端口；文件缺失/损坏返回 default"""
    try:
        with open(_PORT_FILE, "r", encoding="utf-8") as f:
            return int(json.load(f).get("port") or default)
    except Exception:
        return default


def probe_health(port: int, timeout: float = _HEALTH_TIMEOUT) -> bool:
    """探测该端口是否有一个健康的 Cortex 后端（GET /health）"""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/health", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False
