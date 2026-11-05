"""前端代理 ↔ 后端 SSE 端到端（真实 uvicorn）

覆盖两件真实链路才能验证的事：

1. **刷新/关页即取消**：浏览器断开 → 代理关闭上游 → uvicorn 取消 SSE 生成器。
   桌宠 `max_loop_rounds=0` 的持续思考靠这条链路才能停下；否则会继续空转并占住
   串行队列，表现为「重新打开页面收不到任何消息」。
2. **/stream/ 下的普通 JSON 接口**（如历史）不能按 SSE 转发，否则浏览器拿不到
   Content-Length、请求永久挂起。
"""
import asyncio
import socket
import threading
import time

import pytest

pytest.importorskip("uvicorn")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

import frontend.server as srv  # noqa: E402
from utils import port_discovery  # noqa: E402


@pytest.fixture
def app_state():
    return {"started": threading.Event(), "cancelled": threading.Event()}


@pytest.fixture
def backend(app_state):
    """真实 uvicorn 后端（随机端口），SSE 生成器记录启动/取消"""
    app = FastAPI()

    @app.get("/stream/pet/history")
    async def history():
        return JSONResponse({"success": True, "data": {"messages": [{"role": "user", "content": "hi"}]}})

    @app.post("/stream/pet/chat")
    async def chat():
        async def gen():
            app_state["started"].set()
            try:
                for i in range(2000):
                    yield f'event: token\ndata: {{"token":"{i}"}}\n\n'
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                app_state["cancelled"].set()
                raise

        return StreamingResponse(gen(), media_type="text/event-stream")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started, "uvicorn 未在 10s 内启动"
    yield port
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def proxy(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "port.json"))
    port_discovery.save_backend_port(backend)
    server = srv.create_server(0)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()


def _open_sse(proxy_port):
    client = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
    client.sendall(
        b"POST /api/stream/pet/chat HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Accept: text/event-stream\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 2\r\n\r\n{}"
    )
    return client


def test_client_disconnect_cancels_backend_generator(proxy, backend, app_state):
    """浏览器关页 → 代理断开上游 → 后端生成器被取消（桌宠停止空转）"""
    client = _open_sse(proxy)
    try:
        received = b""
        deadline = time.time() + 10
        while b"event: token" not in received and time.time() < deadline:
            received += client.recv(4096)
        assert b"event: token" in received
        assert app_state["started"].wait(5)
    finally:
        client.close()

    assert app_state["cancelled"].wait(10), "客户端断开后后端 SSE 生成器仍在运行（会继续空转并占住队列）"


def test_json_endpoint_through_proxy_is_complete(proxy):
    """/stream/pet/history 是普通 JSON：必须带 Content-Length 并正常结束，不能挂起"""
    client = socket.create_connection(("127.0.0.1", proxy), timeout=10)
    try:
        client.sendall(
            b"GET /api/stream/pet/history HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Accept: application/json\r\n\r\n"
        )
        data = b""
        deadline = time.time() + 5
        client.settimeout(5)
        while time.time() < deadline:
            try:
                chunk = client.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
            if b'"role"' in data:
                break
    finally:
        client.close()

    assert b"200" in data.split(b"\r\n", 1)[0]
    assert b"Content-Length" in data
    assert b'"role": "user"' in data or b'"role":"user"' in data
    # 应答体在连接关闭前完整到达（不依赖 keep-alive 超时）
    assert data.rstrip().endswith(b"}")


def test_sse_frames_are_forwarded_incrementally(proxy):
    """逐帧转发：第一帧要在长连接结束前就到达（不是攒完再发）"""
    client = _open_sse(proxy)
    try:
        client.settimeout(5)
        got = b""
        deadline = time.time() + 10
        while got.count(b"event: token") < 1 and time.time() < deadline:
            got += client.recv(4096)
        assert b"event: token" in got
        # 第二帧紧随其后（流式转发，不是攒完整轮再发）
        while got.count(b"event: token") < 2 and time.time() < deadline:
            got += client.recv(4096)
        assert got.count(b"event: token") >= 2
    finally:
        client.close()


