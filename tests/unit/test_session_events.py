"""会话事件日志（Phase 1）测试 — 真实 SQLite

覆盖：seq 分配、持久/瞬态分流、分页、重启后水位对齐、清理、截断，
以及 api_stream._record_event 的落库与兜底路径。
"""
import threading

import pytest

import modules.database.connection as conn
import modules.database.session_events as se


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "events.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(se, "get_db_manager", lambda: dm)
    return se.SessionEventRepository()


def _envelope(msg_type="tool", event="tool_call", data=None, content=""):
    return {
        "type": msg_type,
        "event": event,
        "session_id": "s1",
        "role": "expert",
        "content": content,
        "data": data or {},
        "timestamp": 0.0,
    }


# ── seq 分配 ──────────────────────────────────────────────────────────────

def test_seq_starts_at_one_and_increments(repo):
    assert repo.append("s1", type="tool", event="tool_call") == 1
    assert repo.append("s1", type="tool", event="tool_result") == 2
    assert repo.last_seq("s1") == 2


def test_seq_isolated_per_session(repo):
    assert repo.append("a", type="tool", event="tool_call") == 1
    assert repo.append("b", type="tool", event="tool_call") == 1
    assert repo.last_seq("a") == 1
    assert repo.last_seq("b") == 1


def test_seq_survives_new_repository_instance(repo, monkeypatch):
    repo.append("s1", type="tool", event="tool_call")
    repo.append("s1", type="tool", event="tool_result")
    # 模拟进程重启：新实例没有内存水位，必须从库内最大值继续
    fresh = se.SessionEventRepository()
    assert fresh.append("s1", type="tool", event="tool_call") == 3


def test_append_envelope_sets_seq_and_returns_it(repo):
    env = _envelope()
    assert repo.append_envelope("s1", env) == 1
    assert env["_seq"] == 1


# ── 持久 / 瞬态分流 ───────────────────────────────────────────────────────

def test_transient_event_gets_seq_but_is_not_persisted(repo):
    assert repo.append_envelope("s1", _envelope(msg_type="progress")) == 1
    assert repo.list_events("s1") == []
    # 瞬态事件仍占用水位，后续持久事件 seq 更大（客户端按 seq 排序不乱）
    assert repo.append_envelope("s1", _envelope()) == 2
    assert [e["_seq"] for e in repo.list_events("s1")] == [2]


def test_durable_types_persisted_with_envelope_shape(repo):
    env = _envelope(data={"call_id": "c1", "name": "read_file", "args": {"path": "a.py"}})
    repo.append_envelope("s1", env)
    events = repo.list_events("s1")
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "tool"
    assert ev["event"] == "tool_call"
    assert ev["role"] == "expert"
    assert ev["data"]["call_id"] == "c1"
    assert ev["data"]["args"] == {"path": "a.py"}
    assert ev["_seq"] == 1


def test_message_and_thinking_are_durable(repo):
    for msg_type in ("thinking", "message", "user_message", "mental", "error", "done", "todo"):
        repo.append_envelope("s1", _envelope(msg_type=msg_type))
    assert len(repo.list_events("s1")) == 7


def test_chunk_is_not_durable(repo):
    """流式正文增量（chunk）只是预览：不落库，刷新后以 message 为准"""
    repo.append_envelope("s1", _envelope(msg_type="chunk", event="chunk_delta"))
    assert repo.list_events("s1") == []


def test_ack_and_ping_are_not_durable(repo):
    for msg_type in ("ack", "ping", "thinking_progress"):
        repo.append_envelope("s1", _envelope(msg_type=msg_type))
    assert repo.list_events("s1") == []


# ── 读取 ──────────────────────────────────────────────────────────────────

def test_list_events_after_seq_and_order(repo):
    for i in range(5):
        repo.append("s1", type="tool", event="tool_call", data={"n": i})
        # 瞬态事件只占 seq 不落库
        repo.append_envelope("s1", _envelope(msg_type="progress", event="tick"))
    all_events = repo.list_events("s1")
    assert [e["_seq"] for e in all_events] == [1, 3, 5, 7, 9]
    tail = repo.list_events("s1", after_seq=5)
    assert [e["_seq"] for e in tail] == [7, 9]


def test_list_events_limit(repo):
    for i in range(10):
        repo.append("s1", type="tool", event="tool_call")
    assert len(repo.list_events("s1", limit=3)) == 3
    # 非法 limit 被夹到安全区间
    assert len(repo.list_events("s1", limit=0)) == 1
    assert len(repo.list_events("s1", limit=10 ** 9)) == 10


def test_list_events_missing_session(repo):
    assert repo.list_events("nope") == []


def test_last_seq_without_events(repo):
    assert repo.last_seq("nope") == 0


# ── 扩容保护与清理 ────────────────────────────────────────────────────────

def test_content_truncated(repo):
    repo.append("s1", type="message", event="message", content="x" * (se._MAX_CONTENT_CHARS + 50))
    ev = repo.list_events("s1")[0]
    assert len(ev["content"]) == se._MAX_CONTENT_CHARS


