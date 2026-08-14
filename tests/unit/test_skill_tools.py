"""skill_tools 测试：能力降级 / 自动加载 / 查询成功 / 不存在 / 异常"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

import infra.tool_manager.tools.skill_tools as st
from infra.tool_manager.service_registry import (
    get_capability,
    register_capability,
    unregister_capability,
)


def _skill(**kw):
    base = dict(
        id="sk-1", name="测试技能", description="说明书正文", keywords=["k1", "k2"],
        trigger={"include": ["触发"]}, tool_rules={"allow_tools": []},
        enabled=True, source="skill_md", metadata={"m": 1},
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def manager(monkeypatch):
    original = get_capability("skill_manager")
    mgr = MagicMock()
    mgr._loaded = True
    register_capability("skill_manager", lambda: mgr)
    yield mgr
    if original is None:
        unregister_capability("skill_manager")
    else:
        register_capability("skill_manager", original)


def test_get_manager_returns_manager_when_loaded(manager):
    assert st._get_manager() is manager


def test_get_manager_loads_when_not_loaded(manager):
    manager._loaded = False
    assert st._get_manager() is manager
    manager.load_skills.assert_called_once()


def test_get_manager_none_when_capability_missing(monkeypatch):
    original = get_capability("skill_manager")
    unregister_capability("skill_manager")
    try:
        assert st._get_manager() is None
    finally:
        if original is None:
            unregister_capability("skill_manager")
        else:
            register_capability("skill_manager", original)


def test_get_skill_detail_success(manager):
    manager.get_skill.return_value = _skill()
    out = st.get_skill_detail("sk-1")
    assert out["success"] is True
    s = out["skill"]
    assert s["id"] == "sk-1"
    assert s["name"] == "测试技能"
    assert s["description"] == "说明书正文"
    assert s["keywords"] == ["k1", "k2"]
    assert s["trigger"] == {"include": ["触发"]}
    assert s["tool_rules"] == {"allow_tools": []}
    assert s["enabled"] is True
    assert s["source"] == "skill_md"
    assert s["metadata"] == {"m": 1}
    manager.get_skill.assert_called_once_with("sk-1")


def test_get_skill_detail_keywords_none(manager):
    manager.get_skill.return_value = _skill(keywords=None)
    out = st.get_skill_detail("sk-1")
    assert out["skill"]["keywords"] == []


def test_get_skill_detail_not_found(manager):
    manager.get_skill.return_value = None
    out = st.get_skill_detail("ghost")
    assert out["error"] == "技能不存在: ghost"


def test_get_skill_detail_service_missing(monkeypatch):
    original = get_capability("skill_manager")
    unregister_capability("skill_manager")
    try:
        out = st.get_skill_detail("sk-1")
        assert out["error"] == "技能服务未注册"
    finally:
        if original is None:
            unregister_capability("skill_manager")
        else:
            register_capability("skill_manager", original)


def test_get_skill_detail_exception(manager):
    manager.get_skill.side_effect = RuntimeError("boom")
    out = st.get_skill_detail("sk-1")
    assert out["error"] == "boom"
