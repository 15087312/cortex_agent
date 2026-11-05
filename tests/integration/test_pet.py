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


def test_pet_chat_stream(gw_app, monkeypatch):
    app, _ = gw_app
    async def _fake_stream(self, text, extra_system="", on_reasoning=None):
        # 互动动作：输入是动作 prompt，不再注入状态描述
        assert text
        assert extra_system == ""
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
            # 状态已移除：done 帧不再携带 state/state_text
            assert '"state"' not in body and "state_text" not in body


def test_pet_chat_disabled_returns_409(gw_app, monkeypatch):
    """总开关关闭：/stream/pet/chat 拒绝回复"""
    app, _ = gw_app
    monkeypatch.setattr(settings, "DESKTOP_PET_ENABLED", False)
    with TestClient(app) as client:
        r = client.post("/stream/pet/chat", json={"text": "你好"})
        assert r.status_code == 409


def test_pet_config_idle_backoff_roundtrip(gw_app, monkeypatch, tmp_path):
    """e2e：PUT /stream/pet/config 透传 idle_backoff_max，GET 读回一致"""
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_cfg.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    app, _ = gw_app
    with TestClient(app) as client:
        # 显式设置 → 读回
        r = client.put("/stream/pet/config", json={"idle_backoff_max": 16})
        assert r.status_code == 200
        assert r.json()["data"]["idle_backoff_max"] == 16.0
        r = client.get("/stream/pet/config")
        assert r.status_code == 200
        assert r.json()["data"]["idle_backoff_max"] == 16.0


def test_pet_config_idle_backoff_clamped(gw_app, monkeypatch, tmp_path):
    """e2e：非法值（<1 / 非数字）在 HTTP 层也被 clamp/回退，不裸传存储"""
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_cfg2.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    app, _ = gw_app
    with TestClient(app) as client:
        r = client.put("/stream/pet/config", json={"idle_backoff_max": 0})
        assert r.status_code == 200
        assert r.json()["data"]["idle_backoff_max"] == 1.0  # 下限钉 1（关退避）

        # 负数同样被 store 钉回下限
        r = client.put("/stream/pet/config", json={"idle_backoff_max": -5})
        assert r.status_code == 200
        assert r.json()["data"]["idle_backoff_max"] == 1.0

        # 非数字被 pydantic 在 HTTP 层直接拒绝（422），不落库
        r = client.put("/stream/pet/config", json={"idle_backoff_max": "abc"})
        assert r.status_code == 422
