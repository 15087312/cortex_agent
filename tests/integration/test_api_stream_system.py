"""api_stream StreamThinkingSystem 真实测试（无 mock 伪造）：持久化/事件格式化/停止/emit

用真实 StreamThinkingSystem（__new__ 避免重量级 Orchestrator 构造，方法全真实）
+ 真实 SessionRepository + 真实临时 SQLite + 真实 ConnectionManager（后台线程 loop）。
"""
import asyncio
import threading

import pytest

import modules.database.connection as conn
import modules.thinking.api_stream as stream_mod
from modules.thinking.api_stream import StreamThinkingSystem


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    async def accept(self):
        pass


class _LoopServer:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=3)
        self.loop.close()


@pytest.fixture
def server():
    s = _LoopServer()
    yield s
    s.stop()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """真实临时 SQLite + 真实 SessionRepository"""
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_as.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    conn.get_db_manager().initialize()
    return SessionRepository()


@pytest.fixture
def cm(monkeypatch, server):
    mgr = stream_mod.ConnectionManager()
    mgr._loop = server.loop
    monkeypatch.setattr(stream_mod, "connection_manager", mgr)
    return mgr


def _system(repo):
    s = StreamThinkingSystem.__new__(StreamThinkingSystem)
    s.sessions = {}
    s._running = False
    s._lock = asyncio.Lock()
    s._session_repo = repo
    return s


async def test_append_message_real_db(repo):
    """真实追加消息：内存 + 真实 DB 落库，连续同内容去重"""
    s = _system(repo)
    s.sessions["s1"] = {"messages": [], "running": True}
    mid = await s._append_message("s1", "user", "hi")
    assert mid  # 真实 DB 返回消息 id
    mid2 = await s._append_message("s1", "user", "hi")
    assert mid2 == mid  # 去重
    assert len(s.sessions["s1"]["messages"]) == 1


async def test_append_message_new_session_noop(repo):
    s = _system(repo)
    assert await s._append_message("nope", "user", "x") == ""


async def test_persist_thought_real_db(repo):
    """真实落库 thought 消息"""
    s = _system(repo)
    await s._persist_thought("s1", "思考", tier="large")
    with conn.get_db_manager().get_session() as sess:
        from modules.database.chat_models import ChatMessage
        row = sess.query(ChatMessage).filter_by(session_id="s1").first()
        assert row is not None and row.role == "thought" and row.content == "思考"


async def test_processing_flag(repo):
    s = _system(repo)
    s.sessions["s1"] = {"messages": [], "processing": False, "running": True}
    assert await s._is_processing("s1") is False
    await s._set_processing("s1", True)
    assert await s._is_processing("s1") is True
    assert await s._is_processing("nope") is False


def test_format_scheduler_event_tool_call(repo):
    s = _system(repo)
    ev = {"type": "tool_call", "action": "run", "target": "read_file", "success": True}
    assert "工具 read_file run 成功" in s._format_scheduler_event(ev)["content"]


def test_format_scheduler_event_security(repo):
    s = _system(repo)
    ev = {"type": "security", "action": "scan", "target": "input", "payload": {"detail": "风险", "duration_ms": 12}}
    assert "安全审查" in s._format_scheduler_event(ev)["content"]


def test_format_scheduler_event_unknown(repo):
    s = _system(repo)
    ev = {"source": "abc", "action": "do", "target": "x"}
    assert "abc" in s._format_scheduler_event(ev)["content"]


def test_stop_session(repo, cm, server):
    s = _system(repo)
    task = asyncio.Task
    s.sessions["s1"] = {"running": True, "scheduler_task": None, "messages": []}

    async def go():
        await s.stop("s1")
    asyncio.run_coroutine_threadsafe(go(), server.loop).result(timeout=5)
    assert s.sessions["s1"]["running"] is False


async def test_emit_real_impl(cm, server):
    """_emit 真实实现：真实 ConnectionManager 发送"""
    s = _system(repo=None)
    s.sessions["s1"] = {"messages": [], "running": True}
    asyncio.run_coroutine_threadsafe(cm.connect("s1", _FakeWS()), server.loop).result(timeout=5)
    await s._emit("s1", {"event": "test"}, callback=None)
    ws = list(cm.active_connections.values())[0]
    assert ws.sent and ws.sent[0]["event"] == "test"
