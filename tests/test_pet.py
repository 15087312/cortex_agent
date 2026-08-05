"""桌面宠物：主会话保护 + 回复端点测试（最小 app，不加载感知）"""
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.errors import AppError
from config.settings import settings
from modules.thinking import chat_gateway


class _FakeRepo:
    def __init__(self):
        self.deleted = []

    def delete_session(self, session_id):
        self.deleted.append(session_id)


@pytest.fixture(autouse=True)
def chatonly_mode(monkeypatch):
    monkeypatch.setenv("CORTEX_MODE", "chatonly")


@pytest.fixture
def gw_app(monkeypatch):
    fake_repo = _FakeRepo()

    monkeypatch.setattr(chat_gateway, "ensure_shared_schema", lambda: None)
    monkeypatch.setattr(chat_gateway, "_get_chat_session_repo", lambda: fake_repo)

    app = FastAPI()

    @app.exception_handler(AppError)
    async def _app_error_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(chat_gateway.router)
    return app, fake_repo


def test_pet_session_delete_protected(gw_app):
    app, _ = gw_app
    pet = getattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main") or "pet_main"
    with TestClient(app) as client:
        resp = client.delete(f"/stream/session/{pet}")
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "PET_SESSION_PROTECTED"


def test_pet_session_excluded_from_batch_delete(gw_app):
    app, fake_repo = gw_app
    pet = getattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main") or "pet_main"
    temp = "pet_batch_temp_test"
    with TestClient(app) as client:
        resp = client.post("/stream/sessions/batch-delete",
                           json={"session_ids": [pet, temp]})
        assert resp.status_code == 200
        assert pet not in fake_repo.deleted
        assert temp in fake_repo.deleted


def test_pet_last_reply_endpoint(gw_app):
    app, _ = gw_app
    with TestClient(app) as client:
        resp = client.get("/stream/pet/last-reply")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "data" in body
