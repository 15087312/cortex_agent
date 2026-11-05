"""会话事件日志（Phase 1）扩展测试 — 截断 / 冲突重试 / 清理失败 / 单例

补充 `test_session_events.py` 未覆盖的分支：data 序列化兜底与超限截断、
seq 冲突的一次重试与重试仍失败、append_envelope 的非 dict data 与落库失败降级、
clear 失败返回 0、_loads 兼容、全局单例。
"""
import threading

import pytest
from sqlalchemy.exc import IntegrityError

import modules.database.connection as conn
import modules.database.session_events as se


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "events_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(se, "get_db_manager", lambda: dm)
    se.reset_session_event_repo()
    yield se.SessionEventRepository()
    se.reset_session_event_repo()


# ── data 序列化与截断 ────────────────────────────────────────────────────

def test_unserializable_data_falls_back_to_string_preview(repo):
    """JSON 无法编码（非基础类型键）→ 退回字符串预览，不让事件写失败"""
    repo.append("s1", type="tool", event="tool_call", data={(1, 2): "x"})
    data = repo.list_events("s1")[0]["data"]
    assert "_unserializable" in data


def test_oversized_data_is_truncated(repo):
    """单条 data 超过上限时截断，避免一帧把表撑大"""
    repo.append("s1", type="tool", event="tool_result", data={"output": "y" * 25000})
    with repo._session() as s:
        raw = s.query(se.SessionEvent).filter(se.SessionEvent.session_id == "s1").one()
        stored_len = len(raw.data_json)
    assert stored_len == se._MAX_DATA_CHARS


def test_non_dict_envelope_data_is_wrapped(repo):
    """envelope.data 是字符串/列表时包成 {'value': ...}，回放形状保持一致"""
    repo.append_envelope("s1", {"type": "tool", "event": "tool_result", "data": "纯文本"})
    assert repo.list_events("s1")[0]["data"] == {"value": "纯文本"}


def test_persist_override_false_skips_write(repo):
    """persist=False 时 durable 类型也可只占 seq 不落库（同步补发场景）"""
    seq = repo.append_envelope("s1", {"type": "message", "event": "message"}, persist=False)
    assert seq == 1
    assert repo.list_events("s1") == []


# ── seq 冲突 ─────────────────────────────────────────────────────────────

def test_append_retries_once_on_seq_collision(repo):
    """显式 seq 已被占用（跨进程写）→ 对齐库内水位后重试一次"""
    assert repo.append("s1", type="tool", event="tool_call") == 1
    assert repo.append("s1", type="tool", event="tool_call", seq=1) == 2


def test_append_raises_when_collision_persists(repo, monkeypatch):
    """重试后仍撞号 → 抛出 IntegrityError（调用方决定是否降级）"""
    repo.append("s1", type="tool", event="tool_call")
    monkeypatch.setattr(repo, "_max_seq", lambda sid: 0)
    monkeypatch.setattr(repo, "_reserve_seq", lambda sid: 1)
    with pytest.raises(IntegrityError):
        repo.append("s1", type="tool", event="tool_call", seq=1)


def test_append_envelope_swallows_persist_failure(repo, monkeypatch):
    """落库失败不能阻断实时推送：seq 照发，异常只记 debug"""
    monkeypatch.setattr(repo, "append", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    env = {"type": "tool", "event": "tool_call", "data": {}}
    assert repo.append_envelope("s1", env) == 1
    assert env["_seq"] == 1


# ── 清理与兼容 ───────────────────────────────────────────────────────────

def test_clear_failure_returns_zero(repo, monkeypatch):
    """清理时 DB 不可用 → 返回 0，不把删除接口带崩"""
    def _boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(repo, "_session", _boom)
    assert repo.clear("s1") == 0


def test_loads_handles_empty_and_non_object_json(repo):
    """空字符串 / 非对象 JSON 回放时都要给出 dict 形状"""
    with repo._session() as s:
        s.add(se.SessionEvent(session_id="s1", seq=1, type="tool", event="tool_call", data_json=""))
        s.add(se.SessionEvent(session_id="s1", seq=2, type="tool", event="tool_call", data_json="[1,2]"))
    events = repo.list_events("s1")
    assert events[0]["data"] == {}
    assert events[1]["data"] == {"value": [1, 2]}


def test_repo_singleton_is_cached(repo):
    """全局单例只创建一次（WS 线程与思考线程共用同一 seq 计数器）"""
    se.reset_session_event_repo()
    first = se.get_session_event_repo()
    assert se.get_session_event_repo() is first
