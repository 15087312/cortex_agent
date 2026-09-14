"""桌面宠物：主会话保护 + 回复端点测试（最小 app，不加载感知）"""
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from infra.errors import AppError
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

    from modules.desktop_pet import api as pet_api
    app.include_router(chat_gateway.router)
    app.include_router(pet_api.router)
    return app, fake_repo


def test_pet_session_delete_not_protected(gw_app):
    """桌宠已非会话形态：pet_main 不再受保护，删除走正常逻辑"""
    app, fake_repo = gw_app
    with TestClient(app) as client:
        resp = client.delete("/stream/session/pet_main")
        assert resp.status_code == 200
        assert "pet_main" in fake_repo.deleted


def test_pet_session_included_in_batch_delete(gw_app):
    """pet_main 不再是保留会话，参与批量删除"""
    app, fake_repo = gw_app
    temp = "pet_batch_temp_test"
    with TestClient(app) as client:
        resp = client.post("/stream/sessions/batch-delete",
                           json={"session_ids": ["pet_main", temp]})
        assert resp.status_code == 200
        assert "pet_main" in fake_repo.deleted
        assert temp in fake_repo.deleted


def test_pet_last_reply_endpoint(gw_app):
    app, _ = gw_app
    with TestClient(app) as client:
        resp = client.get("/stream/pet/last-reply")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "data" in body


def test_pet_actions_endpoint(gw_app):
    app, _ = gw_app
    with TestClient(app) as client:
        resp = client.get("/stream/pet/actions")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["categories"]) == 5
        assert len(data["actions"]) == 19
        ids = {a["id"] for a in data["actions"]}
        assert "cake" in ids and "hug" in ids and "joke" in ids


def test_pet_state_endpoint(gw_app):
    app, _ = gw_app
    with TestClient(app) as client:
        resp = client.get("/stream/pet/state")
        assert resp.status_code == 200
        values = resp.json()["data"]["values"]
        for k in ("mood", "satiety", "energy", "cleanliness"):
            assert k in values and 0 <= values[k] <= 100


def test_pet_state_model(tmp_path):
    from modules.desktop_pet.pet_state import PetState, DEFAULTS
    st = PetState(path=str(tmp_path / "pet_state.json"))
    # 衰减：1 小时后饱食下降
    t0 = st._updated_at
    v = st.read(t0 + 3600)
    assert v["satiety"] < DEFAULTS["satiety"]
    # 效果应用
    v2 = st.apply("cake", t0 + 3600)
    assert v2["satiety"] > v["satiety"]
    assert v2["energy"] == v["energy"]


def test_pet_chat_stream(gw_app, monkeypatch):
    app, _ = gw_app
    async def _fake_stream(self, text, extra_system="", on_reasoning=None):
        assert extra_system
        yield "你好"
        yield "呀"

    from modules.desktop_pet import pet_engine as pe
    monkeypatch.setattr(pe.PetEngine, "stream_chat", _fake_stream)
    with TestClient(app) as client:
        with client.stream("POST", "/stream/pet/chat", json={"action_id": "cake"}) as r:
            body = r.read().decode()
            assert "token" in body
            assert "你好" in body
            assert "done" in body
