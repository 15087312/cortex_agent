"""security_system/api 测试：SecurityAPI 方法 + FastAPI 路由（TestClient）"""
import sys as _sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from modules.security_system import api as sec_api
from modules.security_system.api import SecurityAPI
from modules.security_system.security_level import SecurityLevel

_SETTINGS_MODULE = _sys.modules["config.settings"]


def _make_api(monkeypatch):
    api = SecurityAPI.__new__(SecurityAPI)
    api.switch_manager = MagicMock()
    api.audit_logger = MagicMock()
    api.core_validator = MagicMock()
    api.content_validator = MagicMock()
    return api


# ── SecurityAPI 方法 ──────────────────────────────────────────────────────────

def test_validate_input_core_fail(monkeypatch):
    api = _make_api(monkeypatch)
    api.core_validator.validate_all.return_value = (False, "高危")
    ok, result = api.validate_input("rm -rf /")
    assert ok is False
    assert result == "高危"
    api.audit_logger.log.assert_called_with("输入校验", "L0", "rm -rf /", False)


def test_validate_input_content_pass(monkeypatch):
    api = _make_api(monkeypatch)
    api.core_validator.validate_all.return_value = (True, "hi")
    api.switch_manager.is_enabled.return_value = True
    api.content_validator.validate.return_value = (True, "hi")
    ok, result = api.validate_input("hi")
    assert ok is True
    assert result == "hi"
    api.audit_logger.log.assert_any_call("输入校验", "L1", "hi", True)


def test_validate_input_content_fail(monkeypatch):
    api = _make_api(monkeypatch)
    api.core_validator.validate_all.return_value = (True, "hi")
    api.switch_manager.is_enabled.return_value = True
    api.content_validator.validate.return_value = (False, "敏感")
    ok, result = api.validate_input("hi")
    assert ok is False
    assert result == "敏感"


def test_validate_input_content_disabled(monkeypatch):
    api = _make_api(monkeypatch)
    api.core_validator.validate_all.return_value = (True, "hi")
    api.switch_manager.is_enabled.return_value = False
    ok, result = api.validate_input("hi")
    assert ok is True
    api.content_validator.validate.assert_not_called()


def test_set_security_switch_success(monkeypatch):
    api = _make_api(monkeypatch)
    api.switch_manager.set_switch.return_value = True
    assert api.set_security_switch(SecurityLevel.CONTENT, False) is True
    api.audit_logger.log.assert_called_with("开关修改", "L1", "设置为False", True)


def test_set_security_switch_fail(monkeypatch):
    api = _make_api(monkeypatch)
    api.switch_manager.set_switch.return_value = False
    assert api.set_security_switch(SecurityLevel.CONTENT, False) is False
    api.audit_logger.log.assert_not_called()


def test_get_security_state_and_logs(monkeypatch):
    api = _make_api(monkeypatch)
    api.switch_manager.get_all_state.return_value = {"L0": True}
    api.audit_logger.get_recent_logs.return_value = [{"a": 1}]
    assert api.get_security_state() == {"L0": True}
    assert api.get_audit_logs(limit=3) == [{"a": 1}]
    api.audit_logger.get_recent_logs.assert_called_with(3)


def test_security_api_singleton(monkeypatch):
    import modules.security_system.api as mod
    monkeypatch.setattr(mod, "_security_api", None)
    a = mod.get_security_api()
    b = mod.get_security_api()
    assert a is b
    monkeypatch.setattr(mod, "_security_api", None)


# ── FastAPI 路由 ──────────────────────────────────────────────────────────────

@pytest.fixture
def api_mock(monkeypatch):
    api = _make_api(monkeypatch)
    api.switch_manager.get_all_state.return_value = {"L0": True, "L1": True, "L4": True}
    api.audit_logger.get_recent_logs.return_value = [{"timestamp": "t", "event_type": "x"}]
    api.switch_manager.set_switch.return_value = True
    api.core_validator.validate_all.return_value = (True, "hello")
    api.content_validator.validate.return_value = (True, "hello")
    monkeypatch.setattr(sec_api, "get_security_api", lambda: api)
    return api


@pytest.fixture
def client(api_mock, monkeypatch):
    _SETTINGS_MODULE.settings.SIMPLE_API_KEY = "test-secret"
    app = FastAPI()
    from api.errors import AppError, ErrorCode, error_response
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.exception_handler(AppError)
    async def _ae(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=error_response(exc.code, exc.message).model_dump())

    @app.exception_handler(HTTPException)
    async def _he(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": {"message": exc.detail}})

    app.include_router(sec_api.router)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    _SETTINGS_MODULE.settings.SIMPLE_API_KEY = ""


def _auth_headers():
    return {"X-API-Key": "test-secret"}


def test_route_status(client):
    resp = client.get("/security/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["state"]["L0"] is True
    assert body["data"]["audit_enabled"] is True


def test_route_audit(client):
    resp = client.get("/security/audit?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["count"] == 1


def test_route_switch_ok(client):
    resp = client.post("/security/switch?level=L1&enable=false", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["data"]["result"] is True


def test_route_switch_invalid_level(client):
    resp = client.post("/security/switch?level=L9&enable=true", headers=_auth_headers())
    assert resp.status_code == 400
    assert resp.json()["success"] is False


def test_route_switch_unauthorized(client):
    resp = client.post("/security/switch?level=L1&enable=false")
    assert resp.status_code == 401


def test_route_validate_input(client):
    resp = client.post("/security/validate/input", json="hello", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["passed"] is True
    assert body["data"]["result"] == "hello"


def test_route_validate_input_unauthorized(client):
    resp = client.post("/security/validate/input", json="hello")
    assert resp.status_code == 401
