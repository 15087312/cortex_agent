"""会话投影（Phase 4）扩展测试 — 显式 seq / 失败兜底 / 序列化边界

补充 `test_session_projections.py` 未覆盖的分支：显式 seq 覆盖、清理失败、
事件计数器不可用时的 seq 兜底、不可序列化 value、损坏 JSON、单例缓存，
以及 `projections` 发射器的删除 / 读取失败与 usage 容错。
"""
import threading

import pytest

import modules.database.connection as conn
import modules.database.session_events as se
import modules.database.session_projections as sp
from modules.thinking import projections


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "proj_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(sp, "get_db_manager", lambda: dm)
    monkeypatch.setattr(se, "get_db_manager", lambda: dm)
    se.reset_session_event_repo()
    sp.reset_projection_repo()
    yield dm
    se.reset_session_event_repo()
    sp.reset_projection_repo()


@pytest.fixture
def repo(db):
    return sp.ProjectionRepository()


@pytest.fixture
def pushed(monkeypatch):
    frames = []
    monkeypatch.setattr(projections, "_push_frame", lambda sid, frame: frames.append((sid, frame)))
    return frames


# ── 仓储边界 ─────────────────────────────────────────────────────────────

def test_upsert_with_explicit_seq_keeps_it(repo):
    """显式 seq（调用方已向事件计数器申请）原样使用，不再二次分配"""
    assert repo.upsert("s1", "todos", {"total": 1}, seq=42)["seq"] == 42


def test_clear_failure_returns_zero(repo, monkeypatch):
    def _boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(repo, "_session", _boom)
    assert repo.clear("s1") == 0


def test_next_seq_falls_back_to_timestamp_when_event_repo_down(monkeypatch):
    """事件仓储不可用 → 退回毫秒时间戳，保证 seq 仍单调"""
    monkeypatch.setattr(se, "get_session_event_repo", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    seq = sp._next_seq("s1")
    assert isinstance(seq, int) and seq > 0


def test_upsert_unserializable_value_is_kept_as_preview(repo):
    """value 不可序列化 → 存字符串预览（回放时退化为 {}，但不写失败）"""
    payload = sp._dumps({(1, 2): "x"})
    assert "_unserializable" in payload


def test_oversized_value_truncated(repo):
    payload = sp._dumps({"blob": "z" * (sp._MAX_VALUE_CHARS + 100)})
    assert len(payload) == sp._MAX_VALUE_CHARS


def test_loads_handles_empty_and_broken_json():
    assert sp._loads(None) == {}
    assert sp._loads("{oops") == {}


def test_projection_repo_singleton_is_cached(repo):
    sp.reset_projection_repo()
    first = sp.get_projection_repo()
    assert sp.get_projection_repo() is first


# ── 发射器边界 ───────────────────────────────────────────────────────────

def test_emit_projection_requires_session_and_key(db):
    assert projections.emit_projection("", "todos", {}) is None
    assert projections.emit_projection("s1", "", {}) is None


def test_push_frame_swallows_send_errors(db, monkeypatch):
    """推送失败（连接层异常）不能中断投影写入路径"""
    from modules.thinking import api_stream
    monkeypatch.setattr(
        api_stream.connection_manager, "send_json_from_thread",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("socket closed")),
    )
    projections._push_frame("s1", {"key": "todos", "value": {}, "seq": 1})  # 不抛


def test_current_projections_without_session_returns_empty(db):
    assert projections.current_projections("") == []


def test_clear_projections_swallows_repo_errors(monkeypatch):
    monkeypatch.setattr(sp, "get_projection_repo",
                        lambda: (_ for _ in ()).throw(RuntimeError("down")))
    projections.clear_projections("s1")  # 不抛


def test_clear_projections_removes_rows(db, pushed):
    projections.emit_projection("s1", "todos", {"total": 1})
    projections.clear_projections("s1")
    assert sp.get_projection_repo().all("s1") == {}


def test_build_todos_non_list_payload_is_normalized(monkeypatch):
    """_load_todos 返回 dict（旧格式）→ 归一为空列表，不让前端拿到错误形状"""
    monkeypatch.setattr("infra.tool_manager.tools.todo._load_todos", lambda sid: {"1": "a"})
    assert projections.build_todos("s1") == {"todos": [], "done": 0, "total": 0}


def test_record_token_usage_tolerates_read_failure(db, pushed, monkeypatch):
    """读旧值失败（DB 抖动）→ 从 0 开始计，本轮用量不丢"""
    class _BoomRepo:
        def get(self, sid, key):
            raise RuntimeError("read fail")

        def upsert(self, sid, key, value, seq=None):
            return {"key": key, "value": value, "seq": 1}

    monkeypatch.setattr(sp, "get_projection_repo", lambda: _BoomRepo())
    value = projections.record_token_usage("s1", "large", {"prompt_tokens": 3, "completion_tokens": 2})
    assert value["total"] == 5


def test_usage_value_skips_non_dict_tiers():
    value = projections._usage_value({"by_tier": {"bad": "x", "good": {"total": 2}}})
    assert "bad" not in value["by_tier"]
    assert value["by_tier"]["good"] == {"prompt": 0, "completion": 0, "total": 2}


def test_build_runners_buckets_tiers_and_context(db, monkeypatch):
    """按 tier 分桶：large 提供上下文占用，主管/专家各进各的列表"""
    import modules.thinking.core.model_runner as mr

    class _Mgr:
        def list_runners(self):
            return [
                {"tier": "large", "name": "总指挥", "role": "orchestrator", "model_id": "large_primary",
                 "status": "thinking", "context_tokens": 3200, "context_window_size": 128000},
                {"tier": "supervisor", "name": "主管", "role": "pm", "model_id": "pm_1"},
                {"tier": "expert", "name": "专家", "role": "code_writer", "model_id": "ex_1",
                 "supervisor": "主管"},
            ]

    monkeypatch.setattr(mr, "_runner_managers", {"s1": _Mgr()})
    monkeypatch.setattr(mr, "_runner_managers_lock", threading.RLock())

    snap = projections.build_runners("s1")
    assert snap["large_model"]["name"] == "总指挥"
    assert snap["context_tokens"] == 3200
    assert snap["context_window_size"] == 128000
    assert [s["name"] for s in snap["active_supervisors"]] == ["主管"]
    assert [e["name"] for e in snap["active_experts"]] == ["专家"]