def test_unserializable_data_does_not_raise(repo):
    repo.append("s1", type="tool", event="tool_call", data={"obj": object()})
    assert repo.list_events("s1")[0]["data"]


def test_bad_data_json_returns_empty(repo):
    with repo._session() as s:
        s.add(se.SessionEvent(session_id="s1", seq=1, type="tool", event="tool_call",
                              data_json="{invalid"))
    assert repo.list_events("s1")[0]["data"] == {}


def test_clear_removes_events_and_resets_seq(repo):
    repo.append("s1", type="tool", event="tool_call")
    repo.append("s2", type="tool", event="tool_call")
    assert repo.clear("s1") == 1
    assert repo.list_events("s1") == []
    assert len(repo.list_events("s2")) == 1
    # 清理后 seq 重新从 1 开始（会话删除即重建，不该继承旧水位）
    assert repo.append("s1", type="tool", event="tool_call") == 1


# ── api_stream._record_event ──────────────────────────────────────────────

def test_record_event_persists_durable_and_buffers(repo, monkeypatch):
    import modules.thinking.api_stream as api_stream
    monkeypatch.setattr(se, "get_session_event_repo", lambda: repo)
    monkeypatch.setattr(api_stream, "_event_buffers", {})
    monkeypatch.setattr(api_stream, "_event_seq", {})

    env = _envelope()
    assert api_stream._record_event("s1", env) == 1
    assert env["_seq"] == 1
    assert len(repo.list_events("s1")) == 1
    assert len(api_stream._event_buffers["s1"]) == 1


def test_events_endpoint_returns_envelopes(repo, monkeypatch):
    import asyncio
    import modules.thinking.api_stream as api_stream
    monkeypatch.setattr(se, "get_session_event_repo", lambda: repo)
    repo.append_envelope("s1", _envelope())
    repo.append_envelope("s1", _envelope(event="tool_result", data={"status": "ok"}))

    body = asyncio.run(api_stream.get_session_events("s1"))
    assert body["success"] is True
    events = body["data"]["events"]
    assert [e["event"] for e in events] == ["tool_call", "tool_result"]
    assert body["data"]["last_seq"] == 2

    tail = asyncio.run(api_stream.get_session_events("s1", after_seq=1))
    assert [e["event"] for e in tail["data"]["events"]] == ["tool_result"]


def test_events_route_registered(repo, monkeypatch):
    """真实路由：/stream/sessions/{id}/events 返回事件与水位。"""
    from fastapi.testclient import TestClient
    from api.main import app

    monkeypatch.setattr(se, "get_session_event_repo", lambda: repo)
    repo.append_envelope("s1", _envelope())

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/stream/sessions/s1/events?after_seq=0&limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["events"][0]["event"] == "tool_call"
    assert body["data"]["last_seq"] == 1


def test_events_endpoint_degrades_on_error(monkeypatch):
    import asyncio
    import modules.thinking.api_stream as api_stream

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(se, "get_session_event_repo", _boom)
    body = asyncio.run(api_stream.get_session_events("s1"))
    assert body == {"success": True, "data": {"events": [], "last_seq": 0}}


def test_model_runner_tool_event_is_logged(repo, monkeypatch):
    """工具事件除实时推送外，还要落库并带 seq（刷新后可回放）。"""
    import modules.thinking.api_stream as api_stream
    from modules.thinking.core.model_runner import ModelRunner

    monkeypatch.setattr(se, "get_session_event_repo", lambda: repo)
    monkeypatch.setattr(api_stream, "_event_buffers", {})
    monkeypatch.setattr(api_stream, "_event_seq", {})
    sent = []
    monkeypatch.setattr(
        api_stream.connection_manager, "send_json_from_thread",
        lambda sid, env: sent.append((sid, env)),
    )

    runner = ModelRunner.__new__(ModelRunner)
    runner.session_id = "s1"
    runner.tier = "expert"
    runner.model_id = "m1"
    runner.identity = None

    class _TC:
        id = "c1"
        name = "read_file"

    runner._push_tool_call(_TC(), {"path": "a.py", "api_key": "sk-secret"})
    runner._push_tool_result(_TC(), "file body", status="ok", duration_ms=12.0)

    assert len(sent) == 2
    assert all(env.get("_seq") for _, env in sent)
    events = repo.list_events("s1")
    assert [e["event"] for e in events] == ["tool_call", "tool_result"]
    # 参数已脱敏（tool_events.sanitize_args）
    assert events[0]["data"]["args"]["api_key"] == "***"
    assert events[1]["data"]["status"] == "ok"


def test_record_event_falls_back_when_repo_fails(monkeypatch):
    import modules.thinking.api_stream as api_stream
    monkeypatch.setattr(api_stream, "_event_buffers", {})
    monkeypatch.setattr(api_stream, "_event_seq", {})

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(se, "get_session_event_repo", _boom)
    env = _envelope()
    # 落库失败也要有 seq，实时推送不能因此中断
    assert api_stream._record_event("s1", env) == 1
    assert env["_seq"] == 1
    assert api_stream._record_event("s1", _envelope()) == 2
