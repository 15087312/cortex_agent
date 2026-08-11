"""自定义 agent 契约测试（§27.18 修复）：身份合并/创建端点/调度/model_id 消费"""
import asyncio
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from config.settings import settings
from modules.thinking import identity as ident_mod
from modules.thinking.identity import get_identities, ModelIdentity


@pytest.fixture
def tmp_personas(tmp_path, monkeypatch):
    """personas.yaml 隔离到临时目录"""
    path = tmp_path / "personas.yaml"
    monkeypatch.setattr(type(settings), "_personas_yaml_path", property(lambda self: path))
    return path


def _run(coro):
    return asyncio.run(coro)


# ── 契约：get_identities 必须包含自定义 agent（§27.18 修复）─────────────────

def test_get_identities_includes_custom_agent(tmp_personas):
    ident_mod._merged_identities = None
    settings.set_custom_agent("custom_boss", {
        "name": "自定义总指挥", "tier": "large", "model_id": "my_boss",
        "personality": "你是自定义总指挥。",
    })
    try:
        ids = get_identities()
        assert "custom_boss" in ids, "get_identities 必须包含自定义 agent"
        t = ids["custom_boss"]
        assert t["tier"] == "large"
        assert t["model_id"] == "my_boss"
        # from_template 必须能创建
        ident = ModelIdentity.from_template("custom_boss")
        assert ident.model_id == "my_boss"
    finally:
        settings.delete_custom_agent("custom_boss")
        ident_mod._merged_identities = None


def test_get_identities_custom_agent_default_model_id(tmp_personas):
    ident_mod._merged_identities = None
    settings.set_custom_agent("custom_expert", {"name": "自定义专家", "tier": "expert"})
    try:
        ids = get_identities()
        assert ids["custom_expert"]["model_id"] == "custom_expert_001"
    finally:
        settings.delete_custom_agent("custom_expert")
        ident_mod._merged_identities = None


def test_delete_custom_agent_removes_from_identities(tmp_personas):
    ident_mod._merged_identities = None
    settings.set_custom_agent("temp_agent", {"name": "x", "tier": "expert"})
    assert "temp_agent" in get_identities()
    settings.delete_custom_agent("temp_agent")
    ident_mod._merged_identities = None
    assert "temp_agent" not in get_identities()


# ── 端点：create_custom_agent（api/main.py，此前零测试）─────────────────────

def test_create_custom_agent_endpoint(tmp_personas):
    import api.main as api_mod
    ident_mod._merged_identities = None
    r = _run(api_mod.create_custom_agent({"role": "custom_dev", "name": "开发专家", "tier": "expert"}))
    assert r["success"] is True
    try:
        # 写入 personas.yaml + 进入身份表
        assert settings.get_custom_agent("custom_dev") is not None
        assert "custom_dev" in get_identities()
    finally:
        settings.delete_custom_agent("custom_dev")
        ident_mod._merged_identities = None


def test_create_custom_agent_validation(tmp_personas):
    import api.main as api_mod
    # 缺 tier
    r = _run(api_mod.create_custom_agent({"role": "x", "name": "y"}))
    assert r.status_code == 422
    # 与内置冲突
    r2 = _run(api_mod.create_custom_agent({"role": "orchestrator", "name": "z", "tier": "expert"}))
    assert r2.status_code == 409


# ── 调度：start_runner 能启动自定义 agent（真实 get_identities）──────────────

def test_start_runner_custom_agent(tmp_personas, monkeypatch):
    ident_mod._merged_identities = None
    settings.set_custom_agent("custom_super", {"name": "自定义主管", "tier": "supervisor", "model_id": "cs_01"})
    try:
        import modules.thinking.core.model_runner as mr_mod
        from modules.thinking.core.model_runner import ModelRunnerManager

        import modules.thinking.communication.interface as iface_mod
        monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: type("B", (), {"send": lambda self, m: None})())

        import modules.thinking.identity as ident2
        perms = MagicMock()
        perms.max_concurrent_runners = 3
        monkeypatch.setattr(ident2, "get_permissions", lambda key: perms)

        factory = MagicMock()
        import modules.thinking.model_factory as mf_mod
        monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
        factory.create_supervisor.return_value = MagicMock()

        class FakeRunner(MagicMock):
            def __init__(self, *a, **k):
                super().__init__()
                inst = k.get("model_instance")
                self.tier = "supervisor"
                self.model_id = "cs_01"
                self.identity = inst.identity
            async def start(self, *a, **k):
                pass

        monkeypatch.setattr(mr_mod, "ModelRunner", FakeRunner)

        m = ModelRunnerManager(session_id="s1")
        model_id = _run(m.start_runner("custom_super", "任务"))
        assert model_id is not None, "start_runner 必须能启动自定义 agent"
        assert model_id in m._runners
    finally:
        settings.delete_custom_agent("custom_super")
        ident_mod._merged_identities = None


