"""会话投影（Phase 4）测试 — 真实 SQLite

覆盖：按 key 覆盖写、seq 与事件共用计数器、帧形状、四个 key 的构建、
token 累加、清理，以及发射失败不影响调用方。
"""
import threading

import pytest

import modules.database.connection as conn
import modules.database.session_events as se
import modules.database.session_projections as sp
from modules.thinking import projections


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "proj.db"))
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
    """收集实际推给前端的帧（不碰 WebSocket）"""
    frames = []
    monkeypatch.setattr(projections, "_push_frame", lambda sid, frame: frames.append((sid, frame)))
    return frames


# ── 覆盖写与 seq ──────────────────────────────────────────────────────

def test_upsert_creates_frame(repo):
    frame = repo.upsert("s1", "todos", {"todos": [], "done": 0, "total": 0})
    assert frame["key"] == "todos"
    assert frame["value"]["total"] == 0
    assert frame["seq"] >= 1


def test_upsert_overwrites_single_row_and_bumps_seq(repo):
    first = repo.upsert("s1", "todos", {"todos": [{"id": "a"}], "done": 0, "total": 1})
    second = repo.upsert("s1", "todos", {"todos": [], "done": 0, "total": 0})
    assert second["seq"] > first["seq"]
    # 每个 key 只有一行（覆盖写，不追加）
    assert len(repo.all("s1")) == 1
    assert repo.get("s1", "todos")["value"]["total"] == 0


def test_seq_shared_with_event_log(repo):
    """投影和事件同处一个序号空间：客户端能统一比较新旧"""
    event_repo = se.get_session_event_repo()
    event_seq = event_repo.append("s1", type="tool", event="tool_call")
    proj = repo.upsert("s1", "runners", {})
    next_event = event_repo.append("s1", type="tool", event="tool_call")
    assert proj["seq"] > event_seq
    assert next_event > proj["seq"]


def test_seq_never_reuses_projection_seq_after_restart(db, pushed):
    """重启后事件计数器从事件表重建（可能落后于投影表），投影 seq 仍必须严格递增——
    否则前端的 higher-seq-wins 会把新快照当旧帧丢掉。"""
    projections.emit_projection("s1", "todos", {"total": 1})
    first = sp.get_projection_repo().get("s1", "todos")["seq"]
    se.reset_session_event_repo()
    sp.reset_projection_repo()
    second = sp.get_projection_repo().upsert("s1", "todos", {"total": 2})["seq"]
    assert second > first


def test_list_frames_sorted_by_seq(repo):
    repo.upsert("s1", "todos", {"n": 1})
    repo.upsert("s1", "runners", {"n": 2})
    repo.upsert("s1", "todos", {"n": 3})   # 覆盖 → seq 变大
    keys = [f["key"] for f in repo.list_frames("s1")]
    assert keys == ["runners", "todos"]
    seqs = [f["seq"] for f in repo.list_frames("s1")]
    assert seqs == sorted(seqs)


def test_sessions_are_isolated(repo):
    repo.upsert("a", "todos", {"v": "a"})
    repo.upsert("b", "todos", {"v": "b"})
    assert repo.get("a", "todos")["value"]["v"] == "a"
    assert repo.get("b", "todos")["value"]["v"] == "b"


def test_get_missing_key_returns_none(repo):
    assert repo.get("s1", "tokenUsage") is None


def test_clear_removes_rows(repo):
    repo.upsert("s1", "todos", {})
    repo.upsert("s1", "runners", {})
    assert repo.clear("s1") == 2
    assert repo.all("s1") == {}


# ── 帧形状 ────────────────────────────────────────────────────────────

def test_push_frame_builds_session_projection_envelope(db, monkeypatch):
    from modules.thinking import api_stream
    sent = []
    monkeypatch.setattr(
        api_stream.connection_manager, "send_json_from_thread",
        lambda sid, env: sent.append((sid, env)) or True,
    )
    frame = {"key": "todos", "value": {"todos": [], "done": 0, "total": 0}, "seq": 7}
    projections._push_frame("s1", frame)
    assert len(sent) == 1
    sid, env = sent[0]
    assert sid == "s1"
    assert env["type"] == "session_projection"
    assert env["event"] == "projection"
    assert env["data"] == frame
    # `_seq` 与投影 seq 一致：前端可以像事件一样比较新旧
    assert env["_seq"] == 7


def test_emit_projection_writes_and_pushes(db, pushed):
    frame = projections.emit_projection("s1", "contextPressure", {"tokens": 10, "window": 100, "ratio": 0.1})
    assert frame["key"] == "contextPressure"
    assert pushed[0][1]["seq"] == frame["seq"]
    assert sp.get_projection_repo().get("s1", "contextPressure")["value"]["ratio"] == 0.1


def test_emit_projection_swallows_write_errors(monkeypatch):
    """仓储炸了也不能中断思考循环"""
    from modules.database import session_projections as sp_mod

    class Boom:
        def upsert(self, *a, **kw):
            raise RuntimeError("db down")

    monkeypatch.setattr(sp_mod, "get_projection_repo", lambda: Boom())
    assert projections.emit_projection("s1", "todos", {}) is None


def test_push_current_sends_all_kept_frames(db, pushed):
    projections.emit_projection("s1", "todos", {"total": 0})
    projections.emit_projection("s1", "runners", {})
    pushed.clear()
    assert projections.push_current("s1") == 2
    assert {f[1]["key"] for f in pushed} == {"todos", "runners"}


