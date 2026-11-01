"""工具可视化事件（Phase 0/2）：脱敏/预览/载荷 + 卡片 meta/view + ModelRunner 推送。"""
import json
from types import SimpleNamespace

from modules.thinking.core.model_runner import ModelRunner
from modules.thinking.tool_events import (
    STATUS_DENIED,
    STATUS_ERROR,
    STATUS_RUNNING,
    STATUS_TIMEOUT,
    build_call_payload,
    build_result_payload,
    preview,
    sanitize_args,
)


# ── 参数脱敏 / 截断 ────────────────────────────────────────────────

def test_sanitize_args_masks_credentials():
    cleaned = sanitize_args({
        "api_key": "sk-123",
        "Authorization": "Bearer x",
        "nested": {"db_password": "p", "path": "a.py"},
        "command": "pytest -q",
    })
    assert cleaned["api_key"] == "***"
    assert cleaned["Authorization"] == "***"
    assert cleaned["nested"]["db_password"] == "***"
    assert cleaned["nested"]["path"] == "a.py"
    assert cleaned["command"] == "pytest -q"


def test_sanitize_args_truncates_long_values_and_lists():
    cleaned = sanitize_args({"content": "x" * 1000, "items": list(range(50))})
    assert cleaned["content"].endswith("chars)")
    assert len(cleaned["content"]) < 260
    assert cleaned["items"][-1].startswith("…(+")
    assert len(cleaned["items"]) == 21


def test_sanitize_args_caps_whole_object_size():
    cleaned = sanitize_args({f"k{i}": "v" * 50 for i in range(40)})
    assert isinstance(cleaned, str)
    assert "chars)" in cleaned


def test_sanitize_args_handles_non_dict_and_none():
    assert sanitize_args(None) == {}
    assert sanitize_args("{bad json") == "{bad json"


def test_preview_keeps_short_and_marks_truncation():
    assert preview("abc") == "abc"
    long = preview("a" * 30, limit=10)
    assert long.startswith("a" * 10)
    assert "(+20 chars)" in long


# ── 事件载荷 ──────────────────────────────────────────────────────

def test_build_call_payload_shape():
    data = build_call_payload(
        "call-1", "read_file", {"path": "a.py", "api_key": "k"},
        tier="expert", model_id="m1", identity_name="实现专家",
    )
    assert data["phase"] == "call"
    assert data["status"] == STATUS_RUNNING
    assert data["call_id"] == "call-1"
    assert data["name"] == "read_file"
    assert data["args"]["api_key"] == "***"
    assert data["tier"] == "expert"
    assert data["identity_name"] == "实现专家"
    assert "duration_ms" in data
    # Phase 2：调用瞬间就带卡片骨架（read 卡片）
    assert data["view"]["card"] == "read"
    assert data["view"]["path"] == "a.py"


def test_build_call_payload_unknown_tool_has_no_view():
    data = build_call_payload("c", "calc", {"expression": "1+1"})
    assert "view" not in data


def test_build_call_payload_terminal_view():
    data = build_call_payload("c", "exec_command", {"command": "pytest -q"})
    assert data["view"]["card"] == "terminal"
    assert data["view"]["title"] == "pytest -q"


def test_build_result_payload_shape():
    data = build_result_payload(
        "call-1", "exec_command", "line1\nline2",
        status=STATUS_ERROR, duration_ms=12.345, error="exit 1",
    )
    assert data["phase"] == "result"
    assert data["status"] == STATUS_ERROR
    assert data["duration_ms"] == 12.3
    assert data["result_chars"] == len("line1\nline2")
    assert data["result_preview"] == "line1\nline2"
    assert data["error"] == "exit 1"


def test_build_result_payload_preview_is_bounded():
    data = build_result_payload("c", "read_file", "x" * 1000,
                                status=STATUS_TIMEOUT, error="e" * 500)
    assert len(data["result_preview"]) < 450
    assert len(data["error"]) < 350