# ── 激活开关：禁用的自定义 agent 拒绝启动（编排页开关真生效）───────────────

def test_start_runner_disabled_agent_rejected(tmp_personas, monkeypatch):
    ident_mod._merged_identities = None
    settings.set_custom_agent("disabled_agent", {"name": "禁用", "tier": "expert"})
    settings.set_agent_active("disabled_agent", False)
    try:
        import modules.thinking.core.model_runner as mr_mod
        from modules.thinking.core.model_runner import ModelRunnerManager
        import modules.thinking.communication.interface as iface_mod
        monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: type("B", (), {"send": lambda self, m: None})())
        import modules.thinking.identity as ident2
        perms = MagicMock()
        perms.max_concurrent_runners = 3
        monkeypatch.setattr(ident2, "get_permissions", lambda key: perms)
        factory = MagicMock()
        import modules.thinking.model_factory as mf_mod
        monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
        factory.create_expert.return_value = MagicMock()
        monkeypatch.setattr(mr_mod, "ModelRunner", MagicMock)

        m = ModelRunnerManager(session_id="s1")
        assert _run(m.start_runner("disabled_agent", "任务")) is None, "禁用的 agent 必须拒绝启动"
    finally:
        settings.delete_custom_agent("disabled_agent")
        ident_mod._merged_identities = None


def test_start_runner_enabled_agent_allowed(tmp_personas, monkeypatch):
    ident_mod._merged_identities = None
    settings.set_custom_agent("enabled_agent", {"name": "启用", "tier": "expert", "model_id": "ea"})
    try:
        import modules.thinking.core.model_runner as mr_mod
        from modules.thinking.core.model_runner import ModelRunnerManager
        import modules.thinking.communication.interface as iface_mod
        monkeypatch.setattr(iface_mod, "get_message_bus_port", lambda: type("B", (), {"send": lambda self, m: None})())
        import modules.thinking.identity as ident2
        perms = MagicMock()
        perms.max_concurrent_runners = 3
        monkeypatch.setattr(ident2, "get_permissions", lambda key: perms)
        factory = MagicMock()
        import modules.thinking.model_factory as mf_mod
        monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
        factory.create_expert.return_value = MagicMock()

        class FakeRunner(MagicMock):
            def __init__(self, *a, **k):
                super().__init__()
                self.tier = "expert"
                self.model_id = "ea"
                self.identity = k.get("model_instance").identity
            async def start(self, *a, **k):
                pass

        monkeypatch.setattr(mr_mod, "ModelRunner", FakeRunner)
        m = ModelRunnerManager(session_id="s1")
        mid = _run(m.start_runner("enabled_agent", "任务"))
        assert mid is not None, "启用的 agent 应能启动"
    finally:
        settings.delete_custom_agent("enabled_agent")
        ident_mod._merged_identities = None


# ── 审计修复：persona-presets 读侧 / 自定义 agent 人格进 prompt / LOG_LEVEL ──

def test_persona_presets_save_and_list(tmp_personas):
    """保存的预设能列出来（旧 get_persona_presets 对 values() 解包会 500）"""
    settings.save_persona_preset("p1", "人设A", {"orchestrator": "总指挥人设"})
    presets = settings.get_persona_presets()
    assert len(presets) == 1
    assert presets[0]["id"] == "p1"
    assert presets[0]["name"] == "人设A"
    assert presets[0]["personas"]["orchestrator"] == "总指挥人设"
    # 清理
    data = settings._load_personas_yaml()
    data.pop("persona_presets", None)
    settings._save_personas_yaml(data)


def test_composer_get_role_uses_custom_agent_personality(tmp_personas):
    """agent 模式 composer 读取自定义 agent 的 personality（不再回退 orchestrator 克隆）"""
    ident_mod._merged_identities = None
    settings.set_custom_agent("custom_writer", {
        "name": "自定义作者", "tier": "expert", "personality": "你是独特人格。",
        "speaking_style": "文艺", "expertise": "Python, 写作",
    })
    try:
        from config.prompts.composer import PromptComposer
        c = PromptComposer()
        role = c._get_role("custom_writer")
        assert role.personality == "你是独特人格。"
        assert role.name == "自定义作者"
        assert role.expertise == ["Python", "写作"]  # 逗号字符串已拆列表
    finally:
        settings.delete_custom_agent("custom_writer")
        ident_mod._merged_identities = None


def test_logger_reads_settings_log_level(monkeypatch):
    """设置页 LOG_LEVEL 应生效（此前 setup_logger 硬编码 INFO）"""
    import utils.logger as lg_mod
    monkeypatch.setattr(lg_mod._settings, "LOG_LEVEL", "DEBUG")
    lg = lg_mod.setup_logger("test_log_level_probe", log_dir="/tmp")
    assert lg.level == getattr(__import__("logging"), "DEBUG", 10)
    monkeypatch.setattr(lg_mod._settings, "LOG_LEVEL", "INFO")
