"""tool_manager/api.py（FastAPI router）测试：鉴权 / 安全门 / 各端点"""
import json
import pathlib
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from api.errors import AppError
from infra.tool_manager import api as tools_api
from infra.tool_manager import ToolRegistry
from infra.tool_manager.service_registry import (
    get_capability,
    register_capability,
    unregister_capability,
)

AUTH_KEY = "test-secret-key"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    application = FastAPI()

    @application.exception_handler(AppError)
    async def _app_error_handler(request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": exc.code.value, "message": exc.message}},
        )

    application.include_router(tools_api.router)
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(monkeypatch):
    from api import auth as auth_mod
    monkeypatch.setattr(auth_mod.settings, "SIMPLE_API_KEY", AUTH_KEY)
    return AUTH_KEY


@pytest.fixture
def gate(monkeypatch):
    original = get_capability("tool_security_gate")
    fake = MagicMock()
    fake.check = AsyncMock(return_value=(True, "ok"))
    register_capability("tool_security_gate", lambda: fake)
    yield fake
    if original is None:
        unregister_capability("tool_security_gate")
    else:
        register_capability("tool_security_gate", original)


@pytest.fixture
def fake_tm(monkeypatch):
    tm = MagicMock()
    tm.call_tool = AsyncMock(return_value={"success": True, "result": "ok", "error": None})
    tm.call_tool_sync = MagicMock(return_value={"success": True, "result": "ok", "error": None})
    tm.list_available_tools = MagicMock(return_value={"a": {"source": "builtin"}})
    tm.list_by_source = MagicMock(return_value={"builtin": ["a"], "plugin": [], "dynamic": []})
    tm.get_status = MagicMock(return_value={"total_tools": 1, "tool_backend": "mcp"})
    tm.get_tool_events = MagicMock(return_value=[{"tool": "a", "success": True}])
    tm.get_tool_event_stats = MagicMock(return_value={"total": 1, "success": 1, "failed": 0})
    tm.clear_tool_events = MagicMock(return_value=2)
    tm.get_tool_info = MagicMock(return_value={"name": "a", "source": "builtin"})
    monkeypatch.setattr(tools_api, "tool_manager", tm)
    return tm


@pytest.fixture
def isolate_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(ToolRegistry, "_settings_path", lambda: tmp_path / "tool_settings.json")
    ToolRegistry._disabled_loaded = False
    ToolRegistry._disabled_tools = set()
    yield
    ToolRegistry._disabled_loaded = False
    ToolRegistry._disabled_tools = set()


def _register_api_tool(name, func, **kw):
    ToolRegistry.register_tool(name=name, func=func, **kw)
    return name


def _unregister_api_tool(name):
    ToolRegistry.unregister(name)


def _api_tool_func(a, b):
    return a + b


# ── 认证 / 安全门（单元） ───────────────────────────────────────────────────

def test_require_tool_auth_valid_role(auth):
    assert tools_api.require_tool_auth(x_api_key=AUTH_KEY, caller_role="supervisor") == "supervisor"


def test_require_tool_auth_coerces_unknown_role(auth):
    assert tools_api.require_tool_auth(x_api_key=AUTH_KEY, caller_role="hacker") == "expert"


def test_require_tool_auth_missing_key(auth):
    with pytest.raises(HTTPException) as exc:
        tools_api.require_tool_auth(x_api_key=None, caller_role="expert")
    assert exc.value.status_code == 401


def test_require_tool_auth_wrong_key(auth):
    with pytest.raises(HTTPException) as exc:
        tools_api.require_tool_auth(x_api_key="wrong", caller_role="expert")
    assert exc.value.status_code == 401


async def test_security_gate_check_allowed(auth, gate):
    await tools_api._security_gate_check("t", {"a": 1}, "expert")
    gate.check.assert_awaited_once()
    kwargs = gate.check.await_args.kwargs
    assert kwargs["tool_name"] == "t"
    assert kwargs["tool_params"] == {"a": 1}
    assert kwargs["caller_tier"] == "expert"


async def test_security_gate_check_denied(auth, gate):
    gate.check.return_value = (False, "需要审批")
    with pytest.raises(HTTPException) as exc:
        await tools_api._security_gate_check("t", {}, "expert")
    assert exc.value.status_code == 403
    assert "需要审批" in exc.value.detail


async def test_security_gate_check_fail_closed(auth):
    original = get_capability("tool_security_gate")
    unregister_capability("tool_security_gate")
    try:
        with pytest.raises(HTTPException) as exc:
            await tools_api._security_gate_check("t", {}, "expert")
        assert exc.value.status_code == 503
    finally:
        if original is None:
            unregister_capability("tool_security_gate")
        else:
            register_capability("tool_security_gate", original)


# ── 只读端点 ────────────────────────────────────────────────────────────────

def test_list_tools(client, fake_tm):
    r = client.get("/tools/")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["data"]["count"] == 1
    assert data["data"]["by_source"]["builtin"] == ["a"]
    fake_tm.list_available_tools.assert_called_once_with(source=None)