def test_build_result_payload_carries_meta_and_view():
    result = str({"stdout": "ok", "exit_code": 0, "elapsed_seconds": 0.1})
    data = build_result_payload(
        "c", "exec_command", result, args={"command": "ls", "workdir": "/repo"},
    )
    assert data["meta"]["exit_code"] == 0
    assert data["meta"]["cwd"] == "/repo"
    assert data["view"]["card"] == "terminal"
    assert data["view"]["output"] == "ok"


def test_build_result_payload_write_diff_view():
    result = str({"path": "/repo/x.py", "action": "updated", "diff": ["-a", "+b"]})
    data = build_result_payload("c", "write_text_file", result, args={"path": "/repo/x.py"})
    assert data["view"]["card"] == "diff"
    assert data["view"]["additions"] == 1
    assert data["view"]["deletions"] == 1


def test_build_result_payload_unknown_tool_has_no_view_or_meta():
    data = build_result_payload("c", "calc", "2")
    assert "view" not in data
    assert "meta" not in data


def test_read_payload_fits_event_data_cap():
    """meta + view 是重复承载，整体必须仍小于 session_events 的 data 上限。"""
    from modules.database.session_events import _MAX_DATA_CHARS
    content = "\n".join("x" * 200 for _ in range(500))
    data = build_result_payload(
        "c", "read_text_file", str({"path": "/a/b.py", "content": content}),
        args={"path": "/a/b.py"},
    )
    assert len(json.dumps(data, ensure_ascii=False)) < _MAX_DATA_CHARS


def test_result_payload_without_result():
    data = build_result_payload("c", "todo", None, status=STATUS_DENIED)
    assert data["result_chars"] == 0
    assert data["result_preview"] == ""


# ── ModelRunner 推送 ──────────────────────────────────────────────

class _FakeConnections:
    def __init__(self):
        self.sent = []

    def send_json_from_thread(self, session_id, event):
        self.sent.append((session_id, event))
        return True


def _runner():
    r = ModelRunner.__new__(ModelRunner)
    r.session_id = "sess-1"
    r.tier = "expert"
    r.model_id = "m1"
    r.identity = SimpleNamespace(name="实现专家", role="expert")
    return r


def test_push_tool_call_envelope(monkeypatch):
    import modules.thinking.api_stream as api
    fake = _FakeConnections()
    monkeypatch.setattr(api, "connection_manager", fake)
    tc = SimpleNamespace(id="call-1", name="read_file")

    _runner()._push_tool_call(tc, {"path": "a.py"})

    sid, env = fake.sent[0]
    assert sid == "sess-1"
    assert env["type"] == "tool"
    assert env["event"] == "tool_call"
    assert env["role"] == "expert"
    assert env["data"]["call_id"] == "call-1"
    assert env["data"]["args"]["path"] == "a.py"
    assert env["data"]["identity_name"] == "实现专家"


def test_push_tool_result_falls_back_to_name_as_call_id(monkeypatch):
    import modules.thinking.api_stream as api
    fake = _FakeConnections()
    monkeypatch.setattr(api, "connection_manager", fake)
    tc = SimpleNamespace(name="web_search")  # 无 id

    _runner()._push_tool_result(tc, "3 results", status=STATUS_ERROR,
                                duration_ms=9.9, error="boom")

    _, env = fake.sent[0]
    assert env["event"] == "tool_result"
    assert env["data"]["call_id"] == "web_search"
    assert env["data"]["status"] == STATUS_ERROR
    assert env["data"]["error"] == "boom"
    assert env["data"]["duration_ms"] == 9.9


def test_push_tool_event_swallows_send_failure(monkeypatch):
    import modules.thinking.api_stream as api

    class _Boom:
        def send_json_from_thread(self, *a, **k):
            raise RuntimeError("ws down")

    monkeypatch.setattr(api, "connection_manager", _Boom())
    tc = SimpleNamespace(id="c", name="todo")
    _runner()._push_tool_call(tc, {})  # 不抛
