"""配置热重载测试：改模型配置/提示词实时生效，无需重启"""
import os
import time
from unittest.mock import MagicMock

import pytest

from config.settings import settings


# ── 模型配置指纹 ───────────────────────────────────────────────────────────

def test_fingerprint_changes_on_config(monkeypatch):
    from infra.model.config_fingerprint import model_config_fingerprint
    f1 = model_config_fingerprint("large")
    monkeypatch.setattr(settings, "LARGE_MODEL_API_URL", "http://changed/v1")
    assert model_config_fingerprint("large") != f1


def test_fingerprint_tracks_key_and_format(monkeypatch):
    from infra.model.config_fingerprint import model_config_fingerprint
    f1 = model_config_fingerprint("small")
    monkeypatch.setattr(settings, "SMALL_MODEL_API_KEY", "sk-changed")
    assert model_config_fingerprint("small") != f1


# ── ModelRunner.client 配置变更重建 ────────────────────────────────────────

def test_model_runner_client_rebuild_on_config_change(monkeypatch):
    from modules.thinking.chat_light.model_runner import ModelRunner

    created = []
    monkeypatch.setattr(
        "infra.model.large_model_client.LargeModelClient",
        lambda: MagicMock(),
    )
    r = ModelRunner()
    c1 = r.client
    assert r.client is c1  # 配置未变 → 复用缓存

    monkeypatch.setattr(settings, "LARGE_MODEL_API_URL", "http://new-url/v1")
    c2 = r.client
    assert c2 is not c1  # 配置变更 → 自动重建


def test_model_runner_client_reuse_unchanged(monkeypatch):
    from modules.thinking.chat_light.model_runner import ModelRunner

    monkeypatch.setattr(
        "infra.model.large_model_client.LargeModelClient",
        lambda: MagicMock(),
    )
    r = ModelRunner()
    c1 = r.client
    assert r.client is c1
    assert r.client is c1  # 连续复用


# ── ContextSlicer._get_client 配置变更重建 ─────────────────────────────────

def test_slicer_client_rebuild_on_config_change(monkeypatch):
    from modules.thinking.chat_light.context_slicer import ContextSlicer

    monkeypatch.setattr(
        "infra.model.large_model_client.LargeModelClient",
        lambda: MagicMock(),
    )
    s = ContextSlicer()
    c1 = s._get_client()
    assert s._get_client() is c1  # 复用

    monkeypatch.setattr(settings, "LARGE_MODEL_API_KEY", "sk-new-key")
    c2 = s._get_client()
    assert c2 is not c1  # 配置变更 → 重建


# ── PromptComposer base.yaml 热重载 ────────────────────────────────────────

def _clear_persona(monkeypatch):
    """清空设置页人设/系统覆盖，让 build_system 走 base.yaml identity 分支
    （pydantic Settings 禁止 setattr 方法，故整体替换模块引用）"""
    import importlib
    cs_mod = importlib.import_module("config.settings")
    from types import SimpleNamespace
    fake = SimpleNamespace(
        get_system_override=lambda *a, **k: "",
        get_persona=lambda *a, **k: "",
        get_custom_agents=lambda: [],
        ASSISTANT_NAME="助手",
        USER_NAME="用户",
    )
    monkeypatch.setattr(cs_mod, "settings", fake)


def test_prompt_composer_reload_on_file_change(monkeypatch, tmp_path):
    import modules.thinking.chat_light.prompt_composer as pc
    _clear_persona(monkeypatch)

    base = tmp_path / "base.yaml"
    base.write_text("identity: 旧身份提示词\n", encoding="utf-8")
    monkeypatch.setattr(pc, "PROMPTS_DIR", tmp_path)

    composer = pc.PromptComposer()
    assert "旧身份提示词" in composer.build_system()

    # 修改文件 + 强制更新 mtime（保证变化被检测到）
    base.write_text("identity: 新身份提示词\n", encoding="utf-8")
    now = time.time()
    os.utime(base, (now + 1, now + 1))

    assert "新身份提示词" in composer.build_system()  # 无需重建实例


def test_prompt_composer_unchanged_file_reuses(monkeypatch, tmp_path):
    import modules.thinking.chat_light.prompt_composer as pc
    _clear_persona(monkeypatch)

    base = tmp_path / "base.yaml"
    base.write_text("identity: 固定身份\n", encoding="utf-8")
    monkeypatch.setattr(pc, "PROMPTS_DIR", tmp_path)

    composer = pc.PromptComposer()
    assert composer._identity == "固定身份"
    # mtime 未变 → 不重读（identity 保持）
    composer.build_system()
    assert composer._identity == "固定身份"


# ── 纯对话人设：尊重编排的 active 状态 ─────────────────────────────────────

def _fake_settings(monkeypatch, custom_agents, agent_active, personas, override=""):
    import importlib
    from types import SimpleNamespace
    cs_mod = importlib.import_module("config.settings")
    fake = SimpleNamespace(
        get_system_override=lambda *a, **k: override,
        get_persona=lambda role: personas.get(role, ""),
        get_custom_agents=lambda: list(custom_agents),
        get_agent_active=lambda role: agent_active.get(role, True),
        ASSISTANT_NAME="助手",
        USER_NAME="用户",
    )
    monkeypatch.setattr(cs_mod, "settings", fake)


def _new_composer(monkeypatch, tmp_path, identity):
    import modules.thinking.chat_light.prompt_composer as pc
    base = tmp_path / "base.yaml"
    base.write_text(f"identity: {identity}\n", encoding="utf-8")
    monkeypatch.setattr(pc, "PROMPTS_DIR", tmp_path)
    return pc.PromptComposer()


def test_prompt_composer_respects_inactive_agent(monkeypatch, tmp_path):
    """编排里停用的 agent 人设不应被强制套用 → 回退内置 identity"""
    _fake_settings(monkeypatch,
        custom_agents=[{"tier": "large", "role": "123"}],
        agent_active={"123": False, "orchestrator": True},
        personas={"123": "停用的人设：芙宁娜", "orchestrator": ""})
    composer = _new_composer(monkeypatch, tmp_path, "内置总指挥身份")
    out = composer.build_system()
    assert "内置总指挥身份" in out
    assert "芙宁娜" not in out  # 停用的 123 人设未生效


def test_prompt_composer_uses_active_agent(monkeypatch, tmp_path):
    """激活的 large 自定义 agent 人设优先"""
    _fake_settings(monkeypatch,
        custom_agents=[{"tier": "large", "role": "123"}],
        agent_active={"123": True, "orchestrator": False},
        personas={"123": "激活的人设：代码指挥"})
    composer = _new_composer(monkeypatch, tmp_path, "内置总指挥身份")
    out = composer.build_system()
    assert "代码指挥" in out
    assert "内置总指挥身份" not in out


def test_prompt_composer_override_still_highest(monkeypatch, tmp_path):
    """高级修改（系统覆盖）仍为最高优先级，覆盖激活人设"""
    _fake_settings(monkeypatch,
        custom_agents=[{"tier": "large", "role": "123"}],
        agent_active={"123": True},
        personas={"123": "激活的人设"},
        override="高级修改的系统提示词")
    composer = _new_composer(monkeypatch, tmp_path, "内置总指挥身份")
    assert "高级修改的系统提示词" in composer.build_system()
