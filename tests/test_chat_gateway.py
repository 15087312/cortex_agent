"""
Chat Gateway 协议级端到端测试。

覆盖 chatonly 路线的完整 envelope 协议：
  - WS 全流程: session_ready → received → thinking_step(聚合) → assistant_message → done
  - WS 错误路径: error 事件，且不再发「处理完成」done
  - WS stop: 取消运行中的思考任务、收到 stopped 事件、连接保持可复用
  - WS 心跳: 静默期收到 thinking_progress（TUI 防超时）
  - SSE 全流程: assistant_message + done
  - REST: session 创建/列表/消息/上下文/删除
  - 模式分流: agent 模式委托 api_stream、chatonly 走简单路线
  - schema 对齐迁移（幂等补列）
"""
import asyncio
import os
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.thinking import chat_gateway  # noqa: E402


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------

class FakeThinker:
    """模拟 backend ContinuousThinker：向 queue 投递 token / done / error。"""

    def __init__(self, chunks=("你", "好", "，", "世", "界"), delay=0.0, error=None):
        self.chunks = chunks
        self.delay = delay
        self.error = error
        self.cancelled = False
        self.calls = []
        self._blackboard = FakeBlackboard()

    async def think(self, session_id, content, queue):
        self.calls.append((session_id, content))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            for c in self.chunks:
                await queue.put({"type": "message", "content": c})
            if self.error:
                await queue.put({"type": "error", "content": self.error})
            else:
                await queue.put({"type": "done"})
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def get_blackboard(self):
        return self._blackboard


class FakeBlackboard:
    def __init__(self):
        self.messages = []

    def get_messages(self, session_id):
        return self.messages

    def add_message(self, session_id, role, content):
        self.messages.append({"role": role, "content": content})


class FakeRepo:
    """模拟 backend SessionRepository（内存版）。"""

    def __init__(self):
        self.sessions = {}
        self.messages = {}

    def create_session(self, sid):
        self.sessions.setdefault(sid, {
            "session_id": sid, "title": "", "is_active": True,
            "message_count": 0, "last_active": "",
        })

    def save_message(self, sid, role, content, round_num=0):
        if not content or not content.strip():
            return
        self.messages.setdefault(sid, []).append({
            "role": role, "content": content, "round_num": round_num,
        })
        self.sessions.setdefault(sid, {})["message_count"] = len(self.messages[sid])

    def get_messages(self, sid, limit=100):
        return self.messages.get(sid, [])[:limit]

    def get_all_sessions(self, limit=50):
        return list(self.sessions.values())[:limit]

    def delete_session(self, sid):
        self.sessions.pop(sid, None)
        self.messages.pop(sid, None)
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_thinker():
    return FakeThinker()


@pytest.fixture
def fake_repo():
    return FakeRepo()


@pytest.fixture
def gw_app(fake_thinker, fake_repo, monkeypatch):
    """挂载 chat_gateway 路由的最小 app + 替换 thinker/repo。"""
    # 避免 schema 对齐触及真实 data/memory.db
    monkeypatch.setattr(chat_gateway, "ensure_shared_schema", lambda: None)
    monkeypatch.setattr(chat_gateway, "_get_chat_thinker", lambda: fake_thinker)
    monkeypatch.setattr(chat_gateway, "_get_chat_session_repo", lambda: fake_repo)

    app = FastAPI()

    # 与 api/main.py 一致：注册 AppError 异常处理器（SSE 400 校验依赖它）
    from fastapi.responses import JSONResponse
    from api.errors import AppError

    @app.exception_handler(AppError)
    async def _app_error_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(chat_gateway.router)
    return app, fake_thinker, fake_repo


@pytest.fixture(autouse=True)
def chatonly_mode(monkeypatch):
    monkeypatch.setenv("CORTEX_MODE", "chatonly")


def _drain_ws(ws, until_event):
    """持续读取 WS 事件直到出现 until_event，返回事件列表。"""
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["event"] == until_event:
            return events


# ---------------------------------------------------------------------------
# WS 全流程
# ---------------------------------------------------------------------------

def test_ws_full_flow(gw_app):
    app, thinker, repo = gw_app
    with TestClient(app) as client:
        with client.websocket_connect("/stream/ws/ses_flow1") as ws:
            ev = ws.receive_json()
            assert ev["event"] == "session_ready"
            assert ev["type"] == "ack"

            ws.send_json({"type": "input", "content": "你好"})

            events = _drain_ws(ws, "done")

    # 事件序列: received → thinking_step* → assistant_message → done
    first = events[0]
    assert first["event"] == "received"
    assert any(e["event"] == "thinking_step" for e in events)
    assert any(e["event"] == "assistant_message" for e in events)
    assert events[-1]["event"] == "done"

    # assistant_message 内容 = 所有 token 拼接
    am = next(e for e in events if e["event"] == "assistant_message")
    assert am["role"] == "main"
    assert am["content"] == "你好，世界"
    assert isinstance(am["timestamp"], float)

    # 消息持久化到 repo（user + assistant）
    assert [m["role"] for m in repo.get_messages("ses_flow1")] == ["user", "assistant"]
    assert thinker.calls == [("ses_flow1", "你好")]


