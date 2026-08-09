"""screen_capture_daemon 客户端 — 从常驻采集进程取帧

所有截图调用点应优先走本模块：连接 daemon socket 取最新帧（PNG bytes），
失败（daemon 未运行/超时/权限未授）时返回 None，由调用方回退本地截图。

避免每个进程各自调用 screencapture（macOS 每次调用都会触发一次
屏幕录制权限确认），收敛为 daemon 内一次授权、一次截图。
"""
import base64
import json
import os
import socket
import threading
import time
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("screen_capture_client")

SOCKET_PATH = os.environ.get(
    "CORTEX_SCREEN_CAPTURE_SOCKET",
    "/tmp/cortex_screen_capture.sock",
)
DEFAULT_TIMEOUT = 3.0  # 单次请求超时（秒）

# 避免高并发下反复 connect 失败刷日志
_last_warn_ts = [0.0]
_warn_lock = threading.Lock()


def _warn_throttled(msg: str):
    now = time.time()
    with _warn_lock:
        if now - _last_warn_ts[0] > 30.0:
            _last_warn_ts[0] = now
            logger.debug(msg)


def _rpc(method: str, params: dict = None, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    """向 daemon 发送一行 JSON 请求，读取一行响应。失败返回 None。"""
    if not os.path.exists(SOCKET_PATH):
        return None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(SOCKET_PATH)
            req = json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n"
            client.sendall(req.encode())
            with client.makefile("r") as f:
                line = f.readline()
            if not line:
                return None
            return json.loads(line)
        finally:
            try:
                client.close()
            except OSError:
                pass
    except Exception as e:
        _warn_throttled(f"daemon 通信失败: {e}")
        return None


def ping(timeout: float = DEFAULT_TIMEOUT) -> bool:
    """健康检查：daemon 是否在线"""
    resp = _rpc("ping", timeout=timeout)
    return bool(resp and "result" in resp and resp["result"].get("ok"))


def get_frame_bytes(
    max_width: int = 1280,
    region: tuple = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[bytes]:
    """从 daemon 取最新帧，返回 PNG bytes；失败返回 None。"""
    params = {"max_width": max_width}
    if region and len(region) == 4:
        params["region"] = list(region)
    resp = _rpc("frame", params, timeout=timeout)
    if not resp or "result" not in resp:
        return None
    try:
        return base64.b64decode(resp["result"]["png"])
    except Exception:
        return None


def get_frame_base64(
    max_width: int = 1280,
    region: tuple = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """从 daemon 取最新帧，返回 base64 PNG；失败返回 None。"""
    params = {"max_width": max_width}
    if region and len(region) == 4:
        params["region"] = list(region)
    resp = _rpc("frame", params, timeout=timeout)
    if not resp or "result" not in resp:
        return None
    return resp["result"].get("png")
