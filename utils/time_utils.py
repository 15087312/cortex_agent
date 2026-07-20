"""
时间工具 - 时间戳转换、时间范围计算
"""
from datetime import datetime, timedelta, timezone
from typing import Optional


def now() -> datetime:
    """获取当前时间（UTC）"""
    return datetime.now(timezone.utc)


def timestamp_to_datetime(timestamp: float) -> datetime:
    """时间戳转 datetime（UTC）"""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def datetime_to_timestamp(dt: datetime) -> float:
    """datetime 转时间戳"""
    return dt.timestamp()


def format_datetime(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化时间"""
    return dt.strftime(fmt)


def parse_datetime(date_str: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> datetime:
    """解析时间字符串"""
    return datetime.strptime(date_str, fmt)


def time_range(
    start: datetime,
    end: datetime,
    step_minutes: int = 5
) -> list:
    """生成时间范围"""
    result = []
    current = start
    while current <= end:
        result.append(current)
        current += timedelta(minutes=step_minutes)
    return result


def get_start_of_day(dt: Optional[datetime] = None) -> datetime:
    """获取一天的开始"""
    if dt is None:
        dt = now()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def get_end_of_day(dt: Optional[datetime] = None) -> datetime:
    """获取一天的结束"""
    if dt is None:
        dt = now()
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
