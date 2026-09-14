"""桌宠 CustomLoop 端到端测试 — HTTP 层真实链路

覆盖：配置写入回读 → SSE 对话流（token/done）→ 独立历史落库 →
工具调用（真实 ToolManager 执行 calc）→ 白名单拦截。
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.desktop_pet.pet_engine import PetEngine


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    old = PetEngine._instance
    PetEngine._instance = None
    yield
    PetEngine._instance = old


@pytest.fixture(autouse=True)
def _pet_db(tmp_path, monkeypatch):
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_e2e.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    yield store
    store_mod.PetStore._instance = None



def _tokens_of(body: str) -> str:
    """从 SSE body 提取并拼接全部 token 帧（逐 token 流式后按帧比对）"""
    out = []
    for line in body.split("\r\n"):
        if line.startswith("data:"):
            try:
                d = json.loads(line[5:].strip())
                if d.get("token") is not None:
                    out.append(d["token"])
            except Exception:
                continue
    return "".join(out)


@pytest.fixture(autouse=True)
def _stub_mcp(monkeypatch):
    """stub MCP 服务（真实 ToolManager 链路，仅替换执行后端，避免测试环境挂起）"""
    import infra.mcp.factory as factory

    class _FakeService:
        def execute(self, request):
            return SimpleNamespace(
                success=True, result="2", error=None, latency_ms=1,
                to_legacy_dict=lambda: {"success": True, "result": "2", "error": None},
            )

        def list_tools(self, source=None):
            return {}

        def get_tool(self, name):
            return None

    monkeypatch.setattr(factory, "get_mcp_tool_service", lambda: _FakeService())


@pytest.fixture
def app(monkeypatch, tmp_path):
    """最小 app：桌宠路由 + 前端连接/状态/广播桩

    注意：不 patch confirm_frontend_connection —— 桌宠页面形态不依赖 WS 握手，
    此 fixture 保持真实路径，任何回归（重新引入握手检查）都会在无 WS 环境下失败。
    """
    from modules.desktop_pet.pet_state import PetState
    st = PetState(path=str(tmp_path / "pet_state.json"))
    monkeypatch.setattr(PetState, "get_instance", lambda: st)

    from modules.desktop_pet import api as pet_api
    app = FastAPI()
    app.include_router(pet_api.router)
    return app


class _FakeClient:
    """预设输出的流式 client"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def chat_stream(self, messages=None, max_tokens=None, on_token=None, **kw):
        self.calls.append({"max_tokens": max_tokens})
        content = self.outputs.pop(0) if self.outputs else ""
        for ch in content:
            if on_token:
                on_token(ch)
        return SimpleNamespace(message=SimpleNamespace(content=content))


def _install_fake_client(monkeypatch, outputs):
    e = PetEngine.get_instance()
    fake = _FakeClient(outputs)
    e._client = fake
    e._client_cfg = ("", "", "", "", "")
    return fake


def _setup_custom(monkeypatch, **extra):
    """配置 loop_type=custom + 引擎假 client"""
    from modules.desktop_pet.store import PetStore
    patch = {"loop_type": "custom", "min_think_interval": 0}
    patch.update(extra)
    PetStore.get_instance().update_config(patch)
    fake = _install_fake_client(monkeypatch, [])
    return fake


# ── 配置端到端 ─────────────────────────────────────────────────────────────

def test_config_custom_roundtrip(app):
    with TestClient(app) as c:
        r = c.put("/stream/pet/config", json={
            "loop_type": "custom",
            "min_think_interval": 7.5,
            "tools_enabled": False,
            "max_history": 4,
            "max_output_tokens": 256,
            "max_loop_rounds": 5,
        })
        assert r.status_code == 200 and r.json()["success"]
        data = r.json()["data"]
        assert data["loop_type"] == "custom"
        assert data["min_think_interval"] == 7.5
        assert data["tools_enabled"] is False
        assert data["max_history"] == 4
        assert data["max_output_tokens"] == 256
        assert data["max_loop_rounds"] == 5
        # 回读
        g = c.get("/stream/pet/config")
        assert g.json()["data"]["min_think_interval"] == 7.5


def test_history_delete_single(app, monkeypatch, _pet_db):
    """删除单条对话：DELETE /stream/pet/history/{id}，不存在返回失败"""
    _pet_db.add_history("user", "问1")
    _pet_db.add_history("assistant", "答1")
    _pet_db.add_history("user", "问2")
    rows = _pet_db.get_history(limit=10)
    mid = rows[1]["id"]
    with TestClient(app) as c:
        r = c.delete(f"/stream/pet/history/{mid}")
        assert r.status_code == 200
        assert r.json()["success"] is True
        # 已删除的 id 再删 → not found
        r2 = c.delete(f"/stream/pet/history/{mid}")
        assert r2.json()["success"] is False
        assert r2.json()["error"] == "not found"
    left = [m["content"] for m in _pet_db.get_history(limit=10)]
    assert left == ["问1", "问2"]


