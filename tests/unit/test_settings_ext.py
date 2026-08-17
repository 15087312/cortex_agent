"""
扩展测试：config/settings.py — personas.yaml 读写、用户级配置持久化、
记忆库管理、视觉 effective_* 属性、额外校验器、模块级兜底。
所有 ~/.cortex/* 路径均 monkeypatch 到 tmp_path，避免触碰真实用户配置。
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from unittest.mock import patch

from config.settings import Settings


@pytest.fixture(autouse=True)
def _sys_shim(monkeypatch):
    """_ensure_user_config 使用 sys.stderr 但模块未 import sys（生产潜在 bug）。
    仅在未定义 sys 时注入到模块命名空间，不改生产代码。"""
    import config.settings as _cs
    _cs = sys.modules["config.settings"]
    if not hasattr(_cs, "sys"):
        monkeypatch.setattr(_cs, "sys", sys, raising=False)


def _new_settings(tmp_path, monkeypatch, user_config=None, memory_libs=None,
                  personas_yaml=None, **overrides):
    """构建隔离 Settings：全部 ~/.cortex 路径通过 Path.home()→tmp_path 重定向。"""
    cortex = tmp_path / ".cortex"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Settings, "_USER_CONFIG_PATH", cortex / "settings.json")
    if user_config is not None:
        cortex.mkdir(parents=True, exist_ok=True)
        (cortex / "settings.json").write_text(json.dumps(user_config), encoding="utf-8")
    if memory_libs is not None:
        cortex.mkdir(parents=True, exist_ok=True)
        (cortex / "memory_libs.json").write_text(json.dumps(memory_libs), encoding="utf-8")
    if personas_yaml is not None:
        cortex.mkdir(parents=True, exist_ok=True)
        (cortex / "personas.yaml").write_text(personas_yaml, encoding="utf-8")
    return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# 基础属性 / 只读属性
# ---------------------------------------------------------------------------

class TestBasicProperties:
    def test_effective_execution_mode(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, EXECUTION_MODE="plan")
        assert s.effective_execution_mode == "plan"

    def test_is_delegation_available(self, tmp_path, monkeypatch):
        assert _new_settings(tmp_path, monkeypatch).is_delegation_available is True

    def test_sqlite_url(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.sqlite_url == f"sqlite:///{s.SQLITE_PATH}"

    def test_modifiable_fields(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert "LOG_LEVEL" in s._MODIFIABLE_FIELDS
        assert "EXECUTION_MODE" in s._MODIFIABLE_FIELDS


# ---------------------------------------------------------------------------
# 额外校验器（EXECUTION_MODE）
# ---------------------------------------------------------------------------

class TestExecutionModeValidator:
    def test_lowercases_valid_mode(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, EXECUTION_MODE="YOLO")
        assert s.EXECUTION_MODE == "yolo"

    def test_rejects_invalid_mode(self, tmp_path, monkeypatch):
        with pytest.raises(Exception):
            _new_settings(tmp_path, monkeypatch, EXECUTION_MODE="nonsense")


# ---------------------------------------------------------------------------
# personas.yaml — 人设 / 覆盖 / 工具权限 / 技能 / 预设 / 自定义 agent
# ---------------------------------------------------------------------------

class TestPersonasYaml:
    def test_get_persona_from_yaml(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, personas_yaml="personas:\n  user: 你好\n")
        assert s.get_persona("user") == "你好"

    def test_get_persona_falls_back_to_persona_prompts(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, PERSONA_PROMPTS='{"user": "legacy"}')
        assert s.get_persona("user") == "legacy"

    def test_get_persona_invalid_json(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, PERSONA_PROMPTS="{not json")
        assert s.get_persona("user") == ""

    def test_get_persona_unknown_role(self, tmp_path, monkeypatch):
        assert _new_settings(tmp_path, monkeypatch).get_persona("ghost") == ""

    def test_get_role_persona_user_override_priority(self, tmp_path, monkeypatch):
        """统一人设入口：用户自定义优先于内置 roles.yaml"""
        s = _new_settings(tmp_path, monkeypatch,
                          personas_yaml="personas:\n  orchestrator: 我的自定义总指挥\n")
        p = s.get_role_persona("orchestrator")
        assert p == "我的自定义总指挥"

    def test_get_role_persona_custom_agent_fallback(self, tmp_path, monkeypatch):
        """无 personas[role] → 回退 custom_agents 的 personality"""
        s = _new_settings(tmp_path, monkeypatch, personas_yaml=(
            "custom_agents:\n"
            "  code_writer:\n"
            "    name: 写码\n"
            "    personality: 专注代码实现\n"
            "    speaking_style: 简洁\n"
            "    expertise: [python]\n"))
        p = s.get_role_persona("code_writer")
        assert "专注代码实现" in p
        assert "【风格】简洁" in p
        assert "【擅长】python" in p

    def test_get_role_persona_builtin_fallback(self, tmp_path, monkeypatch):
        """无自定义 → 回退 roles.yaml 内置"""
        s = _new_settings(tmp_path, monkeypatch)
        p = s.get_role_persona("orchestrator")
        assert "用户与系统之间的唯一桥梁" in p  # roles.yaml 内置总指挥

    def test_get_role_persona_unknown(self, tmp_path, monkeypatch):
        assert _new_settings(tmp_path, monkeypatch).get_role_persona("ghost") == ""

    def test_get_role_persona_roles_yaml_corrupt(self, tmp_path, monkeypatch):
        """roles.yaml 读取失败 → 内置兜底为空，不崩（防御）"""
        import yaml as _yaml
        def boom(*a, **k):
            raise RuntimeError("roles.yaml 损坏")
        monkeypatch.setattr(_yaml, "safe_load", boom)
        s = _new_settings(tmp_path, monkeypatch)
        assert s.get_role_persona("orchestrator") == ""

    def test_get_role_persona_custom_agent_expertise_string(self, tmp_path, monkeypatch):
        """custom_agent.expertise 为逗号分隔字符串 → 拆成列表（防御）"""
        s = _new_settings(tmp_path, monkeypatch, personas_yaml=(
            "custom_agents:\n"
            "  code_writer:\n"
            "    name: 写码\n"
            "    personality: 专注代码\n"
            "    expertise: python,测试\n"))
        p = s.get_role_persona("code_writer")
        assert "【擅长】python、测试" in p

    def test_compose_persona_empty(self, tmp_path, monkeypatch):
        """全空字段 → 返回空串（防御）"""
        s = _new_settings(tmp_path, monkeypatch)
        assert s._compose_persona("", "", None) == ""
        assert s._compose_persona("  ", "") == ""
        assert s._compose_persona("人格", "", []) == "人格"

    def test_set_persona_writes_and_returns(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.set_persona("user", " 你好 ") == "你好"
        assert s.get_persona("user") == "你好"

    def test_set_persona_clears(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.set_persona("user", "你好")
        assert s.set_persona("user", "") == ""

    def test_system_override_roundtrip(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.set_system_override("user", "override") == "override"
        assert s.get_system_override("user") == "override"
        s.set_system_override("user", "")
        assert s.get_system_override("user") == ""

    def test_role_tools_roundtrip(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.set_role_tools("user", {"whitelist": ["a"], "blacklist": ["b"]}) == {
            "whitelist": ["a"], "blacklist": ["b"]}
        assert s.get_role_tools("user") == {"whitelist": ["a"], "blacklist": ["b"]}

    def test_set_role_tools_invalid_clears(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.set_role_tools("user", {"whitelist": ["a"]})
        s.set_role_tools("user", {})
        assert s.get_role_tools("user") == {}

    def test_get_role_tools_non_dict(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, personas_yaml="role_tools:\n  user: nope\n")
        assert s.get_role_tools("user") == {}

    def test_model_params_roundtrip(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        cfg = {"temperature": 0.3, "max_tokens": 100, "drop": None, "empty": ""}
        assert s.set_model_params("user", cfg) == {"temperature": 0.3, "max_tokens": 100}
        assert s.get_model_params("user") == {"temperature": 0.3, "max_tokens": 100}

    def test_set_model_params_all_empty_clears(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.set_model_params("user", {"temperature": 0.3})
        s.set_model_params("user", {})
        assert s.get_model_params("user") == {}

    def test_set_model_params_cleaned_empty_clears(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.set_model_params("user", {"temperature": 0.3})
        s.set_model_params("user", {"a": None, "b": ""})
        assert s.get_model_params("user") == {}

    def test_set_model_params_invalid_clears(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.set_model_params("user", {"temperature": 0.3})
        s.set_model_params("user", None)
        assert s.get_model_params("user") == {}

    def test_get_model_params_non_dict(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, personas_yaml="model_params:\n  user: 5\n")
        assert s.get_model_params("user") == {}

    def test_role_skills_roundtrip(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.set_role_skills("user", ["s1", "s2"]) == ["s1", "s2"]
        assert s.get_role_skills("user") == ["s1", "s2"]

    def test_get_role_skills_empty_and_non_list(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.get_role_skills("ghost") == []
        s2 = _new_settings(tmp_path, monkeypatch, personas_yaml="role_skills:\n  user: xyz\n")
        assert s2.get_role_skills("user") == []

    def test_set_role_skills_clears(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.set_role_skills("user", ["s1"])
        assert s.set_role_skills("user", []) == []

    def test_forced_skill_roundtrip(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.set_forced_skill(" main ") == "main"
        assert s.get_forced_skill() == "main"
        assert s.set_forced_skill("") == ""

    def test_custom_agents_crud(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.get_custom_agents() == []
        assert s.get_custom_agent("x") is None
        agent = s.set_custom_agent("x", {"name": "X", "tier": "总指挥"})
        assert agent["role"] == "x"
        assert s.get_custom_agent("x") == agent
        assert s.get_custom_agents() == [agent]
        assert s.delete_custom_agent("x") is True
        assert s.delete_custom_agent("x") is False
        assert s.get_custom_agents() == []

    def test_get_custom_agents_non_dict(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, personas_yaml="custom_agents: [1, 2]\n")
        assert s.get_custom_agents() == []

    def test_agent_active_default_and_set(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.get_agent_active("x") is True
        s.set_agent_active("x", False)
        assert s.get_agent_active("x") is False

    def test_deactivate_same_tier(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.set_custom_agent("ca_same", {"tier": "总指挥"})
        s.set_custom_agent("ca_other", {"tier": "其他"})
        builtin = {
            "keep": {"tier": "总指挥"},
            "disable_me": {"tier": "总指挥"},
            "other": {"tier": "专家"},
        }
        s.deactivate_same_tier("keep", "总指挥", builtin)
        assert s.get_agent_active("keep") is True
        assert s.get_agent_active("disable_me") is False
        assert s.get_agent_active("other") is True
        assert s.get_agent_active("ca_same") is False
        assert s.get_agent_active("ca_other") is True

    def test_persona_presets_crud(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        assert s.get_persona_presets() == []
        s.save_persona_preset("p1", "预设1", {"user": "你好"})
        presets = s.get_persona_presets()
        assert presets == [{"id": "p1", "name": "预设1", "personas": {"user": "你好"}}]
        assert s.get_persona_preset("p1")["name"] == "预设1"
        assert s.get_persona_preset("missing") is None
        assert s.delete_persona_preset("p1") is True
        assert s.delete_persona_preset("p1") is False

    def test_get_persona_presets_non_dict(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, personas_yaml="persona_presets: nope\n")
        assert s.get_persona_presets() == []

    def test_apply_persona_preset(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s.save_persona_preset("p1", "预设", {"user": "你好", "empty": ""})
        assert s.apply_persona_preset("p1") is True
        assert s.get_persona("user") == "你好"
        assert s.get_persona("empty") == ""
        assert s.apply_persona_preset("missing") is False

    def test_invalidate_identity_cache_swallows_import_error(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "modules.thinking.identity", None)
        Settings._invalidate_identity_cache()

    def test_load_personas_yaml_non_dict(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, personas_yaml="just a string\n")
        assert s._load_personas_yaml() == {}

    def test_load_personas_yaml_retries_on_read_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Settings, "_personas_yaml_path", tmp_path)
        monkeypatch.setattr("time.sleep", lambda _: None)
        s = _new_settings(tmp_path, monkeypatch)
        assert s._load_personas_yaml() == {}

    def test_save_personas_yaml_atomic(self, tmp_path, monkeypatch):
        import yaml as _yaml
        s = _new_settings(tmp_path, monkeypatch)
        s.set_persona("user", "你好")
        saved = _yaml.safe_load((tmp_path / ".cortex" / "personas.yaml").read_text(encoding="utf-8"))
        assert saved["personas"]["user"] == "你好"
        assert not (tmp_path / ".cortex" / "personas.tmp").exists()

    def test_save_personas_yaml_failure_warns(self, tmp_path, monkeypatch, capsys):
        s = _new_settings(tmp_path, monkeypatch)
        monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        s.set_persona("user", "你好")
        err = capsys.readouterr().err
        assert "人设 yaml 保存失败" in err


# ---------------------------------------------------------------------------
# effective_vision_* 属性
# ---------------------------------------------------------------------------

class TestEffectiveVision:
    def test_effective_vision_api_url(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, VISION_API_URL="https://vision.example")
        assert s.effective_vision_api_url == "https://vision.example"
        s2 = _new_settings(tmp_path, monkeypatch, OPENAI_API_BASE_URL="https://openai.example")
        assert s2.effective_vision_api_url == "https://openai.example"

    def test_effective_vision_api_key(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, VISION_API_KEY="vkey")
        assert s.effective_vision_api_key == "vkey"
        s2 = _new_settings(tmp_path, monkeypatch, OPENAI_API_KEY="okey")
        assert s2.effective_vision_api_key == "okey"

    def test_effective_vision_api_model_explicit(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, VISION_API_MODEL="qwen-vl")
        assert s.effective_vision_api_model == "qwen-vl"

    def test_effective_vision_api_model_deepseek(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch,
                          VISION_API_URL="https://api.deepseek.com", IMAGE_MODEL_NAME="img")
        assert s.effective_vision_api_model == "deepseek-v4-flash"

    def test_effective_vision_api_model_openai_url(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch,
                          VISION_API_URL="https://api.openai.com", IMAGE_MODEL_NAME="img")
        assert s.effective_vision_api_model == "img"

    def test_effective_vision_local_and_mlx_model(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, VISION_LOCAL_MODEL="local-vl")
        assert s.effective_vision_local_model == "local-vl"
        s2 = _new_settings(tmp_path, monkeypatch)
        assert s2.effective_vision_local_model == s2.QWEN_VL_MODEL_NAME
        s3 = _new_settings(tmp_path, monkeypatch, VISION_MLX_MODEL="mlx-vl")
        assert s3.effective_vision_mlx_model == "mlx-vl"
        s4 = _new_settings(tmp_path, monkeypatch)
        assert s4.effective_vision_mlx_model == s4.QWEN_VL_MLX_MODEL_NAME


# ---------------------------------------------------------------------------
# 用户级配置（~/.cortex/settings.json）
# ---------------------------------------------------------------------------

class TestUserConfig:
    def test_ensure_user_config_creates_template(self, tmp_path, monkeypatch, capsys):
        deep = tmp_path / "a" / "b"
        monkeypatch.setattr(Settings, "_USER_CONFIG_PATH", deep / "settings.json")
        monkeypatch.setattr(Settings, "_personas_yaml_path", tmp_path / "personas.yaml")
        monkeypatch.setattr(Settings, "_memory_libs_path", tmp_path / "memory_libs.json")
        Settings(_env_file=None)
        err = capsys.readouterr().err
        assert "已创建用户配置目录" in err
        assert "已创建用户配置模板" in err
        data = json.loads((deep / "settings.json").read_text(encoding="utf-8"))
        assert data["LARGE_MODEL_API_KEY"] == ""

    def test_load_user_config_applies_and_skips_unknown(self, tmp_path, monkeypatch, capsys):
        cfg = {"LOG_LEVEL": "DEBUG", "UNKNOWN_KEY": "x", "_private": "y"}
        s = _new_settings(tmp_path, monkeypatch, user_config=cfg)
        assert s.LOG_LEVEL == "DEBUG"
        assert not hasattr(s, "UNKNOWN_KEY")
        assert not hasattr(s, "_private")
        err = capsys.readouterr().err
        assert "已应用用户配置" in err
        assert "LOG_LEVEL" in err
        assert "UNKNOWN_KEY" not in err

    def test_load_user_config_missing_file(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        (tmp_path / ".cortex" / "settings.json").unlink()
        assert s._load_user_config() is None

    def test_load_user_config_no_overridable_keys(self, tmp_path, monkeypatch, capsys):
        s = _new_settings(tmp_path, monkeypatch, user_config={"UNKNOWN": "x", "_c": "y"})
        assert not hasattr(s, "UNKNOWN")
        assert "已应用用户配置" not in capsys.readouterr().err

    def test_load_user_config_corrupt_json(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Settings, "_USER_CONFIG_PATH", tmp_path / "settings.json")
        monkeypatch.setattr(Settings, "_personas_yaml_path", tmp_path / "personas.yaml")
        monkeypatch.setattr(Settings, "_memory_libs_path", tmp_path / "memory_libs.json")
        (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
        s = Settings(_env_file=None)
        assert "用户配置文件解析失败" in capsys.readouterr().err
        assert s.LOG_LEVEL == "INFO"

    def test_save_user_config_default_keys(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, PERSONA_PROMPTS='{"u": "1"}')
        assert s.save_user_config() is True
        data = json.loads((tmp_path / ".cortex" / "settings.json").read_text(encoding="utf-8"))
        assert data["PERSONA_PROMPTS"] == '{"u": "1"}'
        assert data["SYSTEM_PROMPT_OVERRIDES"] == "{}"

    def test_save_user_config_custom_keys_merges_existing(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, LOG_LEVEL="DEBUG", USER_NAME="tester")
        assert s.save_user_config(["LOG_LEVEL", "USER_NAME", "NOT_A_FIELD"]) is True
        data = json.loads((tmp_path / ".cortex" / "settings.json").read_text(encoding="utf-8"))
        assert data["LOG_LEVEL"] == "DEBUG"
        assert data["USER_NAME"] == "tester"
        assert "NOT_A_FIELD" not in data
        assert "LARGE_MODEL_API_KEY" in data  # 原模板内容被合并保留

    def test_save_user_config_missing_file(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        (tmp_path / ".cortex" / "settings.json").unlink()
        assert s.save_user_config(["LOG_LEVEL"]) is True
        data = json.loads((tmp_path / ".cortex" / "settings.json").read_text(encoding="utf-8"))
        assert data["LOG_LEVEL"] == "INFO"

    def test_save_user_config_corrupt_existing(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        (tmp_path / ".cortex" / "settings.json").write_text("{broken", encoding="utf-8")
        assert s.save_user_config(["LOG_LEVEL"]) is True
        data = json.loads((tmp_path / ".cortex" / "settings.json").read_text(encoding="utf-8"))
        assert data["LOG_LEVEL"] == "INFO"

    def test_save_user_config_failure(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Settings, "_USER_CONFIG_PATH", tmp_path / "settings.json")
        monkeypatch.setattr(Settings, "_personas_yaml_path", tmp_path / "personas.yaml")
        monkeypatch.setattr(Settings, "_memory_libs_path", tmp_path / "memory_libs.json")
        s = Settings(_env_file=None)
        monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        assert s.save_user_config(["LOG_LEVEL"]) is False
        assert "用户配置保存失败" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 记忆库管理
# ---------------------------------------------------------------------------

class TestMemoryLibs:
    def test_apply_current_memory_lib_at_startup(self, tmp_path, monkeypatch):
        libs = {
            "current": "work",
            "libs": {
                "默认": {"db": "/x/default.db", "faiss": "/x/d.faiss", "id_map": "/x/d.map"},
                "work": {"db": "/x/work.db", "faiss": "/x/w.faiss", "id_map": "/x/w.map"},
            },
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.MEMORY_DB_PATH == "/x/work.db"
        assert s.MEMORY_FAISS_INDEX == "/x/w.faiss"
        assert s.MEMORY_ID_MAP == "/x/w.map"

    def test_apply_current_memory_lib_no_match(self, tmp_path, monkeypatch):
        libs = {"current": "missing", "libs": {"默认": {"db": "/x/a"}}}
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs, MEMORY_DB_PATH="/keep.db")
        assert s.MEMORY_DB_PATH == "/keep.db"

    def test_apply_current_memory_lib_error_swallowed(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        monkeypatch.setattr(Settings, "get_memory_libs",
                            lambda self: (_ for _ in ()).throw(RuntimeError("x")))
        s._apply_current_memory_lib()

    def test_get_memory_libs_default(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        data = s.get_memory_libs()
        assert data["current"] == "默认"
        assert data["libs"]["默认"]["db"] == s.MEMORY_DB_PATH

    def test_get_memory_libs_corrupt_retries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Settings, "_memory_libs_path", tmp_path / "memory_libs.json")
        monkeypatch.setattr("time.sleep", lambda _: None)
        (tmp_path / "memory_libs.json").write_text("{broken", encoding="utf-8")
        s = _new_settings(tmp_path, monkeypatch)
        data = s.get_memory_libs()
        assert data["current"] == "默认"

    def test_get_memory_libs_empty_libs_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Settings, "_memory_libs_path", tmp_path / "memory_libs.json")
        (tmp_path / "memory_libs.json").write_text(
            json.dumps({"current": "x", "libs": {}}), encoding="utf-8")
        s = _new_settings(tmp_path, monkeypatch)
        data = s.get_memory_libs()
        assert data["current"] == "默认"

    def test_save_memory_libs(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s._save_memory_libs({"current": "x", "libs": {"x": {"db": "/x/db"}}})
        data = json.loads((tmp_path / ".cortex" / "memory_libs.json").read_text(encoding="utf-8"))
        assert data["current"] == "x"

    def test_save_memory_libs_failure(self, tmp_path, monkeypatch, capsys):
        s = _new_settings(tmp_path, monkeypatch)
        monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        s._save_memory_libs({"current": "x", "libs": {}})
        assert "记忆库配置保存失败" in capsys.readouterr().err

    def test_reset_memory_singletons_swallows_import_errors(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "modules.memory.event_store", None)
        monkeypatch.setitem(sys.modules, "modules.memory.event_retrieval", None)
        monkeypatch.setitem(sys.modules, "modules.memory.event_reducer", None)
        s = _new_settings(tmp_path, monkeypatch)
        s._reset_memory_singletons()

    def test_reset_memory_singletons_real_imports(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        s._reset_memory_singletons()
        import modules.memory.event_store as es_mod
        import modules.memory.event_retrieval as er_mod
        import modules.memory.event_reducer as red_mod
        assert es_mod.EventStore._instance is None
        assert er_mod._retrieval_instance is None
        assert red_mod._reducer_instance is None

    def test_switch_memory_lib(self, tmp_path, monkeypatch):
        libs = {
            "current": "默认",
            "libs": {
                "默认": {"db": "/d", "faiss": "/d.f", "id_map": "/d.m"},
                "work": {"db": "/w", "faiss": "/w.f", "id_map": "/w.m"},
            },
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        reset = []
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: reset.append(True))
        assert s.switch_memory_lib("work") is True
        assert s.MEMORY_DB_PATH == "/w"
        assert s.MEMORY_FAISS_INDEX == "/w.f"
        assert s.MEMORY_ID_MAP == "/w.m"
        assert reset == [True]
        saved = json.loads((tmp_path / ".cortex" / "memory_libs.json").read_text(encoding="utf-8"))
        assert saved["current"] == "work"

    def test_switch_memory_lib_unknown(self, tmp_path, monkeypatch):
        libs = {"current": "默认", "libs": {"默认": {"db": "/d", "faiss": "/d.f", "id_map": "/d.m"}}}
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.switch_memory_lib("ghost") is False

    def test_switch_memory_lib_switches_causal(self, tmp_path, monkeypatch):
        """切换记忆库时因果图路径跟随切换（每个库独立 causal.db）"""
        libs = {
            "current": "默认",
            "libs": {
                "默认": {"db": "/d", "faiss": "/d.f", "id_map": "/d.m", "causal": "/d.causal"},
                "work": {"db": "/w", "faiss": "/w.f", "id_map": "/w.m", "causal": "/w.causal"},
            },
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        assert s.switch_memory_lib("work") is True
        assert s.CAUSAL_DB_PATH == "/w.causal"

    def test_switch_memory_lib_legacy_lib_derives_causal(self, tmp_path, monkeypatch):
        """旧库无 causal 字段 → 按库名派生并补写 memory_libs.json（兼容老数据）"""
        libs = {
            "current": "默认",
            "libs": {"默认": {"db": "/d", "faiss": "/d.f", "id_map": "/d.m"},
                     "work": {"db": "/w", "faiss": "/w.f", "id_map": "/w.m"}},
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        assert s.switch_memory_lib("work") is True
        assert s.CAUSAL_DB_PATH.endswith("causal_work.db")
        saved = json.loads((tmp_path / ".cortex" / "memory_libs.json").read_text(encoding="utf-8"))
        assert saved["libs"]["work"]["causal"].endswith("causal_work.db")

    def test_create_memory_lib_includes_causal(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        lib = s.create_memory_lib("工作库")
        assert lib["causal"].endswith("causal_工作库.db")
        assert s.CAUSAL_DB_PATH == lib["causal"]

    def test_apply_current_memory_lib_sets_causal(self, tmp_path, monkeypatch):
        """启动时应用当前记忆库的因果图路径"""
        libs = {
            "current": "work",
            "libs": {
                "默认": {"db": "/d", "faiss": "/d.f", "id_map": "/d.m", "causal": "/d.causal"},
                "work": {"db": "/w", "faiss": "/w.f", "id_map": "/w.m", "causal": "/w.causal"},
            },
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.CAUSAL_DB_PATH == "/w.causal"

    def test_reset_memory_singletons_resets_causal_graph(self, tmp_path, monkeypatch):
        """切换记忆库后重置 CausalGraph 单例，按新因果图路径重新加载"""
        s = _new_settings(tmp_path, monkeypatch)
        s._reset_memory_singletons()
        import modules.memory.causal_graph as cg_mod
        assert cg_mod.CausalGraph._instance is None

    def test_create_memory_lib(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        lib = s.create_memory_lib("工作库")
        assert lib is not None
        assert lib["db"].endswith("memory_工作库.db")
        assert s.MEMORY_DB_PATH == lib["db"]
        assert s.get_memory_libs()["current"] == "工作库"
        assert s.create_memory_lib("工作库") is None

    def test_create_memory_lib_sanitizes_name(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        lib = s.create_memory_lib("a/b*c")
        assert "memory_abc.db" in lib["db"]

    def test_rename_memory_lib(self, tmp_path, monkeypatch):
        libs = {
            "current": "a",
            "libs": {"a": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"},
                     "b": {"db": "/b", "faiss": "/b.f", "id_map": "/b.m"}},
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.rename_memory_lib("a", "c") is True
        data = s.get_memory_libs()
        assert "a" not in data["libs"]
        assert data["libs"]["c"]["db"] == "/a"
        assert data["current"] == "c"

    def test_rename_memory_lib_invalid(self, tmp_path, monkeypatch):
        libs = {
            "current": "a",
            "libs": {"a": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"},
                     "b": {"db": "/b", "faiss": "/b.f", "id_map": "/b.m"}},
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.rename_memory_lib("ghost", "c") is False
        assert s.rename_memory_lib("a", "b") is False

    def test_rename_non_current_lib(self, tmp_path, monkeypatch):
        libs = {
            "current": "a",
            "libs": {"a": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"},
                     "b": {"db": "/b", "faiss": "/b.f", "id_map": "/b.m"}},
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.rename_memory_lib("b", "c") is True
        data = s.get_memory_libs()
        assert "c" in data["libs"]
        assert data["current"] == "a"

    def test_delete_memory_lib_not_exists(self, tmp_path, monkeypatch):
        libs = {"current": "a", "libs": {"a": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"}}}
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        assert s.delete_memory_lib("ghost") is False

    def test_delete_memory_lib_last_recreates_default(self, tmp_path, monkeypatch):
        libs = {"current": "a", "libs": {"a": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"}}}
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        assert s.delete_memory_lib("a") is True
        data = s.get_memory_libs()
        assert data["current"] == "默认"
        assert "默认" in data["libs"]
        assert data["libs"]["默认"]["db"].endswith("memory.db")

    def test_delete_current_memory_lib_switches_to_default(self, tmp_path, monkeypatch):
        libs = {
            "current": "a",
            "libs": {
                "a": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"},
                "默认": {"db": "/d", "faiss": "/d.f", "id_map": "/d.m"},
                "b": {"db": "/b", "faiss": "/b.f", "id_map": "/b.m"},
            },
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        assert s.delete_memory_lib("a") is True
        assert s.get_memory_libs()["current"] == "默认"

    def test_delete_current_without_default_uses_sorted(self, tmp_path, monkeypatch):
        libs = {
            "current": "beta",
            "libs": {"beta": {"db": "/b", "faiss": "/b.f", "id_map": "/b.m"},
                     "alpha": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"}},
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        assert s.delete_memory_lib("beta") is True
        assert s.get_memory_libs()["current"] == "alpha"

    def test_delete_non_current_memory_lib(self, tmp_path, monkeypatch):
        libs = {
            "current": "a",
            "libs": {"a": {"db": "/a", "faiss": "/a.f", "id_map": "/a.m"},
                     "b": {"db": "/b", "faiss": "/b.f", "id_map": "/b.m"}},
        }
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        monkeypatch.setattr(Settings, "_reset_memory_singletons", lambda self: None)
        assert s.delete_memory_lib("b") is True
        data = s.get_memory_libs()
        assert "b" not in data["libs"]
        assert data["current"] == "a"

    def test_memory_lib_event_count_missing_lib_and_db(self, tmp_path, monkeypatch):
        libs = {"current": "默认", "libs": {"默认": {"db": "/nope.db"}}}
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.memory_lib_event_count("ghost") == 0
        assert s.memory_lib_event_count("默认") == 0

    def test_memory_lib_event_count_real_db(self, tmp_path, monkeypatch):
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE events (id INTEGER)")
        conn.executemany("INSERT INTO events (id) VALUES (?)", [(1,), (2,), (3,)])
        conn.commit()
        conn.close()
        libs = {"current": "默认", "libs": {"默认": {"db": str(db)}}}
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.memory_lib_event_count("默认") == 3

    def test_memory_lib_event_count_error(self, tmp_path, monkeypatch):
        db = tmp_path / "bad.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()
        conn.close()
        libs = {"current": "默认", "libs": {"默认": {"db": str(db)}}}
        s = _new_settings(tmp_path, monkeypatch, memory_libs=libs)
        assert s.memory_lib_event_count("默认") == 0


# ---------------------------------------------------------------------------
# model_post_init 目录创建 + 模块级兜底
# ---------------------------------------------------------------------------

class TestModuleFallback:
    def test_model_post_init_creates_db_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "memory.db"
        monkeypatch.setattr(Settings, "_USER_CONFIG_PATH", tmp_path / "settings.json")
        monkeypatch.setattr(Settings, "_personas_yaml_path", tmp_path / "personas.yaml")
        monkeypatch.setattr(Settings, "_memory_libs_path", tmp_path / "memory_libs.json")
        Settings(_env_file=None, SQLITE_PATH=str(target))
        assert (tmp_path / "nested").is_dir()

    def test_model_post_init_skips_empty_db_dir(self, tmp_path, monkeypatch):
        s = _new_settings(tmp_path, monkeypatch, SQLITE_PATH="memory.db")
        assert s.SQLITE_PATH == "memory.db"

    def test_module_level_settings_instantiates(self):
        from config.settings import settings
        assert settings is not None


# ── 模型上下文长度（get_context_length） ──────────────────────────────────

def test_get_context_length_tier_defaults():
    """各层级上下文长度：0 → 全局 CONTEXT_WINDOW_SIZE"""
    from config.settings import settings as cfg
    base = cfg.CONTEXT_WINDOW_SIZE or 128000
    assert cfg.get_context_length("large") == base
    assert cfg.get_context_length("supervisor") == base
    assert cfg.get_context_length("expert") == base
    assert cfg.get_context_length("unknown") == base


def test_get_context_length_tier_specific():
    """配置了层级上下文长度时按对应层级取值"""
    from config.settings import settings as cfg
    try:
        old_l, old_m, old_s = cfg.LARGE_MODEL_CONTEXT_LENGTH, cfg.MEDIUM_MODEL_CONTEXT_LENGTH, cfg.SMALL_MODEL_CONTEXT_LENGTH
        cfg.LARGE_MODEL_CONTEXT_LENGTH = 131072
        cfg.MEDIUM_MODEL_CONTEXT_LENGTH = 65536
        cfg.SMALL_MODEL_CONTEXT_LENGTH = 32768
        assert cfg.get_context_length("large") == 131072
        assert cfg.get_context_length("supervisor") == 65536
        assert cfg.get_context_length("expert") == 32768
    finally:
        cfg.LARGE_MODEL_CONTEXT_LENGTH = old_l
        cfg.MEDIUM_MODEL_CONTEXT_LENGTH = old_m
        cfg.SMALL_MODEL_CONTEXT_LENGTH = old_s