def test_ws_plain_text_message(gw_app):
    """客户端发纯文本（非 JSON）也应被当作 input 处理。"""
    app, thinker, repo = gw_app
    with TestClient(app) as client:
        with client.websocket_connect("/stream/ws/ses_plain1") as ws:
            ws.receive_json()  # session_ready
            ws.send_text("纯文本消息")
            events = _drain_ws(ws, "done")
    assert any(e["event"] == "assistant_message" for e in events)
    assert thinker.calls == [("ses_plain1", "纯文本消息")]


# ---------------------------------------------------------------------------
# WS 错误路径
# ---------------------------------------------------------------------------

def test_ws_error_path(gw_app, monkeypatch):
    app = gw_app[0]
    monkeypatch.setattr(chat_gateway, "_get_chat_thinker",
                        lambda: FakeThinker(error="模型调用失败"))
    with TestClient(app) as client:
        with client.websocket_connect("/stream/ws/ses_err1") as ws:
            ws.receive_json()  # session_ready
            ws.send_json({"type": "input", "content": "触发错误"})
            events = _drain_ws(ws, "error")

    assert events[-1]["event"] == "error"
    assert events[-1]["content"] == "模型调用失败"
    # 失败路径不再发「处理完成」done
    assert not any(e["event"] == "done" for e in events)
    assert not any(e["event"] == "assistant_message" for e in events)


# ---------------------------------------------------------------------------
# WS stop
# ---------------------------------------------------------------------------

def test_ws_stop_cancels_active_task(gw_app, monkeypatch):
    app = gw_app[0]
    slow = FakeThinker(delay=30.0)  # 长时间思考
    monkeypatch.setattr(chat_gateway, "_get_chat_thinker", lambda: slow)

    with TestClient(app) as client:
        with client.websocket_connect("/stream/ws/ses_stop1") as ws:
            ws.receive_json()  # session_ready
            ws.send_json({"type": "input", "content": "开始长思考"})
            ev = ws.receive_json()
            assert ev["event"] == "received"

            # 思考进行中发送 stop
            ws.send_json({"type": "stop"})
            ev = ws.receive_json()
            assert ev["event"] == "stopped"

            # 连接保持，可继续发 ping → pong
            ws.send_json({"type": "ping"})
            ev = ws.receive_json()
            assert ev["event"] == "pong"

            # 且可复用连接发送新消息（换回快速 thinker，避免第二轮也等 30s）
            monkeypatch.setattr(chat_gateway, "_get_chat_thinker", lambda: FakeThinker())
            ws.send_json({"type": "input", "content": "第二轮"})
            events = _drain_ws(ws, "done")

    assert any(e["event"] == "assistant_message" for e in events)
    assert slow.cancelled, "进行中的思考任务应被取消"


# ---------------------------------------------------------------------------
# WS 心跳（静默期 thinking_progress）
# ---------------------------------------------------------------------------

def test_ws_heartbeat_during_silence(gw_app, monkeypatch):
    app = gw_app[0]
    monkeypatch.setattr(chat_gateway, "_get_chat_thinker",
                        lambda: FakeThinker(delay=1.5))

    with TestClient(app) as client:
        with client.websocket_connect("/stream/ws/ses_hb1") as ws:
            ws.receive_json()  # session_ready
            ws.send_json({"type": "input", "content": "慢思考"})
            events = _drain_ws(ws, "done")

    # 首个 token 到达前应有 thinking_progress 心跳
    assert any(e["event"] == "thinking_progress" for e in events)
    assert any(e["event"] == "thinking_step" for e in events)
    assert events[-1]["event"] == "done"


# ---------------------------------------------------------------------------
# SSE 全流程
# ---------------------------------------------------------------------------

def test_sse_full_flow(gw_app):
    app, thinker, repo = gw_app
    with TestClient(app) as client:
        with client.stream("GET", "/stream/sse/ses_sse1?question=你好") as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

    assert '"assistant_message"' in body
    assert '"你好，世界"' in body
    assert '"done"' in body
    assert thinker.calls == [("ses_sse1", "你好")]


def test_sse_requires_question(gw_app):
    app = gw_app[0]
    with TestClient(app) as client:
        resp = client.get("/stream/sse/ses_x?question=")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# REST 端点
# ---------------------------------------------------------------------------

