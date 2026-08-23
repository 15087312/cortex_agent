"""单元测试：api.main — FastAPI 主入口

策略：
- 用 fastapi.testclient.TestClient（非上下文 = 不触发 lifespan，聚焦中间件/端点/异常处理）。
- lifespan（启动/关闭生命周期）用 TestClient 上下文触发，各子系统全部 mock。
- 外部边界全部 mock：ApiLogStore（请求日志落库）、模型工厂、数据库、感知系统、
  memory-libs/personas 等文件写入、config.prompts 加载器。
"""
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_api_log_store(monkeypatch):
    """api_request_log_middleware 落库 → 替换为 mock，避免真实写 data/api_log.db"""
    store = MagicMock()
    monkeypatch.setattr(
        "modules.management.api_log_store.ApiLogStore.get_instance", lambda: store
    )
    return store


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def noop_lifespan():
    """关闭 lifespan，避免测试端点时触发重型初始化"""
    import contextlib
    from api.main import app

    @contextlib.asynccontextmanager
    async def _noop(app):
        yield

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app.router, "lifespan_context", _noop)
        yield


@pytest.fixture
def auth_key(monkeypatch):
    monkeypatch.setattr("api.main._SIMPLE_API_KEY", "test-secret-key")


@pytest.fixture
def no_auth(monkeypatch):
    monkeypatch.setattr("api.main._SIMPLE_API_KEY", "")


@pytest.fixture
def reset_rate_limit():
    import api.main
    api.main.request_counts.clear()
    api.main._request_counter_ref[0] = 0
    yield
    api.main.request_counts.clear()
    api.main._request_counter_ref[0] = 0


@pytest.fixture
def fake_settings(monkeypatch):
    """api.main 使用的 settings → 独立实例（字段可写，文件方法走 mock）"""
    from config.settings import Settings
    s = Settings(_env_file=None)
    monkeypatch.setattr("api.main.settings", s)
    return s


def _mock_method(monkeypatch, cls, name, *args, **kwargs):
    """pydantic 实例禁止 setattr 任意属性 → 在类层级 mock 方法（测试后自动还原）"""
    mock = MagicMock(*args, **kwargs)
    monkeypatch.setattr(cls, name, mock)
    return mock


def _cleanup_route(app, path):
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != path]


def _collect_route_paths(routes) -> list:
    """递归收集路由 path（兼容 fastapi>=0.140 惰性 _IncludedRouter）

    fastapi 0.141+ 的 include_router 不再立即展开 APIRoute，而是封装成
    `fastapi.routing._IncludedRouter`（自身无 .path，实际路由在 original_router.routes 里）。
    递归展开 original_router，两种 fastapi 行为都能拿到真实路径。
    """
    paths = []
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            paths.extend(_collect_route_paths(getattr(inner, "routes", None) or []))
        else:
            p = getattr(r, "path", None)
            if p:
                paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# 辅助函数（_sanitize_body / _truncate_body / _get_client_ip / _cleanup_request_counts）
# ---------------------------------------------------------------------------

def test_sanitize_body():
    from api.main import _sanitize_body
    assert _sanitize_body("") == ""
    # 生产 regex 仅对非引号包裹的值脱敏（query 串 / header 风格）
    assert _sanitize_body('api_key=sk-abcdef1234&name=x') == "api_key=***"
    assert _sanitize_body("authorization: Bearer abcdefghijkl") == "authorization: ***"
    assert _sanitize_body("hello world") == "hello world"


def test_truncate_body():
    from api.main import _truncate_body
    assert _truncate_body(None, 10) == ""
    assert _truncate_body("short", 100) == "short"
    out = _truncate_body("a" * 100, 20)
    assert out == "a" * 20 + "\n...[已截断]"


def test_get_client_ip_trusted_proxy_forwarded():
    from starlette.requests import Request
    from api.main import _get_client_ip
    scope = {
        "type": "http", "method": "GET", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": [(b"x-forwarded-for", b"10.0.0.9, 8.8.8.8")],
        "client": ("127.0.0.1", 1234), "server": ("test", 80),
    }
    assert _get_client_ip(Request(scope)) == "10.0.0.9"


def test_get_client_ip_trusted_proxy_no_forwarded():
    from starlette.requests import Request
    from api.main import _get_client_ip
    scope = {
        "type": "http", "method": "GET", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": [], "client": ("::1", 1234),
        "server": ("test", 80),
    }
    assert _get_client_ip(Request(scope)) == "::1"


def test_get_client_ip_untrusted():
    from starlette.requests import Request
    from api.main import _get_client_ip
    scope = {
        "type": "http", "method": "GET", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": [(b"x-forwarded-for", b"6.6.6.6")],
        "client": ("203.0.113.5", 1234), "server": ("test", 80),
    }
    assert _get_client_ip(Request(scope)) == "203.0.113.5"


def test_get_client_ip_no_client():
    from starlette.requests import Request
    from api.main import _get_client_ip
    scope = {
        "type": "http", "method": "GET", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": [], "client": None, "server": ("test", 80),
    }
    assert _get_client_ip(Request(scope)) == "unknown"


def test_cleanup_request_counts_removes_stale(monkeypatch):
    import api.main
    now = int(time.time() / 60)
    api.main.request_counts = {
        f"ip1|{now - 3}": 1,   # 过期 → 删
        f"ip2|{now + 1}": 2,   # 保留
        "no_pipe_key": 3,      # 非 ip|minute 格式 → 保留
        f"ip3|zzz": 4,         # 分钟非法 → ValueError 跳过
    }
    monkeypatch.setattr(api.main, "_MAX_RATE_LIMIT_KEYS", 100)
    api.main._cleanup_request_counts()
    assert f"ip1|{now - 3}" not in api.main.request_counts
    assert f"ip2|{now + 1}" in api.main.request_counts
    assert "no_pipe_key" in api.main.request_counts
    assert "ip3|zzz" in api.main.request_counts


