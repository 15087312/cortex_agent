"""可视化链路端到端（事件表 → 回放接口 → 投影/待办快照）

验证「刷新后与运行中长得一样」依赖的三条真实链路：

1. 工具事件带 seq 落库，`/stream/sessions/{id}/events` 按序回放，且 `user_message`
   事件提供工具卡片锚点（否则刷新后卡片回不到原位置）；
2. 投影按 key 覆盖写、读回接口与实时帧同构（`session_projection`）；
3. 审批/提问的整快照在重连后仍能重建（`session_request`），应答后快照清空。
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

import modules.database.connection as conn
import modules.database.session_events as se
import modules.database.session_projections as sp
import modules.thinking.api_stream as stream
from modules.thinking import pending_requests as pr
from modules.thinking import projections


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "viz.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(se, "get_db_manager", lambda: dm)
    monkeypatch.setattr(sp, "get_db_manager", lambda: dm)
    se.reset_session_event_repo()
    sp.reset_projection_repo()
    monkeypatch.setattr(stream, "_event_buffers", {})
    monkeypatch.setattr(stream, "_event_seq", {})
    yield dm
    se.reset_session_event_repo()
    sp.reset_projection_repo()
    pr.reset_registry()


@pytest.fixture
def frames(monkeypatch):
    """收集真正推给前端的帧（WS + SSE 共用出口）"""
    sent = []
    monkeypatch.setattr(
        stream.connection_manager, "send_json_from_thread",
        lambda sid, env: sent.append((sid, env)) or True,
    )
    return sent


@pytest.fixture
def client(db):
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


def _record(sid, msg_type, event, data=None, content=""):
    return stream._record_event(sid, {
        "type": msg_type, "event": event, "session_id": sid,
        "role": "system", "content": content, "data": data or {}, "timestamp": time.time(),
    })


# ── 事件日志 → 回放接口 ─────────────────────────────────────────────────

def test_replay_returns_tool_events_in_order_with_anchor(client, db):
    """事件按 seq 回放，且 user_message 锚点先于其后的工具事件（前端据此定位卡片）"""
    _record("s1", "user_message", "user_message", {"message_id": "u1"})
    _record("s1", "tool", "tool_call", {"call_id": "c1", "name": "read_file", "args": {"path": "a.py"}})
    _record("s1", "message", "message", {"message_id": "a1"}, content="我看完了")
    _record("s1", "tool", "tool_result", {"call_id": "c1", "status": "ok"})

    body = client.get("/stream/sessions/s1/events").json()
    events = body["data"]["events"]
    assert [e["type"] for e in events] == ["user_message", "tool", "message", "tool"]
    seqs = [e["_seq"] for e in events]
    assert seqs == sorted(seqs)
    assert body["data"]["last_seq"] == seqs[-1]
    # 工具调用紧跟在用户消息之后：回放锚点成立
    assert events[1]["data"]["call_id"] == "c1"
    assert events[1]["_seq"] > events[0]["_seq"]


def test_replay_after_seq_only_returns_missing_events(client, db):
    """重连补发：after_seq 只回缺口，不重复整段历史"""
    for i in range(4):
        _record("s1", "tool", "tool_call", {"call_id": f"c{i}"})
    all_events = client.get("/stream/sessions/s1/events").json()["data"]["events"]
    tail = client.get(f"/stream/sessions/s1/events?after_seq={all_events[2]['_seq']}").json()
    assert [e["data"]["call_id"] for e in tail["data"]["events"]] == ["c3"]


# ── 投影 ────────────────────────────────────────────────────────────────

def test_projection_is_readable_after_restart_and_matches_live_frame(client, db, frames):
    """投影写入后可从接口读回（刷新/重启恢复），帧形状与实时推送一致"""
    projections.emit_projection("s1", "todos", {"todos": [{"id": "1", "status": "pending"}],
                                                "done": 0, "total": 1})
    live_frame = frames[-1][1]["data"]

    body = client.get("/stream/sessions/s1/projections").json()
    stored = {f["key"]: f for f in body["data"]["projections"]}
    assert stored["todos"]["value"] == live_frame["value"]
    assert stored["todos"]["seq"] == live_frame["seq"]


def test_push_current_sends_every_projection_on_reconnect(db, frames):
    """WS 重连即补发全部投影（不依赖事件补发）"""
    projections.emit_projection("s1", "todos", {"total": 0})
    projections.emit_projection("s1", "contextPressure", {"tokens": 10, "window": 100, "ratio": 0.1})
    frames.clear()

    assert projections.push_current("s1") == 2
    sent = [env for _sid, env in frames]
    assert {e["data"]["key"] for e in sent} == {"todos", "contextPressure"}
    assert all(e["type"] == "session_projection" for e in sent)


def test_runner_state_projection_reuses_process_snapshot_shape(db, frames):
    """回合末发射的 runners 与过程流快照同构（前端用同一套扁平化逻辑）"""
    projections.emit_runner_state("s1")
    by_key = {env["data"]["key"]: env["data"]["value"] for _sid, env in frames}
    assert set(by_key) == {"runners", "contextPressure"}
    assert {"large_model", "active_supervisors", "active_experts"} <= set(by_key["runners"])
    assert set(by_key["contextPressure"]) == {"tokens", "window", "ratio"}


# ── 可应答帧 ────────────────────────────────────────────────────────────

def _approval(request_id="r1"):
    return {
        "type": "security", "target": "exec_command", "action": "等待用户审批",
        "data": {
            "event_type": "security", "action": "等待用户审批", "target": "exec_command",
            "payload": {"detail": "要执行 rm -rf", "request_id": request_id},
        },
    }


def test_pending_snapshot_rebuilds_after_reconnect_and_clears_on_answer(db, frames):
    """刷新后横幅可重建：快照帧带完整请求；应答后推空快照（不留死按钮）"""
    pr.register_from_stage_event("s1", _approval())
    snap = frames[-1][1]
    assert snap["type"] == "session_request"
    assert [r["request_id"] for r in snap["data"]["requests"]] == ["r1"]
    assert snap["data"]["requests"][0]["kind"] == pr.KIND_APPROVAL

    # 重连：再推一次当前快照即可恢复（内存态，不落库）
    frames.clear()
    pr.broadcast("s1")
    assert [r["request_id"] for r in frames[-1][1]["data"]["requests"]] == ["r1"]

    # 用户点击批准 → 待办结束 + 空快照
    assert pr.resolve_and_broadcast("s1", "r1", {"approved": True}) is True
    assert frames[-1][1]["data"]["requests"] == []


def test_pending_snapshot_never_reaches_other_sessions(db, frames):
    """会话隔离：s2 的待办不会出现在 s1 的快照里"""
    pr.register_from_stage_event("s1", _approval("r1"))
    pr.register_from_stage_event("s2", _approval("r2"))
    for sid, env in frames:
        if sid == "s1":
            assert [r["request_id"] for r in env["data"]["requests"]] == ["r1"]
    assert [r["request_id"] for r in pr.get_registry().snapshot("s2")] == ["r2"]
