"""可应答请求待办表（Phase 4.b）测试

覆盖：登记/幂等/会话隔离、快照结构、结束与清理、从调度事件识别审批与提问、
整快照帧形状，以及“结束不存在的请求”不推送。
"""
import pytest

from modules.thinking import pending_requests as pr


@pytest.fixture
def registry(monkeypatch):
    pr.reset_registry()
    yield pr.get_registry()
    pr.reset_registry()


@pytest.fixture
def sent(monkeypatch, registry):
    """收集推给前端的帧（不碰 WebSocket）"""
    from modules.thinking import api_stream
    frames = []
    monkeypatch.setattr(
        api_stream.connection_manager, "send_json_from_thread",
        lambda sid, env: frames.append((sid, env)) or True,
    )
    return frames


def _approval_event(request_id="r1", target="exec_command", detail="要执行 rm"):
    return {
        "content": "[安全审查] exec_command 等待用户审批 — 要执行 rm",
        "data": {
            "event_type": "security",
            "action": "等待用户审批",
            "target": target,
            "payload": {"request_id": request_id, "detail": detail, "caller": "expert_1"},
        },
    }


def _question_event(request_id="q1", question="选哪个？", options=("A", "B")):
    return {
        "content": "[安全审查] user_intent_request 提问",
        "data": {
            "event_type": "security",
            "action": "user_intent_request",
            "target": "user_intent_request",
            "payload": {
                "request_id": request_id, "question": question,
                "options": list(options) if options else None, "context": "上下文",
            },
        },
    }


# ── 待办表本身 ───────────────────────────────────────────────────────

def test_register_and_snapshot(registry):
    registry.register("s1", "r1", pr.KIND_APPROVAL, target="exec_command", detail="d")
    snap = registry.snapshot("s1")
    assert len(snap) == 1
    assert snap[0]["request_id"] == "r1"
    assert snap[0]["kind"] == "approval"
    assert snap[0]["target"] == "exec_command"
    assert snap[0]["session_id"] == "s1"
    assert snap[0]["created_at"] > 0


def test_register_is_idempotent(registry):
    registry.register("s1", "r1", pr.KIND_APPROVAL, target="a")
    first = registry.snapshot("s1")[0]["created_at"]
    registry.register("s1", "r1", pr.KIND_APPROVAL, target="a", detail="new")
    snap = registry.snapshot("s1")
    assert len(snap) == 1
    assert snap[0]["created_at"] == first          # 保留首次时间（顺序稳定）
    assert snap[0]["detail"] == "new"              # 字段刷新


def test_sessions_are_isolated(registry):
    registry.register("a", "r1", pr.KIND_APPROVAL)
    registry.register("b", "r1", pr.KIND_QUESTION, question="q")
    assert registry.snapshot("a")[0]["kind"] == "approval"
    assert registry.snapshot("b")[0]["kind"] == "question"
    assert registry.has("a", "r1") and not registry.has("c", "r1")


def test_resolve_removes_and_reports(registry):
    registry.register("s1", "r1", pr.KIND_APPROVAL)
    assert registry.resolve("s1", "r1", {"approved": True}) is True
    assert registry.snapshot("s1") == []
    # 再结束一次：不存在的请求返回 False（前端不会收到重复快照）
    assert registry.resolve("s1", "r1") is False


def test_clear_returns_count(registry):
    registry.register("s1", "r1", pr.KIND_APPROVAL)
    registry.register("s1", "r2", pr.KIND_QUESTION)
    assert registry.clear("s1") == 2
    assert registry.snapshot("s1") == []


def test_snapshot_copy_is_detached(registry):
    """快照是副本：调用方改它不会污染待办表"""
    registry.register("s1", "r1", pr.KIND_APPROVAL, target="a")
    snap = registry.snapshot("s1")
    snap[0]["target"] = "hacked"
    assert registry.snapshot("s1")[0]["target"] == "a"


# ── 从调度事件识别 ───────────────────────────────────────────────────

def test_register_from_approval_event(registry, sent):
    record = pr.register_from_stage_event("s1", _approval_event())
    assert record["kind"] == "approval"
    assert record["target"] == "exec_command"
    assert record["detail"] == "要执行 rm"
    # 登记即推整快照
    assert len(sent) == 1
    env = sent[0][1]
    assert env["type"] == "session_request"
    assert env["event"] == "requests_snapshot"
    assert env["data"]["requests"][0]["request_id"] == "r1"


def test_register_from_question_event(registry, sent):
    record = pr.register_from_stage_event("s1", _question_event())
    assert record["kind"] == "question"
    assert record["question"] == "选哪个？"
    assert record["options"] == ["A", "B"]
    assert record["context"] == "上下文"


def test_question_without_options(registry, sent):
    record = pr.register_from_stage_event("s1", _question_event(options=None))
    assert record["options"] == []


def test_ignores_non_interactive_events(registry, sent):
    # 非 security 事件
    assert pr.register_from_stage_event("s1", {"data": {"event_type": "scheduler"}}) is None
    # security 但只是拦截/审查中（没有 request_id 或不是等待用户）
    assert pr.register_from_stage_event("s1", {
        "data": {"event_type": "security", "action": "安全拦截",
                 "payload": {"request_id": "x"}},
    }) is None
    assert pr.register_from_stage_event("s1", {
        "data": {"event_type": "security", "action": "等待用户审批", "payload": {}},
    }) is None
    assert registry.snapshot("s1") == []
    assert sent == []


def test_repeated_event_does_not_duplicate(registry, sent):
    pr.register_from_stage_event("s1", _approval_event())
    pr.register_from_stage_event("s1", _approval_event())
    assert len(registry.snapshot("s1")) == 1
    # 两次都推了快照（幂等快照，前端按 request_id 去重）
    assert len(sent) == 2


# ── 结束与推送 ───────────────────────────────────────────────────────

def test_resolve_and_broadcast(registry, sent):
    pr.register_from_stage_event("s1", _approval_event())
    sent.clear()
    assert pr.resolve_and_broadcast("s1", "r1", {"approved": True}) is True
    assert registry.snapshot("s1") == []
    assert len(sent) == 1
    assert sent[0][1]["data"]["requests"] == []


def test_resolve_and_broadcast_ignores_unknown(registry, sent):
    assert pr.resolve_and_broadcast("s1", "nope") is False
    assert sent == []


def test_resolve_and_broadcast_requires_ids(registry, sent):
    assert pr.resolve_and_broadcast("", "r1") is False
    assert pr.resolve_and_broadcast("s1", "") is False


def test_broadcast_survives_send_failure(registry, monkeypatch):
    """推送失败不能影响等待中的请求（返回的是快照本身）"""
    from modules.thinking import api_stream

    def boom(sid, env):
        raise RuntimeError("ws down")

    monkeypatch.setattr(api_stream.connection_manager, "send_json_from_thread", boom)
    pr.register_from_stage_event("s1", _approval_event())
    snap = pr.broadcast("s1")
    assert [r["request_id"] for r in snap] == ["r1"]