def test_current_projections_empty_without_repo(monkeypatch):
    from modules.database import session_projections as sp_mod
    monkeypatch.setattr(sp_mod, "get_projection_repo", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert projections.current_projections("s1") == []


# ── contextPressure ───────────────────────────────────────────────────

def test_context_pressure_ratio():
    snap = {"context_tokens": 3200, "context_window_size": 128000}
    assert projections.build_context_pressure(snap) == {"tokens": 3200, "window": 128000, "ratio": 0.025}


def test_context_pressure_unknown_window():
    """窗口未知（配置缺失）→ ratio 记 0，不编造占比"""
    assert projections.build_context_pressure({"context_tokens": 50, "context_window_size": 0})["ratio"] == 0.0


def test_context_pressure_clamps_over_window():
    assert projections.build_context_pressure({"context_tokens": 200, "context_window_size": 100})["ratio"] == 1.0


def test_context_pressure_tolerates_garbage():
    assert projections.build_context_pressure(None) == {"tokens": 0, "window": 0, "ratio": 0.0}
    assert projections.build_context_pressure({"context_tokens": "x"})["tokens"] == 0


# ── todos ─────────────────────────────────────────────────────────────

def test_build_todos_counts_done(monkeypatch):
    monkeypatch.setattr(
        "infra.tool_manager.tools.todo._load_todos",
        lambda sid: [
            {"id": "1", "content": "a", "status": "completed"},
            {"id": "2", "content": "b", "status": "pending"},
        ],
    )
    value = projections.build_todos("s1")
    assert value["total"] == 2
    assert value["done"] == 1


def test_build_todos_empty_and_broken(monkeypatch):
    monkeypatch.setattr("infra.tool_manager.tools.todo._load_todos", lambda sid: None)
    assert projections.build_todos("s1") == {"todos": [], "done": 0, "total": 0}

    def boom(sid):
        raise RuntimeError("io")

    monkeypatch.setattr("infra.tool_manager.tools.todo._load_todos", boom)
    assert projections.build_todos("s1")["todos"] == []


# ── runners ───────────────────────────────────────────────────────────

def test_build_runners_without_manager_returns_empty_shape(db):
    snap = projections.build_runners("no-such-session")
    assert snap["large_model"] is None
    assert snap["active_supervisors"] == []
    assert snap["active_experts"] == []
    assert snap["context_tokens"] == 0


# ── tokenUsage ────────────────────────────────────────────────────────

def test_record_token_usage_accumulates(db, pushed):
    projections.record_token_usage("s1", "large", {"prompt_tokens": 10, "completion_tokens": 5})
    value = projections.record_token_usage("s1", "large", {"prompt_tokens": 20, "completion_tokens": 1})
    assert value["prompt"] == 30
    assert value["completion"] == 6
    assert value["total"] == 36
    assert value["by_tier"]["large"] == {"prompt": 30, "completion": 6, "total": 36}


def test_record_token_usage_per_tier(db, pushed):
    projections.record_token_usage("s1", "large", {"prompt_tokens": 10, "completion_tokens": 0})
    value = projections.record_token_usage("s1", "expert", {"prompt_tokens": 4, "completion_tokens": 2})
    assert value["total"] == 16
    assert value["by_tier"]["large"]["total"] == 10
    assert value["by_tier"]["expert"]["total"] == 6


def test_record_token_usage_survives_new_repo_instance(db, pushed):
    """计数落在投影表里，不依赖 runner 实例存活"""
    projections.record_token_usage("s1", "supervisor", {"prompt_tokens": 7, "completion_tokens": 3})
    sp.reset_projection_repo()
    value = projections.record_token_usage("s1", "supervisor", {"prompt_tokens": 1, "completion_tokens": 1})
    assert value["prompt"] == 8
    assert value["total"] == 12


def test_record_token_usage_accepts_total_only(db, pushed):
    value = projections.record_token_usage("s1", "large", {"total_tokens": 9})
    assert value["total"] == 9
    assert value["prompt"] == 0


def test_record_token_usage_ignores_empty(db, pushed):
    assert projections.record_token_usage("s1", "large", None) is None
    assert projections.record_token_usage("s1", "large", {}) is None
    assert projections.record_token_usage("s1", "large", {"prompt_tokens": 0}) is None
    assert pushed == []


# ── 接线：回合末发射 / 接口 / 快照复用 ─────────────────────────────

def test_emit_runner_state_writes_runners_and_pressure(db, pushed):
    value = projections.emit_runner_state("s1")
    assert value["large_model"] is None          # 无 runner → 仍写一份空快照
    assert {"runners", "contextPressure"} <= set(sp.get_projection_repo().all("s1"))
    pressure = sp.get_projection_repo().get("s1", "contextPressure")["value"]
    assert set(pressure) == {"tokens", "window", "ratio"}


def test_projections_route_registered(db, monkeypatch):
    """真实路由：/stream/sessions/{id}/projections 返回投影帧。"""
    from fastapi.testclient import TestClient

    from api.main import app

    repo = sp.ProjectionRepository()
    repo.upsert("s1", "todos", {"todos": [], "done": 0, "total": 0})
    monkeypatch.setattr(sp, "get_projection_repo", lambda: repo)

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/stream/sessions/s1/projections")
    assert r.status_code == 200
    frames = r.json()["data"]["projections"]
    assert [f["key"] for f in frames] == ["todos"]
    assert frames[0]["seq"] >= 1
    assert frames[0]["value"]["total"] == 0


def test_collect_process_snapshot_delegates_to_projections(db, monkeypatch):
    """过程流快照与 runners 投影共用一份构建逻辑（避免两处形状漂移）"""
    from modules.thinking import api_stream
    monkeypatch.setattr(projections, "build_runners", lambda sid: {"large_model": "X", "context_tokens": 5})
    system = object.__new__(api_stream.StreamThinkingSystem)
    assert system._collect_process_snapshot("s1") == {"large_model": "X", "context_tokens": 5}
