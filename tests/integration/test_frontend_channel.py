"""frontend_channel 统一推送出口测试：握手 / 连续持久化 / 统一流程（真实组件，无 mock 伪造）

用真实 ConnectionManager + 独立线程事件循环 + 实现 send_json 接口的轻量 WebSocket
+ 真实临时 SQLite。无 MagicMock/伪造行为。
"""
import asyncio
import threading

import pytest

import modules.database.connection as conn
import modules.thinking.api_stream as stream_mod
import modules.thinking.frontend_channel as fc


class _FakeWS:
    """实现 WebSocket.send_json/accept 接口的轻量对象（协议实现，非 mock 伪造）"""

    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    async def accept(self):
        pass


class _LoopServer:
    """后台线程运行一个真实事件循环，供 ConnectionManager 跨线程发送"""

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
def cm(monkeypatch, server):
    """真实 ConnectionManager，绑定后台运行的主 loop"""
    mgr = stream_mod.ConnectionManager()
    mgr._loop = server.loop
    monkeypatch.setattr(stream_mod, "connection_manager", mgr)
    return mgr


@pytest.fixture
def dbm(tmp_path, monkeypatch):
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_fc.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    return dm


@pytest.fixture
def system(monkeypatch, dbm):
    """真实 StreamThinkingSystem + 真实会话 repo"""
    sys_ = stream_mod.StreamThinkingSystem()
    from modules.database.session_repo import SessionRepository
    sys_._session_repo = SessionRepository()
    monkeypatch.setattr(stream_mod, "get_thinking_system", lambda: sys_)
    return sys_


def _connect(cm, sid, server):
    """通过后台 loop 建立真实连接（保持 cm._loop = server.loop）"""
    asyncio.run_coroutine_threadsafe(cm.connect(sid, _FakeWS()), server.loop).result(timeout=5)


def _run_in_server(server, coro_factory):
    """在后台 loop 里执行完整协程（system/connection 同 loop，避免跨 loop）"""
    return asyncio.run_coroutine_threadsafe(coro_factory(), server.loop).result(timeout=15)


# ── confirm_frontend_connection ──

def test_confirm_no_connections(cm):
    assert fc.confirm_frontend_connection() is False


def test_confirm_broadcast_success(cm, server):
    _connect(cm, "s1", server)
    _connect(cm, "s2", server)
    assert fc.confirm_frontend_connection() is True


def test_confirm_by_session_hit(cm, server):
    _connect(cm, "s1", server)
    _connect(cm, "s2", server)
    assert fc.confirm_frontend_connection("s2") is True


def test_confirm_by_session_miss(cm, server):
    _connect(cm, "s1", server)
    assert fc.confirm_frontend_connection("nope") is False
    assert fc.confirm_frontend_connection("s1") is True


# ── push_content（连续持久化 + 推送）──

def test_push_content_agent_session_persist(cm, system, dbm, server):
    """agent 会话：_append_message 真实落内存 + 真实 DB + WS 推送"""

    async def go():
        await system.start("s1")
        await cm.connect("s1", _FakeWS())
        sent = await fc.push_content("s1", msg_type="proactive", event="test", content="内容")
        return sent, system.sessions["s1"]["messages"][-1]["content"]

    sent, content = _run_in_server(server, go)
    assert sent is True
    assert content == "内容"


def test_push_content_no_connections_persists(cm, system, dbm, server):
    """无活跃连接：消息仍持久化到真实 DB，返回 False"""

    async def go():
        await system.start("s1")
        return await fc.push_content("s1", msg_type="proactive", event="test", content="内容")

    sent = _run_in_server(server, go)
    assert sent is False  # 无连接
    with dbm.get_session() as s:
        from modules.database.chat_models import ChatMessage
        row = s.query(ChatMessage).filter_by(session_id="s1").first()
        assert row is not None and row.content == "内容"


def test_push_content_persist_false(cm, system, dbm, server):
    """persist=False：不落库，只推送"""

    async def go():
        await system.start("s1")
        await cm.connect("s1", _FakeWS())
        await fc.push_content("s1", msg_type="pet", event="pet_reply", content="回复", persist=False)

    _run_in_server(server, go)
    with dbm.get_session() as s:
        from modules.database.chat_models import ChatMessage
        assert s.query(ChatMessage).filter_by(session_id="s1").first() is None


# ── generate_and_push（统一流程）──

def test_generate_and_push_success(cm, system, dbm, server):
    """统一流程：握手 → LLM → 持久化 → 推送"""

    async def go():
        await system.start("s1")
        await cm.connect("s1", _FakeWS())

        async def llm():
            return "生成内容"

        return await fc.generate_and_push("s1", llm, msg_type="proactive", event="test")

    out = _run_in_server(server, go)
    assert out == "生成内容"
    with dbm.get_session() as s:
        from modules.database.chat_models import ChatMessage
        row = s.query(ChatMessage).filter_by(session_id="s1").first()
        assert row is not None and row.content == "生成内容"


def test_generate_and_push_handshake_fail_skips_llm(cm, system, server):
    """握手失败（无连接）→ 跳过 LLM"""

    async def go():
        await system.start("s1")
        called = {"llm": 0}

        async def llm():
            called["llm"] += 1
            return "内容"

        out = await fc.generate_and_push("s1", llm, msg_type="proactive", event="test")
        return out, called["llm"]

    out, llm_calls = _run_in_server(server, go)
    assert out is None
    assert llm_calls == 0


def test_generate_and_push_empty_llm(cm, system, server):
    """LLM 返回空 → 不推送"""

    async def go():
        await system.start("s1")
        await cm.connect("s1", _FakeWS())

        async def llm():
            return ""

        return await fc.generate_and_push("s1", llm, msg_type="proactive", event="test")

    assert _run_in_server(server, go) is None


def test_generate_and_push_broadcast_handshake(cm, system, dbm, server):
    """目标 session 无连接但其他连接在线 → 广播握手仍触发"""

    async def go():
        await system.start("target_session")
        await cm.connect("frontend_current", _FakeWS())

        async def llm():
            return "主动消息"

        return await fc.generate_and_push(
            "target_session", llm, msg_type="proactive", event="proactive_outreach"
        )

    assert _run_in_server(server, go) == "主动消息"
