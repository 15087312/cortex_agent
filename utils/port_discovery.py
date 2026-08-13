"""端口发现与自动回退 — 后端启动时选空闲端口并落盘，消费方据此连接。

背景：8080 可能被残留进程占用，写死端口会导致 "address already in use" 崩溃。
方案：后端在 preferred..preferred+9 里选第一个空闲端口，写入
~/.cortex/backend_port.json；前端代理(server.py)/WebSocket/pet 读取该文件拿到真实端口。
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


def pick_free_port(preferred: int = 8080, max_tries: int = 10) -> int:
    """返回 [preferred, preferred+max_tries) 中第一个空闲端口；全被占则返回 preferred"""
    preferred = max(1, int(preferred))
    for p in range(preferred, preferred + max_tries):
        if _is_free(p):
            return p
    return preferred


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
