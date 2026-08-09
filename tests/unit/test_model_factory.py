"""model_factory 测试（此前 36% 覆盖）：模型实例与工厂"""
from modules.thinking.identity import ModelIdentity
from modules.thinking.model_factory import ModelInstance, ModelInstanceFactory


def _identity(**kw):
    return ModelIdentity(
        model_id=kw.get("model_id", "large_primary"),
        tier=kw.get("tier", "large"),
        role=kw.get("role", "orchestrator"),
        name=kw.get("name", "总指挥"),
        expertise=kw.get("expertise", ["规划"]),
        tool_whitelist=kw.get("tool_whitelist", []),
        api_key=kw.get("api_key", ""),
        api_url=kw.get("api_url", ""),
        max_tokens=kw.get("max_tokens", 0),
        temperature=kw.get("temperature", 0),
        permissions=kw.get("permissions", None),
    )


def _client():
    """真实 LargeModelClient（构造不联网，仅用于实例存储）"""
    from infra.model.large_model_client import LargeModelClient
    return LargeModelClient(api_key="test", api_url="http://localhost:1/v1")


def test_model_instance_props():
    inst = ModelInstance(identity=_identity(), client=_client())
    assert inst.model_id == "large_primary"
    assert inst.tier == "large"
    assert inst.tool_whitelist == []


def test_can_use_tool_controller():
    """真实 ToolPermissionController：判定与真实可见工具一致"""
    inst = ModelInstance(identity=_identity(), client=_client())
    from config.settings import settings as _cfg
    from modules.security_system.tool_permission_controller import get_tool_permission_controller
    allowed = get_tool_permission_controller().get_visible_tools(
        tier="large", mode=_cfg.effective_execution_mode, role="orchestrator"
    )
    expect_all = "*" in allowed
    # 真实可用工具放行；未列出且无 * 时拒绝
    if expect_all:
        assert inst.can_use_tool("任意工具") is True
    else:
        assert inst.can_use_tool(allowed[0]) is True
        assert inst.can_use_tool("绝不存在工具xyz") is False


def test_can_use_tool_wildcard(monkeypatch):
    inst = ModelInstance(identity=_identity(tool_whitelist=["*"]), client=_client())
    import modules.security_system.tool_permission_controller as tpc
    monkeypatch.setattr(tpc, "get_tool_permission_controller", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert inst.can_use_tool("anything") is True


def test_to_dict():
    inst = ModelInstance(identity=_identity(), client=_client(), status="busy")
    d = inst.to_dict()
    assert d["model_id"] == "large_primary"
    assert d["status"] == "busy"
    assert d["role"] == "orchestrator"


def test_factory_max_for_identity():
    f = ModelInstanceFactory()
    ident = _identity(tier="expert")
    assert f._get_max_for_identity(ident) == 10  # 无自定义权限 → 默认上限
    from modules.thinking.identity import ModelPermissions
    ident.permissions = ModelPermissions(max_instances=3)
    assert f._get_max_for_identity(ident) == 3


def test_ensure_capacity_recycles_oldest(monkeypatch):
    f = ModelInstanceFactory()
    ident = _identity(role="expert")
    inst = ModelInstance(identity=ident, client=_client(), created_at=1)
    inst2 = ModelInstance(identity=_identity(role="expert"), client=_client(), created_at=5)
    f._instances = {"a": inst, "b": inst2}
    destroyed = []
    monkeypatch.setattr(f, "destroy", lambda mid: destroyed.append(mid))
    f._ensure_capacity(ident, max_n=2)
    assert destroyed == [inst.model_id]  # 回收最旧


def test_create_large(monkeypatch):
    f = ModelInstanceFactory()
    import infra.model.large_model_client as lmc
    created = []
    real = lmc.LargeModelClient
    monkeypatch.setattr(lmc, "LargeModelClient", lambda *a, **k: created.append(True) or real(*a, **k))
    inst = f.create_large(identity=_identity(api_key="k", api_url="http://localhost:1/v1"))
    assert created  # 真实构造调用
    assert f._instances[inst.model_id] is inst
