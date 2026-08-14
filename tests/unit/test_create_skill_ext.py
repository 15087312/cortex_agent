"""create_skill 工具测试 — 委托 SkillManager + 各错误分支覆盖"""
import asyncio
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.service_registry import get_capability, register_capability, unregister_capability
from infra.tool_manager.tools import create_skill


def _call(*args, **kw):
    return asyncio.run(create_skill.create_skill(*args, **kw))


@pytest.fixture
def skill_manager_cap():
    original = get_capability("skill_manager")
    yield
    if original is None:
        unregister_capability("skill_manager")
    else:
        register_capability("skill_manager", original)


def test_missing_skill_id():
    r = _call(skill_id="", name="X", description="d")
    assert r["status"] == "error"
    assert "skill_id" in r["message"]


def test_missing_name():
    r = _call(skill_id="s1", name="", description="d")
    assert r["status"] == "error"
    assert "skill_id" in r["message"]


def test_service_not_registered(skill_manager_cap):
    unregister_capability("skill_manager")
    r = _call(skill_id="s1", name="技能", description="d")
    assert r["status"] == "error"
    assert "未注册" in r["message"]


def test_success_with_path(skill_manager_cap):
    mgr = MagicMock()
    mgr.create_skill.return_value = (True, "skills/s1/SKILL.md")
    register_capability("skill_manager", lambda: mgr)
    r = _call(skill_id="s1", name="技能", description="描述", keywords=["kw"])
    assert r["status"] == "success"
    assert r["path"] == "skills/s1/SKILL.md"
    assert r["skill_id"] == "s1"
    mgr.create_skill.assert_called_once_with(
        skill_id="s1", name="技能", description="描述",
        keywords=["kw"], trigger=None, tool_rules=None,
    )


def test_success_default_path(skill_manager_cap):
    mgr = MagicMock()
    mgr.create_skill.return_value = (True, "")
    register_capability("skill_manager", lambda: mgr)
    r = _call(skill_id="s1", name="技能", description="", keywords=None)
    assert r["status"] == "success"
    assert r["path"] == "skills/s1/SKILL.md"


def test_manager_returns_failure(skill_manager_cap):
    mgr = MagicMock()
    mgr.create_skill.return_value = (False, "落盘失败")
    register_capability("skill_manager", lambda: mgr)
    r = _call(skill_id="s1", name="技能", description="d")
    assert r["status"] == "error"
    assert r["message"] == "落盘失败"


def test_exception_caught(skill_manager_cap):
    def boom(*a, **k):
        raise RuntimeError("boom")

    register_capability("skill_manager", boom)
    r = _call(skill_id="s1", name="技能", description="d")
    assert r["status"] == "error"
    assert "boom" in r["message"]
