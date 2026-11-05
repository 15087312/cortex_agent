"""api_stream 工具分支补全：SSE 扇出保护 / envelope ack / 过程流持久化降级

补充现有 api_stream 套件未覆盖的小分支：队列满丢帧、无订阅者解绑、
事件循环外订阅、配置缺失时的上下文窗口、require_ack envelope，
以及 `_persist_process` 的早退与落库异常。
"""
import asyncio
import threading

import pytest

import modules.thinking.api_stream as stream


def _system():
    s = stream.StreamThinkingSystem.__new__(stream.StreamThinkingSystem)
    s.sessions = {}
    s._running = False
    s._lock = asyncio.Lock()
    s._session_repo = None
    return s


# ── ConnectionManager：SSE 扇出 ──────────────────────────────────────────

def test_subscribe_stream_outside_event_loop_is_safe():
    """同步上下文订阅（无 running loop）→ 不抛，也不把 _loop 置空"""
    cm = stream.ConnectionManager()
    q = cm.subscribe_stream("s1")
    try:
        assert cm.has_stream("s1") is True
        assert cm._streams["s1"] == {q}
    finally:
        cm.unsubscribe_stream("s1", q)


def test_unsubscribe_unknown_session_is_noop():
    cm = stream.ConnectionManager()
    cm.unsubscribe_stream("ghost", asyncio.Queue())  # 不 KeyError


def test_unsubscribe_last_subscriber_removes_bucket():
    cm = stream.ConnectionManager()
    q = cm.subscribe_stream("s1")
    cm.unsubscribe_stream("s1", q)
    assert "s1" not in cm._streams
    assert cm.has_stream("s1") is False


def test_fanout_streams_drops_frame_when_queue_full():
    """订阅者读得比生成慢（队列满）→ 丢这一帧并记 debug，不阻塞思考"""
    cm = stream.ConnectionManager()
    q = asyncio.Queue(maxsize=1)
    q.put_nowait({"n": 1})
    cm._streams["s1"] = {q}
    cm._fanout_streams("s1", {"n": 2})
    assert q.qsize() == 1
    assert q.get_nowait() == {"n": 1}  # 旧帧保留，新帧被丢弃


def test_fanout_streams_without_subscribers_is_noop():
    stream.ConnectionManager()._fanout_streams("ghost", {"n": 1})


# ── envelope / 配置兜底 ─────────────────────────────────────────────────

def test_build_event_require_ack_carries_ack_id():
    env = stream._build_event(
        session_id="s1", msg_type="message", event="message",
        content="hi", require_ack=True,
    )
    assert env["require_ack"] is True
    assert env["ack_id"].startswith("ack_s1_")


def test_build_event_defaults_are_stable():
    env = stream._build_event(session_id="s1", msg_type="tool", event="tool_call")
    assert env["role"] == "system"
    assert env["data"] == {}
    assert "require_ack" not in env


def test_resolve_context_window_returns_zero_when_config_fails(monkeypatch):
    """配置读取失败 → 0（前端据此隐藏上下文条，而不是显示编造值）"""
    from config.settings import settings as cfg
    monkeypatch.setattr(type(cfg), "get_context_length",
                        lambda self, tier: (_ for _ in ()).throw(RuntimeError("no config")))
    assert stream._resolve_context_window("large") == 0


# ── _persist_process（真实 SQLite）───────────────────────────────────────

@pytest.fixture
def repo(tmp_path, monkeypatch):
    """真实 SessionRepository + 临时 SQLite（不 mock 落库，断言真实行）"""
    import modules.database.connection as conn
    import modules.database.session_repo as sr

    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "api_stream_ext4.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(sr, "get_db_manager", lambda: dm)
    return sr.SessionRepository()


@pytest.mark.asyncio
async def test_persist_process_without_repo_writes_nothing(monkeypatch, repo):
    """仓储不可用 → 直接返回，库里不会凭空多出一行"""
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: None)
    await s._persist_process("s1", "过程流")
    assert repo.get_messages("s1") == []


@pytest.mark.asyncio
async def test_persist_process_skips_empty_payload(monkeypatch, repo):
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    await s._persist_process("s1", "", None)
    assert repo.get_messages("s1") == []


@pytest.mark.asyncio
async def test_persist_process_writes_flow_and_snapshot_to_db(monkeypatch, repo):
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    await s._persist_process("s1", "过程流正文", {"large_model": {"name": "总指挥"}})

    rows = repo.get_messages("s1")
    assert len(rows) == 1
    assert rows[0]["role"] == "process"
    assert rows[0]["content"] == "过程流正文"
    assert rows[0]["tier"] == "process"
    assert rows[0]["metadata"]["runners"]["large_model"]["name"] == "总指挥"


@pytest.mark.asyncio
async def test_persist_process_swallows_save_errors(monkeypatch, repo):
    """落库失败只记日志：过程流是展示数据，不能中断对话"""
    s = _system()
    monkeypatch.setattr(s, "_get_session_repo", lambda: repo)
    monkeypatch.setattr(repo, "_session", lambda: (_ for _ in ()).throw(RuntimeError("db full")))
    await s._persist_process("s1", "过程流")  # 不抛
    # 用未被打桩的新仓储回读同一库：失败时不会留下半条消息
    assert type(repo)().get_messages("s1") == []