def test_get_tool_status(client, fake_tm):
    r = client.get("/tools/status")
    assert r.status_code == 200
    assert r.json()["data"]["total_tools"] == 1


def test_get_tool_events(client, fake_tm):
    r = client.get("/tools/events", params={"limit": 5, "tool_name": "a", "success": "true", "since": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["count"] == 1
    fake_tm.get_tool_events.assert_called_once_with(limit=5, tool_name="a", success=True, since=100.0)


def test_get_tool_event_stats(client, fake_tm):
    r = client.get("/tools/events/stats")
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 1


# ── 调用端点（含安全门） ─────────────────────────────────────────────────────

def test_call_tool_allowed(auth, gate, fake_tm, client):
    r = client.post("/tools/call", json={"tool_name": "x", "params": {"a": 1}},
                    headers={"X-API-Key": AUTH_KEY})
    assert r.status_code == 200
    assert r.json()["success"] is True
    fake_tm.call_tool.assert_awaited_once_with("x", {"a": 1}, caller_role="expert")
    gate.check.assert_awaited_once()


def test_call_tool_custom_role(auth, gate, fake_tm, client):
    r = client.post("/tools/call", json={"tool_name": "x", "params": {}},
                    headers={"X-API-Key": AUTH_KEY, "caller-role": "commander"})
    assert r.status_code == 200
    fake_tm.call_tool.assert_awaited_once_with("x", {}, caller_role="commander")


def test_call_tool_denied(auth, gate, fake_tm, client):
    gate.check.return_value = (False, "角色无权")
    r = client.post("/tools/call", json={"tool_name": "x", "params": {}},
                    headers={"X-API-Key": AUTH_KEY})
    assert r.status_code == 403
    assert "角色无权" in r.json()["detail"]


def test_call_tool_gate_missing_fail_closed(auth, fake_tm, client):
    original = get_capability("tool_security_gate")
    unregister_capability("tool_security_gate")
    try:
        r = client.post("/tools/call", json={"tool_name": "x", "params": {}},
                        headers={"X-API-Key": AUTH_KEY})
        assert r.status_code == 503
        assert r.json()["detail"] == "安全门未初始化"
    finally:
        if original is None:
            unregister_capability("tool_security_gate")
        else:
            register_capability("tool_security_gate", original)


def test_call_tool_missing_auth(auth, gate, fake_tm, client):
    r = client.post("/tools/call", json={"tool_name": "x", "params": {}})
    assert r.status_code == 401


def test_call_tool_sync(auth, gate, fake_tm, client):
    r = client.post("/tools/call-sync", json={"tool_name": "x", "params": {}},
                    headers={"X-API-Key": AUTH_KEY})
    assert r.status_code == 200
    fake_tm.call_tool_sync.assert_called_once_with("x", {}, caller_role="expert")


def test_call_from_json(auth, gate, fake_tm, client):
    payload = json.dumps({"tool_name": "x", "params": {"a": 1}, "caller_role": "supervisor"})
    r = client.post("/tools/call-json", content=json.dumps(payload),
                    headers={"X-API-Key": AUTH_KEY, "Content-Type": "application/json"})
    assert r.status_code == 200
    fake_tm.call_tool_sync.assert_called_once_with("x", {"a": 1}, caller_role="supervisor")
    kwargs = gate.check.await_args.kwargs
    assert kwargs["caller_tier"] == "supervisor"


def test_call_from_json_uses_arguments_key(auth, gate, fake_tm, client):
    payload = json.dumps({"name": "x", "arguments": {"b": 2}})
    r = client.post("/tools/call-json", content=json.dumps(payload),
                    headers={"X-API-Key": AUTH_KEY, "Content-Type": "application/json"})
    assert r.status_code == 200
    fake_tm.call_tool_sync.assert_called_once_with("x", {"b": 2}, caller_role="expert")


def test_call_from_json_invalid_json(auth, gate, fake_tm, client):
    r = client.post("/tools/call-json", content=json.dumps("{not valid json"),
                    headers={"X-API-Key": AUTH_KEY, "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid JSON format"


def test_call_from_json_non_dict_json(auth, gate, fake_tm, client):
    r = client.post("/tools/call-json", content=json.dumps("[1, 2]"),
                    headers={"X-API-Key": AUTH_KEY, "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid JSON format"


# ── 事件管理 ────────────────────────────────────────────────────────────────

def test_clear_tool_events(auth, fake_tm, client):
    r = client.delete("/tools/events", headers={"X-API-Key": AUTH_KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "已清空 2 条工具调用记录"
    assert body["data"]["cleared"] == 2


# ── 启用 / 源码 / 信息 ──────────────────────────────────────────────────────

def test_set_tool_enabled(isolate_settings, client):
    name = _register_api_tool("api_enable_me", _api_tool_func, source="dynamic")
    try:
        r = client.put(f"/tools/enabled/{name}", json={"enabled": False})
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["data"]["enabled"] is False
        assert not ToolRegistry.is_tool_enabled(name)
    finally:
        _unregister_api_tool(name)


def test_set_tool_enabled_not_found(isolate_settings, client):
    r = client.put("/tools/enabled/ghost_tool", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TOOL_STATE_ERROR"


def test_get_tool_source(client):
    name = _register_api_tool("api_src_tool", _api_tool_func, source="builtin")
    try:
        r = client.get(f"/tools/source/{name}")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert "def _api_tool_func" in body["data"]["source"]
        assert body["data"]["editable"] is False
    finally:
        _unregister_api_tool(name)


def test_get_tool_source_editable(client):
    name = _register_api_tool(
        "api_src_ai_tool", _api_tool_func, source="dynamic", tags=["ai_tool"],
    )
    try:
        r = client.get(f"/tools/source/{name}")
        body = r.json()
        assert body["success"] is True
        assert body["data"]["editable"] is True
    finally:
        _unregister_api_tool(name)


def test_get_tool_source_lambda(client):
    name = _register_api_tool("api_src_lambda", len, source="dynamic")
    try:
        r = client.get(f"/tools/source/{name}")
        body = r.json()
        assert body["success"] is True
        assert "不可用" in body["data"]["source"]
    finally:
        _unregister_api_tool(name)


def test_get_tool_source_not_found(client):
    r = client.get("/tools/source/ghost_tool")
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TOOL_NOT_FOUND"


def test_get_tool_info(client, fake_tm):
    r = client.get("/tools/info/api_tool_x")
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "a"
    fake_tm.get_tool_info.assert_called_once_with("api_tool_x")


def test_get_tool_info_not_found(client, fake_tm):
    fake_tm.get_tool_info.return_value = None
    r = client.get("/tools/info/ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_get_loaded_plugins(client):
    r = client.get("/tools/plugins/loaded")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "plugins" in body["data"]


# ── AI 自创工具 ─────────────────────────────────────────────────────────────

def test_list_ai_tools(client):
    r = client.get("/tools/ai")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "tools" in body["data"]


async def test_list_ai_tools_no_persisted_file(monkeypatch):
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: False)
    out = await tools_api.list_ai_tools()
    assert out["success"] is True


async def test_list_ai_tools_corrupt_persisted_file(monkeypatch):
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: True)
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, encoding="utf-8": "{broken")
    out = await tools_api.list_ai_tools()
    assert out["success"] is True


async def test_list_ai_tools_non_dict_persisted_entry(monkeypatch):
    """持久化记录存在但非 dict（或缺 code）时跳过补充 code，不报错。"""
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: True)
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, encoding="utf-8": '{"my_tool": "not-a-dict"}')
    out = await tools_api.list_ai_tools()
    assert out["success"] is True


def test_create_ai_tool(client, monkeypatch):
    from infra.tool_manager.tools import ai_tools
    monkeypatch.setattr(ai_tools, "create_tool", lambda **kw: "工具 my_tool 创建成功")
    r = client.post("/tools/ai", json={"tool_name": "my_tool", "description": "d", "code": "def f(): pass"})
    assert r.status_code == 200
    assert r.json()["data"]["message"] == "工具 my_tool 创建成功"


def test_create_ai_tool_error(client, monkeypatch):
    from infra.tool_manager.tools import ai_tools
    monkeypatch.setattr(ai_tools, "create_tool", lambda **kw: "❌ 代码验证失败")
    r = client.post("/tools/ai", json={"tool_name": "x", "description": "d", "code": "bad"})
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TOOL_CREATE_ERROR"


def test_edit_ai_tool(client, monkeypatch):
    from infra.tool_manager.tools import ai_tools
    monkeypatch.setattr(ai_tools, "edit_tool", lambda **kw: "更新成功")
    r = client.put("/tools/ai/my_tool", json={"description": "新", "code": "def f(): pass"})
    assert r.json()["data"]["message"] == "更新成功"


def test_edit_ai_tool_error(client, monkeypatch):
    from infra.tool_manager.tools import ai_tools
    monkeypatch.setattr(ai_tools, "edit_tool", lambda **kw: "❌ 失败")
    r = client.put("/tools/ai/my_tool", json={"description": "新", "code": "bad"})
    assert r.json()["error"]["code"] == "TOOL_EDIT_ERROR"


def test_delete_ai_tool(client, monkeypatch):
    from infra.tool_manager.tools import ai_tools
    monkeypatch.setattr(ai_tools, "delete_tool", lambda tool_name=None: "删除成功")
    r = client.delete("/tools/ai/my_tool")
    assert r.json()["data"]["message"] == "删除成功"


def test_delete_ai_tool_error(client, monkeypatch):
    from infra.tool_manager.tools import ai_tools
    monkeypatch.setattr(ai_tools, "delete_tool", lambda tool_name=None: "❌ 不存在")
    r = client.delete("/tools/ai/ghost")
    assert r.json()["error"]["code"] == "TOOL_DELETE_ERROR"


# ── register（未实现） ──────────────────────────────────────────────────────

def test_register_tool_not_implemented(auth, client):
    r = client.post("/tools/register", json={"name": "x", "description": "d"},
                    headers={"X-API-Key": AUTH_KEY})
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "NOT_IMPLEMENTED"
