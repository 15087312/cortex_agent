"""event_reducer 解析方法测试（纯函数，此前 15% 覆盖）"""
from modules.memory.event_reducer import _parse_importance, EventReducer


def _reducer():
    return EventReducer.__new__(EventReducer)


def test_parse_importance_valid():
    assert _parse_importance(0.8) == 0.8
    assert _parse_importance(0.5) == 0.5
    assert _parse_importance(1.0) == 1.0


def test_parse_importance_invalid():
    assert _parse_importance("abc") == 0.4  # 回退默认
    assert _parse_importance(None) == 0.4
    assert _parse_importance(-1) == 0.0  # 负数被钳制到 0


def test_parse_response_valid_json():
    data = _reducer()._parse_response('{"events": [{"fact": "写了代码", "importance": 0.8}]}')
    assert "events" in data
    assert len(data["events"]) >= 1


def test_parse_response_with_code_fence():
    r = EventReducer.__new__(EventReducer)
    text = '```json\n{"events": [{"fact": "完成测试", "importance": 0.6}]}\n```'
    data = r._parse_response(text)
    assert "events" in data
    assert len(data["events"]) >= 1


def test_parse_response_garbage():
    data = _reducer()._parse_response("not json at all")
    assert "events" in data or isinstance(data, dict)
