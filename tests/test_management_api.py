"""management/api 测试（此前 38% 覆盖）：管理控制台端点"""
import asyncio
from unittest.mock import MagicMock, patch

import modules.management.api as api_mod
from modules.management.core.collector import ModuleInfo


def _fake_collector():
    c = MagicMock()
    c.collect_all.return_value = {
        "thinking": {"status": "healthy"},
        "memory": {"status": "healthy"},
    }
    return c


def _patch_store(monkeypatch):
    import modules.management.api_log_store as store_mod
    store = MagicMock()
    store.flush.return_value = None
    store.query.return_value = [{"method": "GET", "path": "/x"}]
    store.count.return_value = 1
    store.stats.return_value = {"total": 5}
    monkeypatch.setattr(store_mod.ApiLogStore, "get_instance", staticmethod(lambda: store))
    return store


def test_get_dashboard(monkeypatch):
    monkeypatch.setattr(api_mod, "_collector", _fake_collector())
    _patch_store(monkeypatch)
    out = asyncio.run(api_mod.get_dashboard())
    assert out["success"] is True
    assert out["data"]["health"]["healthy_modules"] == 2


def test_get_dashboard_no_collector(monkeypatch):
    monkeypatch.setattr(api_mod, "_collector", MagicMock())
    api_mod._collector.collect_all.return_value = {}
    _patch_store(monkeypatch)
    out = asyncio.run(api_mod.get_dashboard())
    assert out["data"]["health"]["health_percent"] == 100


def test_get_api_requests(monkeypatch):
    store = _patch_store(monkeypatch)
    out = asyncio.run(api_mod.get_api_requests(method="GET", path="/", status=0, limit=50, offset=0, since_hours=0))
    assert out["data"]["total"] == 1
    store.query.assert_called_once()


def test_get_api_requests_stats(monkeypatch):
    _patch_store(monkeypatch)
    out = asyncio.run(api_mod.get_api_requests_stats(since_hours=24))
    assert out["data"]["total"] == 5


def test_get_all_modules(monkeypatch):
    r = MagicMock()
    r.get_all_modules.return_value = [ModuleInfo(name="thinking", module_path="/x", has_api=True, has_core=True)]
    monkeypatch.setattr(api_mod, "_registry", r)
    out = asyncio.run(api_mod.get_all_modules())
    assert out["success"] is True
    assert out["data"]["modules"][0]["name"] == "thinking"
    assert out["data"]["total"] == 1


def test_get_modules_status(monkeypatch):
    monkeypatch.setattr(api_mod, "_collector", _fake_collector())
    out = asyncio.run(api_mod.get_modules_status())
    assert out["data"]["thinking"]["status"] == "healthy"


def test_get_module_detail_found(monkeypatch):
    r = MagicMock()
    r.get_module.return_value = ModuleInfo(name="thinking", module_path="/x", has_api=True)
    monkeypatch.setattr(api_mod, "_registry", r)
    monkeypatch.setattr(api_mod, "_collector", _fake_collector())
    out = asyncio.run(api_mod.get_module_detail("thinking"))
    assert out["success"] is True
    assert out["data"]["info"]["name"] == "thinking"


def test_get_orchestration(monkeypatch):
    from types import SimpleNamespace
    loader = MagicMock()
    loader.load.return_value = {"roles": {"orchestrator": {"name": "总指挥", "tier": "large", "personality": "稳重"}}}
    import config.prompts.loader as loader_mod
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    monkeypatch.setattr(loader_mod, "get_loader", lambda: loader)
    st = SimpleNamespace(
        get_persona=lambda k: "",
        get_system_override=lambda k: "",
        get_role_tools=lambda k: None,
        get_model_params=lambda k: None,
    )
    monkeypatch.setattr(cfg_mod, "settings", st)
    out = asyncio.run(api_mod.get_orchestration())
    assert out["data"]["agents"][0]["role"] == "orchestrator"


def test_get_todos(monkeypatch):
    import infra.tool_manager.tools.todo as todo_mod
    monkeypatch.setattr(todo_mod, "_load_todos", lambda sid: [{"id": "t1", "title": "任务"}])
    out = asyncio.run(api_mod.get_todos(session_id="s1"))
    assert out["data"]["todos"] == [{"id": "t1", "title": "任务"}]


def test_set_todo_status(monkeypatch):
    import infra.tool_manager.tools.todo as todo_mod
    state = {"saved": None}
    def load(sid):
        return [{"id": "t1", "status": "pending"}]
    def save(todos, sid):
        state["saved"] = todos
    monkeypatch.setattr(todo_mod, "_load_todos", load)
    monkeypatch.setattr(todo_mod, "_save_todos", save)
    out = asyncio.run(api_mod.set_todo_status("t1", {"status": "completed"}, session_id="s1"))
    assert out["success"] is True
    assert state["saved"][0]["status"] == "completed"


def test_set_todo_status_invalid_and_missing(monkeypatch):
    import infra.tool_manager.tools.todo as todo_mod
    monkeypatch.setattr(todo_mod, "_load_todos", lambda sid: [])
    out = asyncio.run(api_mod.set_todo_status("t1", {"status": "bad"}, session_id="s1"))
    assert out["success"] is False
    out2 = asyncio.run(api_mod.set_todo_status("t1", {"status": "done"}, session_id="s1"))
    assert out2["success"] is False