def test_cleanup_request_counts_trims_when_over_limit(monkeypatch):
    import api.main
    now = int(time.time() / 60)
    api.main.request_counts = {f"ip{i}|{now}": 1 for i in range(20)}
    monkeypatch.setattr(api.main, "_MAX_RATE_LIMIT_KEYS", 10)
    api.main._cleanup_request_counts()
    assert len(api.main.request_counts) == 10


# ---------------------------------------------------------------------------
# 根 / 健康检查
# ---------------------------------------------------------------------------

def test_root_returns_app_info(client, reset_rate_limit):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["name"] == "Humanoid AGI"
    assert data["data"]["status"] == "running"
    assert "X-Request-ID" in resp.headers
    assert float(resp.headers["X-Process-Time"]) >= 0


def test_health_ok(client, reset_rate_limit, monkeypatch):
    mf = MagicMock()
    mf.is_ready = True
    db = MagicMock()
    db.get_session.return_value = MagicMock()
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: mf)
    monkeypatch.setattr("modules.database.connection.db_manager", db)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "healthy"
    assert body["data"]["checks"]["model_manager"] == "ok"
    assert body["data"]["checks"]["database"] == "ok"


def test_health_degraded(client, reset_rate_limit, monkeypatch):
    mf = MagicMock()
    mf.is_ready = False
    db = MagicMock()
    db.get_session.side_effect = RuntimeError("db down")
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: mf)
    monkeypatch.setattr("modules.database.connection.db_manager", db)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"]["model_manager"] == "not_initialized"
    assert body["data"]["checks"]["database"] == "unavailable"


def test_health_factory_exception(client, reset_rate_limit, monkeypatch):
    monkeypatch.setattr(
        "modules.thinking.model_factory.get_model_factory", MagicMock(side_effect=RuntimeError("no factory"))
    )
    db = MagicMock()
    monkeypatch.setattr("modules.database.connection.db_manager", db)
    resp = client.get("/health")
    assert resp.json()["data"]["checks"]["model_manager"] == "unavailable"
    assert resp.json()["data"]["status"] == "degraded"


# ---------------------------------------------------------------------------
# 认证中间件
# ---------------------------------------------------------------------------

