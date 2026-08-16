"""utils/json_utils 测试：DateTimeEncoder / 序列化 / 反序列化 / 格式化"""
import json
from datetime import datetime, date

import pytest

from utils.json_utils import DateTimeEncoder, serialize, deserialize, format_json


def test_datetime_encoder_datetime():
    assert DateTimeEncoder().default(datetime(2026, 1, 1, 10, 30)) == "2026-01-01T10:30:00"


def test_datetime_encoder_date():
    assert DateTimeEncoder().default(date(2026, 1, 1)) == "2026-01-01"


def test_datetime_encoder_unsupported():
    with pytest.raises(TypeError):
        DateTimeEncoder().default(object())


def test_serialize_datetime():
    out = serialize({"t": datetime(2026, 1, 1, 10, 0)})
    assert '"2026-01-01T10:00:00"' in out


def test_serialize_unicode():
    assert serialize({"a": "中文"}) == '{"a": "中文"}'


def test_deserialize_object():
    assert deserialize('{"a": 1}') == {"a": 1}


def test_deserialize_list():
    assert deserialize("[1, 2]") == [1, 2]


def test_format_json_indent():
    out = format_json({"a": 1})
    assert '"a"' in out and "\n" in out


def test_format_json_datetime():
    out = format_json({"t": date(2026, 1, 1)})
    assert '"2026-01-01"' in out


def test_roundtrip():
    obj = {"x": [1, 2], "t": datetime(2026, 1, 1)}
    assert deserialize(serialize(obj))["x"] == [1, 2]
    assert deserialize(serialize(obj))["t"] == "2026-01-01T00:00:00"
