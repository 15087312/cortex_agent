"""model_factory 测试（此前 36% 覆盖）：模型实例与工厂"""
from unittest.mock import MagicMock, patch

from modules.thinking.model_factory import ModelInstance, ModelInstanceFactory


def _identity(**kw):
    ident = MagicMock()
    ident.model_id = kw.get("model_id", "large_primary")
    ident.tier = kw.get("tier", "large")
    ident.role = kw.get("role", "orchestrator")
    ident.name = kw.get("name", "总指挥")
    ident.expertise = kw.get("expertise", "规划")
    ident.tool_whitelist = kw.get("tool_whitelist", [])
    ident.api_key = kw.get("api_key", "")
    ident.api_url = kw.get("api_url", "")
    ident.max_tokens = kw.get("max_tokens", 0)
    ident.temperature = kw.get("temperature", 0)
    ident.permissions = kw.get("permissions", None)
    return ident


def test_model_instance_props():
    inst = ModelInstance(identity=_identity(), client=MagicMock())
    assert inst.model_id == "large_primary"
    assert inst.tier == "large"
    assert inst.tool_whitelist == []


def test_can_use_tool_controller(monkeypatch):
    inst = ModelInstance(identity=_identity(), client=MagicMock())
    import modules.security_system.tool_permission_controller as tpc
    import importlib
    cfg_mod = importlib.import_module("config.settings")
    ctrl = MagicMock()
    ctrl.get_visible_tools.return_value = ["tool_a"]
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: ctrl)
    monkeypatch.setattr(cfg_mod, "settings", type("C", (), {"effective_execution_mode": "edit"})())
    assert inst.can_use_tool("tool_a") is True
    assert inst.can_use_tool("tool_b") is False


def test_can_use_tool_wildcard(monkeypatch):
    inst = ModelInstance(identity=_identity(tool_whitelist=["*"]), client=MagicMock())
    import modules.security_system.tool_permission_controller as tpc
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert inst.can_use_tool("anything") is True


def test_to_dict():
    inst = ModelInstance(identity=_identity(), client=MagicMock(), status="busy")
    d = inst.to_dict()
    assert d["model_id"] == "large_primary"
    assert d["status"] == "busy"
    assert d["role"] == "orchestrator"


def test_factory_max_for_identity():
    f = ModelInstanceFactory()
    ident = _identity(tier="expert")
    assert f._get_max_for_identity(ident) == 10
    ident.permissions = MagicMock()
    ident.permissions.max_instances = 3
    assert f._get_max_for_identity(ident) == 3


def test_ensure_capacity_recycles_oldest(monkeypatch):
    f = ModelInstanceFactory()
    ident = _identity(role="expert")
    inst = ModelInstance(identity=ident, client=MagicMock(), created_at=1)
    f._instances = {"a": inst, "b": MagicMock(identity=_identity(role="expert"), created_at=5)}
    monkeypatch.setattr(f, "destroy", MagicMock())
    f._ensure_capacity(ident, max_n=2)
    f.destroy.assert_called_once()


def test_create_large(monkeypatch):
    f = ModelInstanceFactory()
    import infra.model.large_model_client as lmc
    Client = MagicMock()
    monkeypatch.setattr(lmc, "LargeModelClient", Client)
    inst = f.create_large(identity=_identity(api_key="k", api_url="u"))
    assert Client.called
    assert f._instances[inst.model_id] is inst
