"""
记忆系统共享工具函数
"""
from typing import Dict, Any


def safe_timestamp(data: Dict[str, Any]) -> float:
    """
    安全提取时间戳，兼容 dict/int/float/str 的异常数据。

    部分旧数据 timestamp 可能是 dict（如 {"start": 123, "end": 456}），
    直接用 > 比较会抛 TypeError。
    """
    ts = data.get("timestamp", 0)
    if isinstance(ts, dict):
        return float(ts.get("start", ts.get("created", 0)))
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0
