"""
JSON 工具 - 序列化、反序列化、格式化
"""
import json
from typing import Any, Dict, List
from datetime import datetime, date


class DateTimeEncoder(json.JSONEncoder):
    """日期时间编码器"""
    
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


def serialize(obj: Any) -> str:
    """序列化对象为 JSON 字符串"""
    return json.dumps(obj, cls=DateTimeEncoder, ensure_ascii=False)


def deserialize(json_str: str) -> Any:
    """反序列化 JSON 字符串"""
    return json.loads(json_str)


def format_json(obj: Any, indent: int = 2) -> str:
    """格式化 JSON"""
    return json.dumps(obj, indent=indent, ensure_ascii=False, cls=DateTimeEncoder)