def test_browser_disconnect_stops_pet_engine(monkeypatch, tmp_path):
    """真实链路：浏览器断开 → 代理断开上游 → 桌宠引擎停止继续思考

    全程只用真实组件：uvicorn + 真实桌宠路由 + 真实 PetEngine / PetTaskQueue /
    CustomLoop / SSE；仅把「模型」换成本地假 client（无 API Key 环境无法真调
    LLM，这是唯一被替换的外部依赖）。断言断开后模型调用次数不再增长——即
    `max_loop_rounds=0` 的持续思考确实被取消，不会空转占住串行队列。
    """
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import modules.desktop_pet.store as store_mod
    from modules.desktop_pet import api as pet_api
    from modules.desktop_pet.pet_engine import PetEngine

    old_engine = PetEngine._instance
    PetEngine._instance = None
    store = store_mod.PetStore(str(tmp_path / "pet_proxy.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    store.update_config({
        "loop_type": "custom", "min_think_interval": 0, "max_loop_rounds": 0,
        "tools_enabled": False, "voice_enabled": False,
    })
    # 上下文采集（屏幕/语音等）不打真实设备；模型换成计数假 client
    monkeypatch.setattr(PetEngine, "build_context", AsyncMock(return_value=""))
    engine = PetEngine.get_instance()
    calls = {"n": 0}
    ctl = {"hold": False, "cancelled": threading.Event()}

    class _StreamingClient:
        """前两轮正常产出；之后进入“长时间思考”（挂起），模拟桌宠静默期

        静默期没有任何下游写操作 —— 这正是旧代理发现不了浏览器断开的场景。
        任务被取消时记录 CancelledError，作为“引擎真的停了”的唯一依据。
        """

        async def chat_stream(self, messages=None, max_tokens=None, on_token=None, **kw):
            if ctl["hold"]:
                try:
                    await asyncio.Event().wait()  # 永不返回
                except asyncio.CancelledError:
                    ctl["cancelled"].set()
                    raise
            calls["n"] += 1
            text = f"第{calls['n']}轮思考"
            for ch in text:
                if on_token:
                    on_token(ch)
                await asyncio.sleep(0.02)
            return SimpleNamespace(message=SimpleNamespace(content=text))

    engine._client = _StreamingClient()
    engine._client_cfg = ("", "", "", "", "")

    app = FastAPI()
    app.include_router(pet_api.router)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    backend_port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=backend_port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started, "uvicorn 未在 10s 内启动"

    monkeypatch.setattr(port_discovery, "_PORT_FILE", str(tmp_path / "port.json"))
    port_discovery.save_backend_port(backend_port)
    proxy = srv.create_server(0)
    proxy.daemon_threads = True
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    try:
        client = _open_sse(proxy.server_address[1])
        try:
            client.settimeout(10)
            got = b""
            # 先证明"持续思考"真的在跑：连上期间必须产出至少两轮
            # （若这里只等到 1 轮就断开，下面的"不再增长"断言会轻易 vacuously 通过）
            deadline = time.time() + 20
            while got.count(b"round_complete") < 2 and time.time() < deadline:
                got += client.recv(4096)
            assert b"event: token" in got, "桌宠 SSE 未产出 token"
            assert got.count(b"round_complete") >= 2, "连续思考未持续产出（无法验证断开后是否停止）"
            assert calls["n"] >= 2
            # 进入静默期：上游长时间无字节，模拟模型思考中（旧代理正是在这里
            # 发现不了浏览器断开 —— 没有任何写操作会失败）
            ctl["hold"] = True
            time.sleep(0.8)
        finally:
            client.close()

        assert ctl["cancelled"].wait(10), (
            "浏览器断开后桌宠思考任务未被取消（静默期不会写失败，只能靠代理主动断连）"
        )
    finally:
        proxy.shutdown()
        server.should_exit = True
        thread.join(timeout=10)
        PetEngine._instance = old_engine
        store_mod.PetStore._instance = None
