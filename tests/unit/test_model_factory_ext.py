"""model_factory 测试：实例创建 / 容量控制 / 生命周期 / 工具权限"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.model_factory as mf
from modules.thinking.model_factory import (
    ModelInstance,
    ModelInstanceFactory,
    get_model_factory,
    _factory,
)


def _identity(**kw):
    ident = MagicMock()
    ident.model_id = kw.get("model_id", "large_primary")
    ident.name = kw.get("name", "总指挥")
    ident.tier = kw.get("tier", "large")
    ident.role = kw.get("role", "orchestrator")
    ident.expertise = ["规划"]
    ident.api_key = kw.get("api_key", None)
    ident.api_url = kw.get("api_url", None)
    ident.max_tokens = kw.get("max_tokens", None)
    ident.temperature = kw.get("temperature", None)
    ident.tool_whitelist = kw.get("tool_whitelist", [])
    ident.permissions = kw.get("permissions", None)
    return ident


def _perm(max_instances=1):
    p = MagicMock()
    p.max_instances = max_instances
    return p


# ── ModelInstance ──────────────────────────────────────────────────────

def test_model_instance_props():
    inst = ModelInstance(identity=_identity(), client=MagicMock())
    assert inst.model_id == "large_primary"
    assert inst.tier == "large"
    assert inst.tool_whitelist == []
    d = inst.to_dict()
    assert d["model_id"] == "large_primary"
    assert d["role"] == "orchestrator"


def test_can_use_tool_via_controller(monkeypatch):
    inst = ModelInstance(identity=_identity(), client=MagicMock())
    ctrl = MagicMock()
    ctrl.get_visible_tools = MagicMock(return_value=["read_file", "*"])
    monkeypatch.setattr("modules.security_system.tool_permission_controller.get_tool_permission_controller", lambda: ctrl)
    assert inst.can_use_tool("read_file") is True
    ctrl2 = MagicMock()
    ctrl2.get_visible_tools = MagicMock(return_value=["read_file"])
    monkeypatch.setattr("modules.security_system.tool_permission_controller.get_tool_permission_controller", lambda: ctrl2)
    assert inst.can_use_tool("read_file") is True
    assert inst.can_use_tool("write_file") is False


def test_can_use_tool_fallback(monkeypatch):
    ident = _identity(tool_whitelist=["read_file", "*"])
    inst = ModelInstance(identity=ident, client=MagicMock())
    def boom(*a, **k):
        raise RuntimeError("no controller")
    monkeypatch.setattr("modules.security_system.tool_permission_controller.get_tool_permission_controller", boom)
    assert inst.can_use_tool("anything") is True  # "*" 通配
    ident2 = _identity(tool_whitelist=["read_file"])
    inst2 = ModelInstance(identity=ident2, client=MagicMock())
    assert inst2.can_use_tool("read_file") is True
    assert inst2.can_use_tool("other") is False


# ── 工厂 ───────────────────────────────────────────────────────────────

def _factory_inst():
    f = ModelInstanceFactory()
    f._instances = {}
    f._count_by_tier = {"large": 0, "supervisor": 0, "expert": 0}
    return f


def test_get_max_for_identity():
    f = _factory_inst()
    ident = _identity(tier="expert")
    assert f._get_max_for_identity(ident) == 10
    ident2 = _identity(tier="supervisor", permissions=_perm(3))
    assert f._get_max_for_identity(ident2) == 3
    ident3 = _identity(tier="bogus")
    assert f._get_max_for_identity(ident3) == 1


def test_ensure_capacity_recycles_oldest():
    f = _factory_inst()
    inst_old = ModelInstance(identity=_identity(model_id="m_old", role="orchestrator"), client=MagicMock(), created_at=1.0)
    inst_new = ModelInstance(identity=_identity(model_id="m_new", role="orchestrator"), client=MagicMock(), created_at=2.0)
    f._instances = {"m_old": inst_old, "m_new": inst_new}
    f.destroy = MagicMock()
    ident = _identity(model_id="m_3", role="orchestrator", permissions=_perm(2))
    f._ensure_capacity(ident, 2)
    f.destroy.assert_called_once_with("m_old")


def test_create_large_with_kwargs(monkeypatch):
    f = _factory_inst()
    client = MagicMock()
    monkeypatch.setattr("modules.thinking.model_factory.ModelIdentity.from_template", staticmethod(lambda k: _identity()))
    monkeypatch.setattr("infra.model.large_model_client.LargeModelClient", lambda **kw: client)
    ident = _identity(model_id="large_custom", api_key="k", api_url="u")
    inst = f.create_large(identity=ident, api_key="kk", api_url="uu")
    assert inst is not None
    assert inst.model_id == "large_custom"
    assert client.max_tokens == 4096


def test_create_large_default(monkeypatch):
    f = _factory_inst()
    client = MagicMock()
    monkeypatch.setattr("infra.model.large_model_client.LargeModelClient.from_config", staticmethod(lambda: client))
    inst = f.create_large(identity=_identity(model_id="large_primary"))
    assert inst.model_id == "large_primary"
    assert f.get_large() is inst


def test_create_supervisor(monkeypatch):
    f = _factory_inst()
    client = MagicMock()
    monkeypatch.setattr("infra.model.medium_model_client.MediumModelClient.from_config", staticmethod(lambda: client))
    ident = _identity(model_id="sup_1", tier="supervisor")
    inst = f.create_supervisor(identity=ident)
    assert inst.tier == "supervisor"
    assert f.get_supervisors() == [inst]


def test_create_expert(monkeypatch):
    f = _factory_inst()
    client = MagicMock()
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient.from_config", staticmethod(lambda: client))
    ident = _identity(model_id="expert_1", tier="expert")
    inst = f.create_expert(identity=ident)
    assert inst.tier == "expert"
    assert f.get_experts() == [inst]


def test_create_lite(monkeypatch):
    f = _factory_inst()
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", MagicMock)
    ident = _identity(model_id="lite_1", tier="expert")
    inst = f.create_lite(identity=ident)
    assert inst.model_id == "lite_1"


def test_register_destroy_get_list(monkeypatch):
    f = _factory_inst()
    ident = _identity(model_id="mid", tier="expert", role="code_writer")
    client = MagicMock()
    inst = f._register(ident, client)
    assert f.get("mid") is inst
    assert f.list_by_tier("expert") == [inst]
    assert len(f.list_all()) == 1
    assert f.destroy("nonexistent") is False
    assert f.destroy("mid") is True
    assert f.get("mid") is None
    assert f._count_by_tier["expert"] == 0


def test_get_status():
    f = _factory_inst()
    f._register(_identity(model_id="a", tier="large"), MagicMock())
    st = f.get_status()
    assert st["total_instances"] == 1
    assert st["by_tier"]["large"] == 1


async def test_close_all():
    f = _factory_inst()
    client_sync = MagicMock()
    client_sync.close = MagicMock()
    f._register(_identity(model_id="a", tier="large"), client_sync)
    async_client = MagicMock()
    async_client.close = MagicMock()
    async_client.close.return_value.__await__ = MagicMock()
    f._register(_identity(model_id="b", tier="supervisor"), async_client)
    await f.close_all()
    assert f._instances == {}


async def test_close_all_error(monkeypatch):
    f = _factory_inst()
    client = MagicMock()
    client.close = MagicMock(side_effect=RuntimeError("close fail"))
    f._register(_identity(model_id="a", tier="large"), client)
    await f.close_all()  # 不抛异常


def test_ensure_ready_and_is_ready(monkeypatch):
    f = _factory_inst()
    monkeypatch.setattr(f, "create_large", MagicMock())
    monkeypatch.setattr(f, "create_supervisor", MagicMock())
    monkeypatch.setattr(f, "create_expert", MagicMock())
    assert f.is_ready is False
    f.ensure_ready()
    f.create_large.assert_called_once()
    f.create_supervisor.assert_called_once()
    f.create_expert.assert_called_once()


def test_get_client_and_shutdown():
    f = _factory_inst()
    client = MagicMock()
    f._register(_identity(model_id="a", tier="expert"), client)
    assert f.get_client("expert") is client
    assert f.get_client("large") is None
    f.shutdown()
    assert f._instances == {}


async def test_reload_from_config(monkeypatch):
    f = _factory_inst()
    f.close_all = AsyncMock()
    f.ensure_ready = MagicMock()
    await f.reload_from_config()
    f.ensure_ready.assert_called_once()


async def test_reload_from_config_error(monkeypatch):
    f = _factory_inst()
    f.close_all = AsyncMock()
    f.ensure_ready = MagicMock(side_effect=RuntimeError("boom"))
    await f.reload_from_config()  # 不抛异常


def test_get_model_factory_singleton(monkeypatch):
    import modules.thinking.model_factory as mod
    monkeypatch.setattr(mod, "_factory", None)
    a = get_model_factory()
    b = get_model_factory()
    assert a is b
    assert mod._factory is a


# ── 输入上下文长度注入（context_length，按模型层级） ──────────────────────

def test_create_large_injects_context_length(monkeypatch):
    """create_large 把 LARGE_MODEL_CONTEXT_LENGTH 注入 identity.context_length"""
    from modules.thinking.model_factory import ModelInstanceFactory
    from modules.thinking.identity import ModelIdentity
    from config.settings import settings as cfg

    try:
        old = cfg.LARGE_MODEL_CONTEXT_LENGTH
        cfg.LARGE_MODEL_CONTEXT_LENGTH = 131072
        factory = ModelInstanceFactory.__new__(ModelInstanceFactory)
        factory._get_max_for_identity = MagicMock(return_value=10)
        factory._ensure_capacity = MagicMock()
        # mock client 构造，避免真实 API 调用
        import infra.model.large_model_client as lmc
        fake_client = MagicMock()
        monkeypatch.setattr(lmc, "LargeModelClient", MagicMock(return_value=fake_client))
        factory._register = MagicMock(return_value="instance")
        ident = ModelIdentity(model_id="large_primary", tier="large")
        factory.create_large(identity=ident)
        assert ident.context_length == 131072
    finally:
        cfg.LARGE_MODEL_CONTEXT_LENGTH = old


def test_create_expert_injects_context_length(monkeypatch):
    """create_expert 用 SMALL_MODEL_CONTEXT_LENGTH"""
    from modules.thinking.model_factory import ModelInstanceFactory
    from modules.thinking.identity import ModelIdentity
    from config.settings import settings as cfg

    try:
        old = cfg.SMALL_MODEL_CONTEXT_LENGTH
        cfg.SMALL_MODEL_CONTEXT_LENGTH = 32768
        factory = ModelInstanceFactory.__new__(ModelInstanceFactory)
        factory._get_max_for_identity = MagicMock(return_value=10)
        factory._ensure_capacity = MagicMock()
        import infra.model.small_model_client as smc
        monkeypatch.setattr(smc, "SmallModelClient", MagicMock())
        factory._register = MagicMock(return_value="instance")
        ident = ModelIdentity(model_id="expert_001", tier="expert")
        factory.create_expert(identity=ident)
        assert ident.context_length == 32768
    finally:
        cfg.SMALL_MODEL_CONTEXT_LENGTH = old


def test_context_length_respected_when_present():
    """identity 已有 context_length 时不覆盖"""
    from modules.thinking.model_factory import ModelInstanceFactory
    from modules.thinking.identity import ModelIdentity
    factory = ModelInstanceFactory.__new__(ModelInstanceFactory)
    factory._get_max_for_identity = MagicMock(return_value=10)
    factory._ensure_capacity = MagicMock()
    factory._register = MagicMock(return_value="instance")
    ident = ModelIdentity(model_id="large_primary", tier="large", context_length=99999)
    import infra.model.large_model_client as lmc
    orig = lmc.LargeModelClient
    lmc.LargeModelClient = MagicMock(return_value=MagicMock())
    try:
        factory.create_large(identity=ident)
    finally:
        lmc.LargeModelClient = orig
    assert ident.context_length == 99999
