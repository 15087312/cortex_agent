"""API 请求日志（内存环形缓冲，供仪表盘展示）

记录最近的后端 API 请求（方法/路径/状态/时间），最多保留 200 条。
"""
import collections
import time

_api_request_log: collections.deque = collections.deque(maxlen=200)


def log_request(method: str, path: str, status: int, duration_ms: float = 0.0) -> None:
    _api_request_log.append({
        "time": time.strftime("%H:%M:%S"),
        "method": method,
        "path": path,
        "status": status,
        "ms": round(duration_ms, 1),
    })


def recent_requests(limit: int = 50) -> list:
    return list(_api_request_log)[-limit:]