def test_config_clamps_bad_values(app):
    with TestClient(app) as c:
        r = c.put("/stream/pet/config", json={
            "min_think_interval": -3,
            "max_history": 0,
            "max_loop_rounds": -1,
        })
        data = r.json()["data"]
        assert data["min_think_interval"] == 0.0
        assert data["max_history"] >= 1
        # 0 = 持续思考循环（合法值）；负数 clamp 到 0
        assert data["max_loop_rounds"] == 0


# ── SSE 对话端到端 ─────────────────────────────────────────────────────────

def test_chat_sse_plain_text(app, monkeypatch, _pet_db):
    _setup_custom(monkeypatch)
    PetEngine.get_instance()._client.outputs = ["你好呀"]
    with TestClient(app) as c:
        with c.stream("POST", "/stream/pet/chat", json={"text": "hello"}) as r:
            body = r.read().decode()
            assert _tokens_of(body) == "你好呀"
            assert "done" in body
    # 独立历史落库（user + assistant）
    hist = _pet_db.get_history(limit=10)
    roles = [m["role"] for m in hist]
    assert roles == ["user", "assistant"]
    assert hist[-1]["content"] == "你好呀"


def test_chat_sse_tool_then_text(app, monkeypatch):
    """工具端到端：真实 ToolManager 执行 calc（1+1=2）→ 最终文本流式返回"""
    _setup_custom(monkeypatch)
    PetEngine.get_instance()._client.outputs = [
        '{"tool":"calc","params":{"expr":"1+1"}}',
        "结果是 2",
    ]
    with TestClient(app) as c:
        with c.stream("POST", "/stream/pet/chat", json={"text": "1+1 等于几"}) as r:
            body = r.read().decode()
            assert _tokens_of(body) == "结果是 2"
            assert "done" in body


def test_chat_sse_whitelist_blocks(app, monkeypatch, _pet_db):
    """白名单拦截端到端：exec_command 不执行，观察注入后正常回复"""
    _setup_custom(monkeypatch)
    PetEngine.get_instance()._client.outputs = [
        '{"tool":"exec_command","params":{"cmd":"touch /tmp/pet_e2e_probe"}}',
        "这个工具我不能用",
    ]
    with TestClient(app) as c:
        with c.stream("POST", "/stream/pet/chat", json={"text": "执行命令"}) as r:
            body = r.read().decode()
            assert _tokens_of(body) == "这个工具我不能用"
    # 历史落库确认回复
    hist = _pet_db.get_history(limit=10)
    assert hist[-1]["content"] == "这个工具我不能用"


def test_chat_empty_text_proactive_think(app, monkeypatch, _pet_db):
    """空输入 = 主动思考模式（页面加载自动触发）：不走 error，正常产出回复并落库"""
    _install_fake_client(monkeypatch, ["自己想想说的话"])
    with TestClient(app) as c:
        with c.stream("POST", "/stream/pet/chat", json={"text": "  "}) as r:
            body = r.read().decode()
            assert "error" not in body
            assert _tokens_of(body) == "自己想想说的话"
            assert "done" in body


def test_chat_with_action_id(app, monkeypatch):
    """动作（如 cake）走状态注入路径，仍由 custom 循环输出"""
    _setup_custom(monkeypatch)
    PetEngine.get_instance()._client.outputs = ["谢谢你的蛋糕！"]
    with TestClient(app) as c:
        with c.stream("POST", "/stream/pet/chat", json={"action_id": "cake"}) as r:
            body = r.read().decode()
            assert _tokens_of(body) == "谢谢你的蛋糕！"


def test_chat_sse_direct_loop_default(app, monkeypatch, _pet_db):
    """默认 direct 循环流式：token + done + 历史落库
    （回归：DirectLoop.run 不接受 on_reasoning/on_message kwargs → 流式必 TypeError）"""
    _install_fake_client(monkeypatch, ["你好呀"])
    with TestClient(app) as c:
        with c.stream("POST", "/stream/pet/chat", json={"text": "hello"}) as r:
            body = r.read().decode()
            assert _tokens_of(body) == "你好呀"
            assert "done" in body
    hist = _pet_db.get_history(limit=10)
    roles = [m["role"] for m in hist]
    assert roles == ["user", "assistant"]
    assert hist[-1]["content"] == "你好呀"


def test_chat_sse_continuous_loop_multiple_rounds(app, monkeypatch, _pet_db):
    """持续思考循环：多轮文本全部流式返回（max_loop_rounds=0），每轮均持久化"""
    _setup_custom(monkeypatch, max_loop_rounds=0)
    PetEngine.get_instance()._client.outputs = ["第一轮", "第二轮", "第三轮"]
    with TestClient(app) as c:
        with c.stream("POST", "/stream/pet/chat", json={"text": "hi"}) as r:
            body = r.read().decode()
            assert _tokens_of(body) == "第一轮第二轮第三轮"
            assert "done" in body
    hist = _pet_db.get_history(limit=10)
    roles = [m["role"] for m in hist]
    assert roles == ["user", "assistant", "assistant", "assistant"]
    contents = [m["content"] for m in hist]
    assert contents == ["hi", "第一轮", "第二轮", "第三轮"]