def test_rest_session_lifecycle(gw_app):
    app, thinker, repo = gw_app
    with TestClient(app) as client:
        # 创建会话
        resp = client.post("/stream/session")
        assert resp.status_code == 200
        sid = resp.json()["data"]["session_id"]
        assert sid.startswith("ses_")

        # 状态
        resp = client.get("/stream/status")
        assert resp.json()["data"]["running"] is True

        # 会话列表包含新建会话
        resp = client.get("/stream/sessions")
        assert any(s["session_id"] == sid for s in resp.json()["data"])

        # 消息列表（空）
        resp = client.get(f"/stream/sessions/{sid}/messages")
        assert resp.json()["data"] == []

        # 写入一条消息后查询
        repo.save_message(sid, "user", "测试消息")
        resp = client.get(f"/stream/sessions/{sid}/messages")
        assert resp.json()["data"][0]["content"] == "测试消息"

        # 上下文（来自 thinker blackboard）
        thinker.get_blackboard().add_message(sid, "user", "黑板消息")
        resp = client.get(f"/stream/context/{sid}")
        assert resp.json()["data"]["count"] == 1

        # 删除会话
        resp = client.delete(f"/stream/session/{sid}")
        assert resp.json()["success"] is True
        resp = client.get("/stream/sessions")
        assert not any(s["session_id"] == sid for s in resp.json()["data"])


# ---------------------------------------------------------------------------
# 模式分流
# ---------------------------------------------------------------------------

def test_resolve_mode_defaults_to_agent(monkeypatch):
    monkeypatch.delenv("CORTEX_MODE", raising=False)
    assert chat_gateway._resolve_mode() == "agent"


def test_resolve_mode_chatonly_variants(monkeypatch):
    for variant in ("chatonly", "chat_only", "chat-only", "CHATONLY"):
        monkeypatch.setenv("CORTEX_MODE", variant)
        assert chat_gateway._resolve_mode() == "chatonly", variant


def test_agent_mode_delegates_to_api_stream(gw_app, monkeypatch):
    """agent 模式下 REST 端点应委托给 api_stream（不建本地会话）。"""
    app = gw_app[0]
    monkeypatch.setenv("CORTEX_MODE", "agent")

    # 注入假 api_stream 模块，避免加载重型真实模块
    fake_stream = types.ModuleType("modules.thinking.api_stream")

    async def fake_create_session():
        return {"success": True, "data": {"session_id": "agent_ses", "origin": "api_stream"}}

    async def fake_get_status():
        return {"success": True, "data": {"running": True, "origin": "api_stream"}}

    fake_stream.create_session = fake_create_session
    fake_stream.get_status = fake_get_status
    # 同时 patch sys.modules 和父包属性：`from modules.thinking import api_stream`
    # 在 chat_gateway 函数内执行时，Python 会取 sys.modules["modules.thinking"].api_stream
    # 父包属性（若已绑定），仅替换 sys.modules 不够。完整套件下父包属性已指向
    # 真实模块，必须一起替换才能让 chat_gateway 委托到 fake。
    monkeypatch.setitem(sys.modules, "modules.thinking.api_stream", fake_stream)
    parent_pkg = sys.modules.get("modules.thinking")
    if parent_pkg is not None and getattr(parent_pkg, "api_stream", None) is not None:
        monkeypatch.setattr(parent_pkg, "api_stream", fake_stream)

    with TestClient(app) as client:
        resp = client.post("/stream/session")
        assert resp.json()["data"]["origin"] == "api_stream"
        resp = client.get("/stream/status")
        assert resp.json()["data"]["origin"] == "api_stream"

    # chatonly repo 不应被触碰
    assert gw_app[2].sessions == {}


# ---------------------------------------------------------------------------
# schema 对齐迁移（幂等补列）
# ---------------------------------------------------------------------------

def test_ensure_shared_schema_adds_missing_columns(tmp_path, monkeypatch):
    import sqlite3

    db_path = str(tmp_path / "align.db")
    conn = sqlite3.connect(db_path)
    # 模拟 backend 精简版建表（缺列）
    conn.execute("CREATE TABLE chat_sessions ("
                 "id TEXT PRIMARY KEY, session_id TEXT, title TEXT, "
                 "created_at TEXT, last_active TEXT, message_count INTEGER, is_active INTEGER)")
    conn.execute("CREATE TABLE chat_messages ("
                 "id TEXT PRIMARY KEY, session_id TEXT, role TEXT, "
                 "content TEXT, created_at TEXT, round_num INTEGER)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("MEMORY_DB_PATH", db_path)
    monkeypatch.setattr(chat_gateway, "_schema_checked", False)

    chat_gateway.ensure_shared_schema()

    conn = sqlite3.connect(db_path)
    s_cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)")}
    m_cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)")}
    conn.close()

    assert {"execution_mode", "metadata_json"} <= s_cols
    assert {"tier", "metadata_json"} <= m_cols
    # 幂等：再跑一次不报错
    chat_gateway.ensure_shared_schema()