def test_auth_missing_key_401(client, auth_key, reset_rate_limit):
    resp = client.get("/tools/call")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_auth_wrong_key_401(client, auth_key, reset_rate_limit):
    resp = client.get("/tools/call", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_auth_correct_key_passes(client, auth_key, reset_rate_limit):
    resp = client.put(
        "/config/DEBUG", json={"value": True}, headers={"X-API-Key": "test-secret-key"}
    )
    assert resp.status_code == 200


def test_auth_disabled_when_no_key(client, no_auth, reset_rate_limit):
    resp = client.put("/config/DEBUG", json={"value": True})
    assert resp.status_code == 200


def test_auth_sensitive_config_write_requires_key(client, auth_key, reset_rate_limit):
    """PUT /config/<模型配置> 不豁免白名单，无 key → 401"""
    resp = client.put("/config/LARGE_MODEL_API_KEY", json={"value": "sk-xxx"})
    assert resp.status_code == 401


def test_auth_whitelisted_paths_bypass(client, auth_key, reset_rate_limit):
    for path in ("/health", "/dashboard", "/config"):
        resp = client.get(path)
        assert resp.status_code in (200, 404), path


def test_auth_pass_through_non_whitelisted_with_key(client, auth_key, reset_rate_limit):
    from api.main import app
    _add_temp_route(app, "GET", "/__authed__", {"ok": 1})
    try:
        resp = client.get("/__authed__", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200
        assert resp.json()["ok"] == 1
    finally:
        _cleanup_route(app, "/__authed__")


# ---------------------------------------------------------------------------
# 限流中间件
# ---------------------------------------------------------------------------

def test_rate_limit_exceeded_429(client, no_auth, reset_rate_limit):
    import api.main
    minute = int(time.time() / 60)
    api.main.request_counts[f"testclient|{minute}"] = 100  # 非回环阈值 100
    resp = client.get("/")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


def test_rate_limit_cleanup_triggered(client, no_auth, reset_rate_limit, monkeypatch):
    import api.main
    minute = int(time.time() / 60)
    api.main.request_counts[f"stale_ip|{minute - 5}"] = 3
    api.main._request_counter_ref[0] = 499  # 下一次请求触发清理
    resp = client.get("/")
    assert resp.status_code == 200
    assert f"stale_ip|{minute - 5}" not in api.main.request_counts


# ---------------------------------------------------------------------------
# 全局异常处理器
# ---------------------------------------------------------------------------

def test_app_error_handler(client, no_auth, reset_rate_limit):
    from api.main import app
    from api.errors import AppError, ErrorCode

    @app.get("/__apperr__")
    async def _boom():
        raise AppError(ErrorCode.FORBIDDEN, "no permission")

    try:
        resp = client.get("/__apperr__")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"
        assert resp.json()["error"]["message"] == "no permission"
    finally:
        _cleanup_route(app, "/__apperr__")


@pytest.mark.parametrize("status,code", [
    (400, "BAD_REQUEST"), (401, "UNAUTHORIZED"), (403, "FORBIDDEN"),
    (404, "NOT_FOUND"), (413, "PAYLOAD_TOO_LARGE"), (415, "UNSUPPORTED_MEDIA_TYPE"),
    (429, "RATE_LIMITED"), (500, "INTERNAL_ERROR"),
])
def test_http_exception_mapping(client, no_auth, reset_rate_limit, status, code):
    from api.main import app

    @app.get(f"/__httpexc_{status}__")
    async def _boom():
        raise HTTPException(status_code=status, detail="boom")

    try:
        resp = client.get(f"/__httpexc_{status}__")
        assert resp.status_code == status
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == code
        assert body["error"]["message"] == "boom"
    finally:
        _cleanup_route(app, f"/__httpexc_{status}__")


def test_http_exception_unknown_status(client, no_auth, reset_rate_limit):
    from api.main import app

    @app.get("/__httpexc_unknown__")
    async def _boom():
        raise HTTPException(status_code=418, detail="teapot")

    try:
        resp = client.get("/__httpexc_unknown__")
        assert resp.status_code == 418
        assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    finally:
        _cleanup_route(app, "/__httpexc_unknown__")


def test_http_exception_non_str_detail(client, no_auth, reset_rate_limit):
    from api.main import app

    @app.get("/__httpexc_listdetail__")
    async def _boom():
        raise HTTPException(status_code=422, detail=["a", "b"])

    try:
        resp = client.get("/__httpexc_listdetail__")
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert resp.json()["error"]["message"] == "请求错误"
    finally:
        _cleanup_route(app, "/__httpexc_listdetail__")


def test_validation_error_handler(client, reset_rate_limit):
    resp = client.put("/config/DEBUG", content="", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert resp.json()["error"]["message"]


def test_general_exception_handler(client, no_auth, reset_rate_limit, monkeypatch):
    from api.main import app
    monkeypatch.setattr("modules.management.core.error_bus.error_bus", MagicMock())

    @app.get("/__generr__")
    async def _boom():
        raise ValueError("internal boom")

    try:
        resp = client.get("/__generr__")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert body["error"]["message"] == "服务内部错误"
    finally:
        _cleanup_route(app, "/__generr__")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def test_dashboard_served(client, reset_rate_limit, monkeypatch, tmp_path):
    from api.main import app
    monkeypatch.setattr("api.main._DASHBOARD_DIR", str(tmp_path))
    (tmp_path / "causal_graph.html").write_text("<h1>hello dash</h1>", encoding="utf-8")
    for path in ("/dashboard", "/dashboard/"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "<h1>hello dash</h1>" in resp.text


def test_dashboard_not_found(client, reset_rate_limit, monkeypatch, tmp_path):
    monkeypatch.setattr("api.main._DASHBOARD_DIR", str(tmp_path))
    resp = client.get("/dashboard")
    assert resp.status_code == 404
    assert "Dashboard not found" in resp.text


# ---------------------------------------------------------------------------
# register_module_routers
# ---------------------------------------------------------------------------

def test_register_module_routers_includes_all():
    from api.main import register_module_routers
    from infra.data_process.api import router as dp_router
    from infra.tool_manager.api import router as tool_router
    # 诊断守卫：若 tool_router 被并发测试/重导入清空，先给出明确原因，
    # 而非笼统的 "/tools 缺失"
    assert tool_router.routes, (
        "tool_router 无路由（infra.tool_manager.api 被重导入或全局状态污染）"
    )
    app = FastAPI()
    register_module_routers(app)
    paths = set(_collect_route_paths(app.routes))
    # 核心路由必须注册（fastapi>=0.140 惰性 _IncludedRouter 由 _collect_route_paths 展开）
    for prefix in ("/tools", "/stream", "/management", "/output", "/security", "/differences"):
        assert any(p.startswith(prefix) for p in paths), (
            f"缺失路由 {prefix} | tool_router routes={len(tool_router.routes)}"
            f" | app.routes[:10]={sorted(paths)[:10]}"
        )
    # data-process：router 自身有路由且被 include（失败时输出诊断，便于 CI 定位）
    assert dp_router.routes, "data_process router 无路由（模块注册被跳过）"
    assert any(p.startswith("/data-process") for p in paths), (
        f"data-process 未注册: router.routes={len(dp_router.routes)}; "
        f"app.routes 前 20 条={sorted(paths)[:20]}"
    )


def test_register_module_routers_skips_difference_when_disabled(monkeypatch):
    from api.main import register_module_routers
    monkeypatch.setattr("api.main.settings.DIFFERENCE_DETECTOR_ENABLED", False)
    app = FastAPI()
    register_module_routers(app)
    paths = set(_collect_route_paths(app.routes))
    assert not any(p.startswith("/differences") for p in paths)


# ---------------------------------------------------------------------------
# Config API
# ---------------------------------------------------------------------------

def test_get_config(client, reset_rate_limit, fake_settings):
    resp = client.get("/config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "DEBUG" in data or "launch_at_startup" in data
    assert isinstance(data, dict)


def test_update_config_forbidden_key(client, reset_rate_limit, fake_settings):
    resp = client.put("/config/SUPER_SECRET_SETTING", json={"value": "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_update_config_field_not_found(client, reset_rate_limit, fake_settings):
    """PREVENT_SLEEP 在允许列表但非 Settings 字段 → 404"""
    resp = client.put("/config/PREVENT_SLEEP", json={"value": True})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_update_config_bad_value_type(client, reset_rate_limit, fake_settings):
    resp = client.put("/config/DEBUG", json={"value": "notabool"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_update_config_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "save_user_config", return_value=True)
    resp = client.put("/config/DEBUG", json={"value": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["key"] == "DEBUG"
    assert body["data"]["new_value"] is True


def test_update_config_persist_failure_warns(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "save_user_config", return_value=False)
    resp = client.put("/config/DEBUG", json={"value": True})
    assert resp.status_code == 200  # 持久化失败仅告警，不阻断


def test_update_config_cortex_mode_sets_env(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "save_user_config", return_value=True)
    monkeypatch.setenv("CORTEX_MODE", "sentinel")
    resp = client.put("/config/CORTEX_MODE", json={"value": "chatonly"})
    assert resp.status_code == 200
    assert os_environ_value("CORTEX_MODE") == "chatonly"


def os_environ_value(key):
    import os
    return os.environ.get(key)


def test_update_config_model_key_reloads(client, no_auth, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "save_user_config", return_value=True)
    mf = MagicMock()
    mf.reload_from_config = AsyncMock()
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: mf)
    resp = client.put("/config/LARGE_MODEL_API_KEY", json={"value": "sk-new"})
    assert resp.status_code == 200
    mf.reload_from_config.assert_awaited_once()


def test_update_config_model_reload_failure_warns(client, no_auth, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "save_user_config", return_value=True)
    mf = MagicMock()
    mf.reload_from_config = AsyncMock(side_effect=RuntimeError("reload failed"))
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: mf)
    resp = client.put("/config/LARGE_MODEL_API_KEY", json={"value": "sk-new"})
    assert resp.status_code == 200


def test_update_config_internal_error_500(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "save_user_config", side_effect=RuntimeError("disk broken"))
    resp = client.put("/config/DEBUG", json={"value": True})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# 人设 / 记忆库 / 角色工具 / 模型参数
# ---------------------------------------------------------------------------

def _mock_loader(monkeypatch, roles=None):
    loader = MagicMock()
    loader.load.return_value = {"roles": roles or {}}
    monkeypatch.setattr("config.prompts.loader.get_loader", lambda: loader)
    return loader


def test_get_personas(client, reset_rate_limit, fake_settings, monkeypatch):
    _mock_loader(monkeypatch, roles={
        "assistant": {"name": "助手", "tier": "medium", "personality": "default"},
    })
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "get_persona", return_value="自定义人设")
    _mock_method(monkeypatch, Settings, "get_system_override", return_value="系统覆盖")
    resp = client.get("/config/personas")
    assert resp.status_code == 200
    personas = resp.json()["data"]["personas"]
    assert personas[0]["role"] == "assistant"
    assert personas[0]["custom"] == "自定义人设"
    assert personas[0]["system_override"] == "系统覆盖"


def test_get_memory_libs(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "get_memory_libs", return_value={
        "current": "默认",
        "libs": {"默认": {"db": "a", "faiss": "b", "id_map": "c"}, "工作": {"db": "d", "faiss": "e", "id_map": "f"}},
    })
    _mock_method(monkeypatch, Settings, "memory_lib_event_count", return_value=42)
    resp = client.get("/config/memory-libs")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["current"] == "默认"
    assert {l["name"] for l in data["libs"]} == {"默认", "工作"}


def test_create_memory_lib_empty_name_422(client, reset_rate_limit, fake_settings):
    resp = client.post("/config/memory-libs", json={"name": "  "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_memory_lib_conflict_409(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "create_memory_lib", return_value=None)
    resp = client.post("/config/memory-libs", json={"name": "已存在"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_create_memory_lib_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "create_memory_lib", return_value={"db": "x"})
    resp = client.post("/config/memory-libs", json={"name": "新库"})
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "新库"


def test_switch_memory_lib_404(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "switch_memory_lib", return_value=False)
    resp = client.put("/config/memory-libs/current", json={"name": "ghost"})
    assert resp.status_code == 404


def test_switch_memory_lib_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "switch_memory_lib", return_value=True)
    resp = client.put("/config/memory-libs/current", json={"name": "工作"})
    assert resp.status_code == 200
    assert resp.json()["data"]["current"] == "工作"


def test_rename_memory_lib_missing_fields_422(client, reset_rate_limit, fake_settings):
    resp = client.put("/config/memory-libs/rename", json={"old_name": "a"})
    assert resp.status_code == 422


def test_rename_memory_lib_fail_409(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "rename_memory_lib", return_value=False)
    resp = client.put("/config/memory-libs/rename", json={"old_name": "a", "new_name": "b"})
    assert resp.status_code == 409


def test_rename_memory_lib_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "rename_memory_lib", return_value=True)
    resp = client.put("/config/memory-libs/rename", json={"old_name": "a", "new_name": "b"})
    assert resp.status_code == 200


def test_delete_memory_lib_404(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "delete_memory_lib", return_value=False)
    resp = client.delete("/config/memory-libs/默认")
    assert resp.status_code == 404


def test_delete_memory_lib_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "delete_memory_lib", return_value=True)
    resp = client.delete("/config/memory-libs/旧库")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] == "旧库"


def test_update_persona_with_and_without_override(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    # 原子写入：persona + system_override 合并为单次 set_persona_and_override
    _mock_method(monkeypatch, Settings, "set_persona_and_override",
                 side_effect=lambda role, value, so=None: {
                     "persona": str(value or ""),
                     "system_override": (so or "").strip(),
                 })
    resp = client.put("/config/persona/assistant", json={"value": "新人格"})
    assert resp.status_code == 200
    assert resp.json()["data"]["custom"] == "新人格"
    assert resp.json()["data"]["system_override"] == ""
    resp2 = client.put("/config/persona/assistant", json={"value": "新人格", "system_override": "全量覆盖"})
    assert resp2.status_code == 200
    assert resp2.json()["data"]["system_override"] == "全量覆盖"


def test_get_role_tools(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "get_role_tools", return_value={"whitelist": [], "blacklist": []})
    resp = client.get("/config/tools/assistant")
    assert resp.status_code == 200
    assert resp.json()["data"]["tools"] == {"whitelist": [], "blacklist": []}


def test_update_role_tools_valid(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "set_role_tools", return_value={})
    _mock_method(monkeypatch, Settings, "get_role_tools", return_value={"whitelist": ["git"]})
    resp = client.put("/config/tools/assistant", json={"tools": {"whitelist": ["git"]}})
    assert resp.status_code == 200
    assert resp.json()["data"]["tools"]["whitelist"] == ["git"]


def test_update_role_tools_invalid_422(client, reset_rate_limit, fake_settings):
    resp = client.put("/config/tools/assistant", json={"tools": "not-a-dict"})
    assert resp.status_code == 422


def test_get_model_params(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "get_model_params", return_value={"temperature": 0.7})
    resp = client.get("/config/model-params/assistant")
    assert resp.status_code == 200
    assert resp.json()["data"]["params"] == {"temperature": 0.7}


def test_update_model_params_valid(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "set_model_params", return_value={})
    _mock_method(monkeypatch, Settings, "get_model_params", return_value={"max_tokens": 100})
    resp = client.put("/config/model-params/assistant", json={"params": {"max_tokens": 100}})
    assert resp.status_code == 200
    assert resp.json()["data"]["params"]["max_tokens"] == 100


def test_update_model_params_invalid_422(client, reset_rate_limit, fake_settings):
    resp = client.put("/config/model-params/assistant", json={"params": [1, 2]})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 自定义 Agent
# ---------------------------------------------------------------------------

def test_create_agent_missing_fields_422(client, reset_rate_limit, fake_settings):
    resp = client.post("/management/orchestration/agents", json={"role": "r"})
    assert resp.status_code == 422


def test_create_agent_invalid_tier_422(client, reset_rate_limit, fake_settings):
    resp = client.post("/management/orchestration/agents", json={"role": "r", "name": "n", "tier": "bogus"})
    assert resp.status_code == 422


def test_create_agent_builtin_conflict_409(client, reset_rate_limit, fake_settings, monkeypatch):
    _mock_loader(monkeypatch, roles={"assistant": {"name": "助手"}})
    resp = client.post("/management/orchestration/agents", json={"role": "assistant", "name": "n", "tier": "expert"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


def test_create_agent_already_exists_409(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_loader(monkeypatch, roles={})
    _mock_method(monkeypatch, Settings, "get_custom_agent", return_value={"role": "custom1"})
    resp = client.post("/management/orchestration/agents", json={"role": "custom1", "name": "n", "tier": "expert"})
    assert resp.status_code == 409


def test_create_agent_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_loader(monkeypatch, roles={})
    _mock_method(monkeypatch, Settings, "get_custom_agent", return_value=None)
    _mock_method(monkeypatch, Settings, "set_custom_agent", return_value={})
    resp = client.post("/management/orchestration/agents", json={"role": "new1", "name": "n", "tier": "expert", "personality": "x"})
    assert resp.status_code == 200
    assert resp.json()["data"]["agent"]["role"] == "new1"


def test_delete_agent_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "delete_custom_agent", return_value=True)
    for name in ("set_persona", "set_system_override", "set_role_tools", "set_model_params"):
        _mock_method(monkeypatch, Settings, name, return_value={})
    resp = client.delete("/management/orchestration/agents/custom1")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_delete_agent_not_found(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "delete_custom_agent", return_value=False)
    resp = client.delete("/management/orchestration/agents/ghost")
    assert resp.status_code == 404


def test_toggle_agent_not_found(client, reset_rate_limit, fake_settings, monkeypatch):
    _mock_loader(monkeypatch, roles={})
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "get_custom_agent", return_value=None)
    resp = client.put("/management/orchestration/agents/ghost/active", json={"active": True})
    assert resp.status_code == 404


def test_toggle_agent_builtin_large_active(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_loader(monkeypatch, roles={"commander": {"tier": "large"}})
    _mock_method(monkeypatch, Settings, "get_custom_agent", return_value=None)
    # 原子操作：停用同层 + 设置启用合并为 deactivate_and_set_active
    _mock_method(monkeypatch, Settings, "deactivate_and_set_active", return_value=None)
    _mock_method(monkeypatch, Settings, "set_agent_active", return_value=None)
    resp = client.put("/management/orchestration/agents/commander/active", json={"active": True})
    assert resp.status_code == 200
    from config.settings import Settings as _S
    _S.deactivate_and_set_active.assert_called_once()
    _S.set_agent_active.assert_not_called()


def test_toggle_agent_custom_active(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_loader(monkeypatch, roles={})
    _mock_method(monkeypatch, Settings, "get_custom_agent", return_value={"role": "custom1", "tier": "expert"})
    _mock_method(monkeypatch, Settings, "set_agent_active", return_value=None)
    resp = client.put("/management/orchestration/agents/custom1/active", json={"active": False})
    assert resp.status_code == 200
    assert resp.json()["data"]["active"] is False


# ---------------------------------------------------------------------------
# 人设预设
# ---------------------------------------------------------------------------

def test_list_persona_presets(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "get_persona_presets", return_value=[{"id": "p1", "name": "预设1"}])
    resp = client.get("/management/persona-presets")
    assert resp.status_code == 200
    assert resp.json()["data"]["presets"][0]["name"] == "预设1"


def test_save_persona_preset_empty_name_422(client, reset_rate_limit, fake_settings):
    resp = client.post("/management/persona-presets", json={"name": ""})
    assert resp.status_code == 422


def test_save_persona_preset_with_personas(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "save_persona_preset", return_value={"id": "p9"})
    resp = client.post("/management/persona-presets", json={"name": "预设", "personas": {"a": "p"}})
    assert resp.status_code == 200
    assert resp.json()["data"]["preset"] == {"id": "p9"}


def test_save_persona_preset_reads_current(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_loader(monkeypatch, roles={"assistant": {"name": "助手"}})
    _mock_method(monkeypatch, Settings, "get_custom_agents", return_value=[{"role": "custom1"}])
    get_persona = MagicMock(side_effect=lambda role: "p" if role == "assistant" else "")
    monkeypatch.setattr(Settings, "get_persona", get_persona)
    save = _mock_method(monkeypatch, Settings, "save_persona_preset", return_value={"id": "p10"})
    resp = client.post("/management/persona-presets", json={"name": "汇总"})
    assert resp.status_code == 200
    assert save.call_args[0][2] == {"assistant": "p"}


def test_delete_persona_preset_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "delete_persona_preset", return_value=True)
    resp = client.delete("/management/persona-presets/p1")
    assert resp.status_code == 200


def test_delete_persona_preset_not_found(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "delete_persona_preset", return_value=False)
    resp = client.delete("/management/persona-presets/p1")
    assert resp.status_code == 404


def test_apply_persona_preset_success(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "apply_persona_preset", return_value=True)
    resp = client.put("/management/persona-presets/p1/apply")
    assert resp.status_code == 200


def test_apply_persona_preset_not_found(client, reset_rate_limit, fake_settings, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "apply_persona_preset", return_value=False)
    resp = client.put("/management/persona-presets/p1/apply")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /config/api-key
# ---------------------------------------------------------------------------

def test_get_api_key_dev_returns_plaintext(client, reset_rate_limit, auth_key, fake_settings):
    resp = client.get("/config/api-key")
    assert resp.status_code == 200
    assert resp.json()["data"]["configured"] is True
    assert resp.json()["data"]["api_key"] == "test-secret-key"


def test_get_api_key_prod_loopback_returns_key(client, reset_rate_limit, auth_key, fake_settings, monkeypatch):
    fake_settings.APP_ENV = "production"
    monkeypatch.setattr("api.main._get_client_ip", lambda req: "127.0.0.1")
    resp = client.get("/config/api-key")
    assert resp.json()["data"]["api_key"] == "test-secret-key"


def test_get_api_key_prod_remote_hidden(client, reset_rate_limit, auth_key, fake_settings, monkeypatch):
    fake_settings.APP_ENV = "production"
    monkeypatch.setattr("api.main._get_client_ip", lambda req: "192.168.1.5")
    resp = client.get("/config/api-key")
    data = resp.json()["data"]
    assert data["configured"] is True
    assert data["api_key"] == ""


# ---------------------------------------------------------------------------
# api_request_log_middleware
# ---------------------------------------------------------------------------

def _add_temp_route(app, method, path, response):
    async def _handler():
        return response
    app.add_api_route(path, _handler, methods=[method], include_in_schema=False)


def test_api_log_records_post_body(client, reset_rate_limit, no_auth, fake_settings, _mock_api_log_store, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "create_memory_lib", return_value={"db": "x"})
    resp = client.post("/config/memory-libs", json={"name": "新库", "api_key": "sk-super-secret-1234"})
    assert resp.status_code == 200
    _mock_api_log_store.add.assert_called()
    req_body = _mock_api_log_store.add.call_args.kwargs["request_body"]
    assert "新库" in req_body


def test_api_log_skips_ignored_get(client, reset_rate_limit, no_auth, _mock_api_log_store):
    client.get("/health")
    assert not _mock_api_log_store.add.called


def test_api_log_records_query_for_get(client, reset_rate_limit, no_auth, _mock_api_log_store, monkeypatch):
    from api.main import app
    _add_temp_route(app, "GET", "/__logquery__", {"ok": 1})
    try:
        resp = client.get("/__logquery__?foo=bar")
        assert resp.status_code == 200
        _mock_api_log_store.add.assert_called()
        req_body = _mock_api_log_store.add.call_args.kwargs["request_body"]
        assert req_body == "?foo=bar"
    finally:
        _cleanup_route(app, "/__logquery__")


def test_api_log_passes_through_stream_response(client, reset_rate_limit, no_auth, _mock_api_log_store, monkeypatch):
    from fastapi.responses import Response
    from api.main import app
    _add_temp_route(app, "GET", "/__logstream__", Response(content="data: x", media_type="text/event-stream"))
    try:
        resp = client.get("/__logstream__")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        _mock_api_log_store.add.assert_called()
        assert _mock_api_log_store.add.call_args.kwargs["response_body"] == ""
    finally:
        _cleanup_route(app, "/__logstream__")


def test_api_log_store_failure_is_silent(client, reset_rate_limit, no_auth, _mock_api_log_store):
    _mock_api_log_store.add.side_effect = RuntimeError("store down")
    resp = client.get("/")
    assert resp.status_code == 200


def test_api_log_truncates_large_body(client, reset_rate_limit, no_auth, fake_settings, _mock_api_log_store, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "create_memory_lib", return_value={"db": "x"})
    big = {"name": "x" * 5000}
    resp = client.post("/config/memory-libs", json=big)
    assert resp.status_code == 200
    req_body = _mock_api_log_store.add.call_args.kwargs["request_body"]
    assert len(req_body) <= 4000 + len("\n...[已截断]")
    assert "已截断" in req_body


def test_api_log_sanitize_failure_swallowed(client, reset_rate_limit, no_auth, fake_settings, _mock_api_log_store, monkeypatch):
    from config.settings import Settings
    _mock_method(monkeypatch, Settings, "create_memory_lib", return_value={"db": "x"})
    state = {"n": 0}

    def _flaky_sanitize(text):
        state["n"] += 1
        if state["n"] == 1:  # 仅请求体脱敏抛错；响应体路径照常
            raise RuntimeError("boom")
        return text

    monkeypatch.setattr("api.main._sanitize_body", _flaky_sanitize)
    resp = client.post("/config/memory-libs", json={"name": "x"})
    assert resp.status_code == 200
    _mock_api_log_store.add.assert_called()
    assert _mock_api_log_store.add.call_args.kwargs["request_body"] == ""


def test_api_log_empty_response_body(client, reset_rate_limit, no_auth, _mock_api_log_store, monkeypatch):
    from fastapi.responses import Response
    from api.main import app
    _add_temp_route(app, "GET", "/__emptyresp__", Response(content=b"", media_type="application/json"))
    try:
        resp = client.get("/__emptyresp__")
        assert resp.status_code == 200
        _mock_api_log_store.add.assert_called()
        assert _mock_api_log_store.add.call_args.kwargs["response_body"] == ""
    finally:
        _cleanup_route(app, "/__emptyresp__")


# ---------------------------------------------------------------------------
# lifespan（生命周期）
# ---------------------------------------------------------------------------

def _patch_lifespan(monkeypatch, tmp_path):
    import pathlib
    from config.settings import Settings
    import api.main as m

    s = Settings(_env_file=None)
    s.VISION_BACKEND = "auto"
    s.launch_at_startup = True
    monkeypatch.setattr(m, "settings", s)
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(s, "_apply_current_memory_lib", MagicMock())
    mocks = {"settings": s}

    mocks["screen_permission"] = MagicMock()
    monkeypatch.setattr("utils.screen_capture.init_screen_permission", mocks["screen_permission"])

    eng = MagicMock()
    eng._load_model.return_value = True
    eng.dim = 128
    monkeypatch.setattr("modules.memory.embedding.EmbeddingEngine.get_instance", staticmethod(lambda: eng))
    mocks["eng"] = eng

    analyzer = AsyncMock()
    analyzer.model_type = "auto"
    analyzer.initialize = AsyncMock()
    monkeypatch.setattr("infra.data_process.core.image_analyzer.ImageAnalyzer", lambda model_type=None: analyzer)
    mocks["analyzer"] = analyzer

    mf = MagicMock()
    monkeypatch.setattr("modules.thinking.model_factory.get_model_factory", lambda: mf)
    mocks["mf"] = mf

    mocks["init_sys"] = AsyncMock()
    monkeypatch.setattr("modules.thinking.api_stream.initialize_system", mocks["init_sys"])

    cm = MagicMock()
    cm.active_connections = {}
    monkeypatch.setattr("modules.thinking.api_stream.connection_manager", cm)
    monkeypatch.setattr("modules.thinking.api_stream._build_event", lambda **kw: {"evt": 1})
    mocks["cm"] = cm

    bus = MagicMock()
    monkeypatch.setattr("modules.management.core.error_bus.error_bus", bus)
    mocks["bus"] = bus

    ps = MagicMock()
    monkeypatch.setattr("modules.perception.setup.get_perception_system", lambda: ps)
    mocks["ps"] = ps
    pi = MagicMock()
    monkeypatch.setattr("modules.perception.integration.get_perception_integrator", lambda: pi)
    mocks["pi"] = pi

    src = MagicMock()
    monkeypatch.setattr("modules.perception.difference.sources.mcp_screen_source.get_screen_diff_source", lambda: src)
    mocks["src"] = src
    sms = MagicMock()
    monkeypatch.setattr("modules.perception.difference.sources.screen_monitor_source.get_screen_monitor_source", lambda: sms)
    mocks["sms"] = sms

    hb = MagicMock()
    monkeypatch.setattr("modules.perception.difference.get_heartbeat", lambda: hb)
    mocks["hb"] = hb

    mocks["tt"] = MagicMock()
    monkeypatch.setattr("modules.perception.trigger_think.register", mocks["tt"])

    mocks["pwr"] = MagicMock()
    monkeypatch.setattr("utils.power.apply", mocks["pwr"])
    mocks["autostart"] = MagicMock()
    monkeypatch.setattr("utils.autostart.apply", mocks["autostart"])

    db = MagicMock()
    monkeypatch.setattr("modules.database.connection.db_manager", db)
    mocks["db"] = db
    return mocks


@pytest.fixture
def life(monkeypatch, tmp_path):
    return _patch_lifespan(monkeypatch, tmp_path)


def test_lifespan_startup_success(life):
    from api.main import app
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
    assert life["eng"]._load_model.called
    assert life["analyzer"].initialize.await_count == 1
    assert life["mf"].ensure_ready.called
    assert life["init_sys"].await_count == 1
    assert life["ps"].setup.called and life["ps"].start.called and life["ps"].stop.called
    assert life["pi"].start.called
    assert life["src"].start.called and life["src"].stop.called
    assert life["sms"].start.called
    assert life["hb"].start.called and life["hb"].stop.called
    assert life["tt"].called
    assert life["pwr"].called
    assert life["autostart"].called
    assert life["db"].close.called
    assert life["mf"].shutdown.called


def test_lifespan_ws_error_callback(life):
    from api.main import app
    with TestClient(app):
        pass
    cb = life["bus"].set_ws_callback.call_args.args[0]
    life["cm"].active_connections = {"sess1": MagicMock()}
    cb("ERROR", "boom", {"module": "m", "function": "f", "extra": 1})
    life["cm"].send_json_from_thread.assert_called_once()


def test_lifespan_production_requires_api_key(life, monkeypatch):
    from api.main import app
    life["settings"].APP_ENV = "production"
    monkeypatch.setattr("api.main._SIMPLE_API_KEY", "")
    with pytest.raises(RuntimeError, match="API Key"):
        with TestClient(app):
            pass


def test_lifespan_cordex_init_failure(life, monkeypatch):
    import pathlib
    from api.main import app
    monkeypatch.setattr(pathlib.Path, "mkdir", MagicMock(side_effect=OSError("no space")))
    with TestClient(app):
        pass


def test_lifespan_screen_permission_failure(life):
    from api.main import app
    life["screen_permission"].side_effect = RuntimeError("perm fail")
    with TestClient(app):
        pass


def test_lifespan_memory_lib_apply_failure(life):
    from api.main import app
    life["settings"]._apply_current_memory_lib = MagicMock(side_effect=RuntimeError("mem fail"))
    with TestClient(app):
        pass


def test_lifespan_embedding_load_failure(life):
    from api.main import app
    life["eng"]._load_model.return_value = False
    with pytest.raises(RuntimeError, match="Embedding 模型加载失败"):
        with TestClient(app):
            pass


def test_lifespan_vision_timeout(life):
    from api.main import app
    life["analyzer"].initialize = AsyncMock(side_effect=asyncio.TimeoutError())
    with TestClient(app) as c:
        c.get("/")
    assert life["mf"].ensure_ready.called  # 超时后仍继续后续初始化


def test_lifespan_vision_generic_failure(life):
    from api.main import app
    life["analyzer"].initialize = AsyncMock(side_effect=ValueError("no vision"))
    with TestClient(app):
        pass


def test_lifespan_vision_mock_skips_preload(life):
    from api.main import app
    life["settings"].VISION_BACKEND = "mock"
    with TestClient(app) as c:
        c.get("/")
    assert not life["analyzer"].initialize.await_count


def test_lifespan_model_factory_failure(life):
    from api.main import app
    life["mf"].ensure_ready.side_effect = RuntimeError("factory down")
    with TestClient(app):
        pass


def test_lifespan_stream_init_failure(life):
    from api.main import app
    life["init_sys"].side_effect = RuntimeError("stream down")
    with TestClient(app):
        pass


def test_lifespan_error_bus_setup_failure(life):
    from api.main import app
    life["bus"].setup_asyncio_handler.side_effect = RuntimeError("bus down")
    with TestClient(app):
        pass


def test_lifespan_ws_callback_register_failure(life):
    from api.main import app
    life["bus"].set_ws_callback.side_effect = RuntimeError("cb fail")
    with TestClient(app):
        pass


def test_lifespan_perception_failure(life):
    from api.main import app
    life["ps"].setup.side_effect = RuntimeError("perception down")
    with TestClient(app):
        pass


def test_lifespan_perception_integrator_failure(life):
    from api.main import app
    life["pi"].start.side_effect = RuntimeError("integrator down")
    with TestClient(app):
        pass


def test_lifespan_mcp_failure(life):
    from api.main import app
    life["src"].start.side_effect = RuntimeError("mcp down")
    with TestClient(app):
        pass
    assert life["sms"].start.called  # 屏幕内容分析仍启动


def test_lifespan_screen_monitor_failure(life):
    from api.main import app
    life["sms"].start.side_effect = RuntimeError("monitor down")
    with TestClient(app):
        pass


def test_lifespan_heartbeat_failure(life):
    from api.main import app
    life["hb"].start.side_effect = RuntimeError("hb down")
    with TestClient(app):
        pass


def test_lifespan_trigger_think_failure(life):
    from api.main import app
    life["tt"].side_effect = RuntimeError("trigger fail")
    with TestClient(app):
        pass


def test_lifespan_power_failure(life):
    from api.main import app
    life["pwr"].side_effect = RuntimeError("power fail")
    with TestClient(app):
        pass


def test_lifespan_autostart_failure(life):
    from api.main import app
    life["autostart"].side_effect = RuntimeError("autostart fail")
    with TestClient(app):
        pass


def test_lifespan_shutdown_failures_swallowed(life):
    from api.main import app
    life["ps"].stop.side_effect = RuntimeError("stop fail")
    life["db"].close.side_effect = RuntimeError("close fail")
    life["mf"].shutdown.side_effect = RuntimeError("shutdown fail")
    life["src"].stop.side_effect = RuntimeError("src stop fail")
    life["hb"].stop.side_effect = RuntimeError("hb stop fail")
    with TestClient(app) as c:
        c.get("/")


def test_lifespan_perception_and_diff_disabled(life):
    from api.main import app
    s = life["settings"]
    s.PERCEPTION_ENABLED = False
    s.SCREEN_DIFF_ENABLED = False
    s.DIFFERENCE_DETECTOR_ENABLED = False
    s.PERCEPTION_INTERNAL_ENABLED = False
    s.launch_at_startup = False
    with TestClient(app) as c:
        c.get("/")
    assert not life["ps"].setup.called
    assert not life["src"].start.called
    assert not life["hb"].start.called
    assert not life["ps"].stop.called


# ---------------------------------------------------------------------------
# GET /system/latest-version — 检查更新（GitHub release 比较）
# ---------------------------------------------------------------------------

def _auth_headers():
    from api.main import _SIMPLE_API_KEY
    return {"X-API-Key": _SIMPLE_API_KEY} if _SIMPLE_API_KEY else {}


def test_latest_version_update_available(client, auth_key, reset_rate_limit, monkeypatch):
    monkeypatch.setattr("api.main._CORTEX_VERSION", "2.0.0")
    monkeypatch.setattr(
        "api.main._fetch_latest_release_version",
        lambda timeout=8.0: {"latest": "2.4.1", "url": "https://github.com/x/tag/v2.4.1", "source": "redirect"},
    )
    resp = client.get("/system/latest-version", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["current"] == "2.0.0"
    assert data["latest"] == "2.4.1"
    assert data["update_available"] is True
    assert "github.com" in data["release_url"]


def test_latest_version_up_to_date(client, auth_key, reset_rate_limit, monkeypatch):
    monkeypatch.setattr("api.main._CORTEX_VERSION", "2.4.1")
    monkeypatch.setattr(
        "api.main._fetch_latest_release_version",
        lambda timeout=8.0: {"latest": "2.4.1", "url": "", "source": "api"},
    )
    resp = client.get("/system/latest-version", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["data"]["update_available"] is False


def test_latest_version_github_unreachable(client, auth_key, reset_rate_limit, monkeypatch):
    def _boom(timeout=8.0):
        raise RuntimeError("无法获取最新版本（GitHub 不可达或无发布）")
    monkeypatch.setattr("api.main._fetch_latest_release_version", _boom)
    resp = client.get("/system/latest-version", headers=_auth_headers())
    assert resp.status_code == 502
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UPDATE_CHECK_FAILED"


def test_parse_tag_version_variants():
    from api.main import _parse_tag_version as p
    assert p("v2.4.0") == "2.4.0"
    assert p("V3.0.1-beta") == "3.0.1-beta"
    assert p("2.5") == "2.5"
    assert p("") == ""


def test_cmp_versions_ordering():
    from api.main import _cmp_versions as c
    assert c("2.4.1", "2.4.0") == 1
    assert c("2.3.9", "2.4.0") == -1
    assert c("2.4.0", "2.4.0") == 0
    assert c("3.0", "2.9.9") == 1
    assert c("10.0.0", "9.99.99") == 1


def test_fetch_latest_release_redirect(monkeypatch):
    """重定向方式：Location 含 /tag/vX.Y.Z → 解析出版本"""
    import api.main as m

    class FakeResp:
        status_code = 302
        headers = {"Location": "https://github.com/x/y/releases/tag/v9.9.9"}

    fake = MagicMock()
    fake.get.return_value = FakeResp()
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    info = m._fetch_latest_release_version()
    assert info["latest"] == "9.9.9"
    assert info["source"] == "redirect"


def test_fetch_latest_release_api_fallback(monkeypatch):
    """重定向失败 → 回退 GitHub API JSON"""
    import api.main as m

    class FakeRespRedirect:
        status_code = 200  # 非重定向 → 跳过
        headers = {}

    class FakeRespApi:
        def __init__(self):
            self.ok = True
        def json(self):
            return {"tag_name": "v8.8.8", "html_url": "https://github.com/x/y/releases/tag/v8.8.8"}

    fake = MagicMock()
    fake.get.side_effect = [FakeRespRedirect(), FakeRespApi()]
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    info = m._fetch_latest_release_version()
    assert info["latest"] == "8.8.8"
    assert info["source"] == "api"
