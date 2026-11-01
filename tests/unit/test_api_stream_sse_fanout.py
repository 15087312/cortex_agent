"""api_stream 的 SSE 出口：工具/流式/投影帧的扇出与去重。

背景（docs/ERRORS_AND_FIXES.md §99）：`ModelRunner._push_tool_event`、
`_push_stream_chunk`、`projections`、`pending_requests` 都直接把帧投给
`connection_manager`，不经 `think(callback=...)` 的 queue。SSE 路线原先没有
订阅入口，因此只看得到调度事件——看不到工具卡片、流式正文与投影。
"""
import asyncio
import json
from unittest.mock import AsyncMock

import modules.thinking.api_stream as stream
from modules.thinking.api_stream import ConnectionManager, _build_event


# ── ConnectionManager：SSE 订阅 ────────────────────────────────────────

async def test_subscribe_stream_receives_send_json():
    cm = ConnectionManager()
    q = cm.subscribe_stream("s1")
    frame = _build_event(session_id="s1", msg_type="tool", event="tool_call",
                         role="large", data={"call_id": "c1"})

    await cm.send_json("s1", frame)          # 没有 WebSocket，只有 SSE
    assert q.get_nowait() is frame

    cm.unsubscribe_stream("s1", q)
    await cm.send_json("s1", frame)
    assert q.empty()
    assert cm.has_stream("s1") is False


async def test_send_json_from_thread_delivers_to_stream_only():
    """纯 SSE 客户端没有 WS 连接，但帧必须送到（否则工具事件全丢）。"""
    cm = ConnectionManager()
    cm._loop = asyncio.get_running_loop()
    q = cm.subscribe_stream("s1")
    frame = _build_event(session_id="s1", msg_type="tool", event="tool_result",
                         role="large", data={"call_id": "c1"})

    assert cm.send_json_from_thread("s1", frame) is True
    await asyncio.sleep(0.01)                # fire-and-forget 任务落地
    assert q.get_nowait() is frame


async def test_send_json_from_thread_without_any_sink_returns_false():
    cm = ConnectionManager()
    cm._loop = asyncio.get_running_loop()
    frame = _build_event(session_id="s1", msg_type="tool", event="tool_call")
    assert cm.send_json_from_thread("s1", frame) is False


async def test_push_sessions_unions_ws_and_sse_without_duplicates():
    """推送/清理判定必须同时看到 WS 与 SSE 出口（§100.6）"""
    cm = ConnectionManager()
    ws = AsyncMock()
    await cm.connect("s_ws", ws)
    q = cm.subscribe_stream("s_sse")
    q2 = cm.subscribe_stream("s_ws")   # 同一会话既有 WS 又有 SSE

    assert cm.push_sessions() == ["s_ws", "s_sse"]

    cm.unsubscribe_stream("s_ws", q2)
    cm.unsubscribe_stream("s_sse", q)
    assert cm.push_sessions() == ["s_ws"]


async def test_multiple_stream_subscribers_each_get_a_copy():
    cm = ConnectionManager()
    q1 = cm.subscribe_stream("s1")
    q2 = cm.subscribe_stream("s1")
    frame = _build_event(session_id="s1", msg_type="chunk", event="chunk_delta", content="你好")

    await cm.send_json("s1", frame)
    assert q1.get_nowait() is frame
    assert q2.get_nowait() is frame


# ── _stream_sse：接到工具帧、且不重复下发调度事件 ──────────────────────

class _FakeSystem:
    """最小调度系统替身：think 里模拟「既有 callback 又有扇出」的真实 _emit。"""

    def __init__(self):
        self.started = False

    async def start(self, session_id):
        self.started = True

    async def think(self, session_id, user_input, callback=None):
        # 1) 调度事件：真实实现里 _emit 会同时走 callback 和 connection_manager
        env = _build_event(session_id=session_id, msg_type="ack", event="received",
                           content="已接收请求，开始处理", role="system")
        if callback:
            await callback(env)
        await stream.connection_manager.send_json(session_id, env)
        # 2) 工具帧：只走 connection_manager（模拟 model_runner._push_tool_event）
        tool_env = _build_event(session_id=session_id, msg_type="tool", event="tool_call",
                                role="large",
                                data={"call_id": "c1", "name": "read_file", "status": "running"})
        await stream.connection_manager.send_json(session_id, tool_env)
        await asyncio.sleep(0)


async def test_stream_sse_receives_tool_frames_without_duplicates(monkeypatch):
    fake = _FakeSystem()
    monkeypatch.setattr(stream, "get_thinking_system", lambda: fake)
    # 其他用例可能留着同会话的订阅（未关闭的生成器），只比较前后差异
    before = set(stream.connection_manager._streams.get("s1") or ())

    events = [e async for e in stream._stream_sse("s1", "问题")]
    names = [e["event"] for e in events]

    assert fake.started is True
    assert names.count("received") == 1          # 同一 envelope 只下发一次
    assert "tool_call" in names
    tool = json.loads(events[names.index("tool_call")]["data"])
    assert tool["data"]["name"] == "read_file"
    # 生成器正常跑完后自己的订阅已注销，不残留队列
    after = set(stream.connection_manager._streams.get("s1") or ())
    assert after == before


# ── _stream_sse：客户端断开后的清理 ────────────────────────────────────
# WS 断开时会拒绝待审批/待交互并清空待办；SSE 之前只注销订阅，于是审批
# future 永久挂起 + Suspension 计时冻结，思考任务也一直跑下去。

class _BlockingSystem:
    def __init__(self):
        self.cancelled = False
        self.sessions = {}

    async def start(self, session_id):
        return None

    async def think(self, session_id, user_input, callback=None):
        env = _build_event(session_id=session_id, msg_type="ack", event="received",
                           content="已接收请求，开始处理", role="system")
        if callback:
            await callback(env)
        try:
            await asyncio.Event().wait()      # 永不返回，直到客户端断开被取消
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_stream_sse_disconnect_cleans_up(monkeypatch):
    fake = _BlockingSystem()
    calls = []
    monkeypatch.setattr(stream, "get_thinking_system", lambda: fake)

    import modules.security_system.tool_security_gate as gate
    import modules.thinking.pending_requests as pending
    import modules.thinking.core.model_runner as runner
    monkeypatch.setattr(gate.ToolSecurityGate, "reject_session_reviews",
                        classmethod(lambda cls, sid: calls.append(("reviews", sid))))
    monkeypatch.setattr(runner, "reject_session_user_responses",
                        lambda sid: calls.append(("responses", sid)))
    monkeypatch.setattr(pending, "clear", lambda sid: calls.append(("pending", sid)))

    agen = stream._stream_sse("s2", "问题")
    first = await agen.__anext__()
    assert first["event"] == "received"
    assert stream.connection_manager.has_stream("s2") is True
    fake.sessions["s2"] = {"processing": True}

    await agen.aclose()                        # 客户端断开
    for _ in range(5):                         # 让取消与清理任务落地
        await asyncio.sleep(0)

    assert stream.connection_manager.has_stream("s2") is False
    assert ("reviews", "s2") in calls
    assert ("responses", "s2") in calls
    assert ("pending", "s2") in calls
    assert fake.cancelled is True              # 断开后不再继续烧 token
    # 会话不能停在“处理中”，否则下一个请求永远是 busy
    assert fake.sessions["s2"]["processing"] is False
