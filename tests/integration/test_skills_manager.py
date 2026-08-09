"""技能管理器测试（CRUD、启用禁用、角色白名单）"""
import pytest

import modules.thinking.skills.manager as mgr
from modules.thinking.skills.manager import SkillManager


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    d.mkdir()
    monkeypatch.setattr(mgr, "_get_skills_dir", lambda: d)
    return d


def test_create_update_delete(skills_dir):
    m = SkillManager()
    ok, msg = m.create_skill("my_skill", "测试技能", "这是说明", keywords=["测试"])
    assert ok, msg
    assert (skills_dir / "my_skill" / "SKILL.md").exists()
    m.reload()
    s = m.get_skill("my_skill")
    assert s is not None and s.name == "测试技能"
    ok, _ = m.update_skill("my_skill", description="新说明")
    assert ok
    m.reload()
    assert m.get_skill("my_skill").description == "新说明"
    ok, _ = m.delete_skill("my_skill")
    assert ok
    assert m.get_skill("my_skill") is None


def test_builtin_protected(skills_dir):
    m = SkillManager()
    m.create_skill("builtin_x", "内置", "x")
    skill = m.get_skill("builtin_x")
    skill.metadata["type"] = "builtin"
    ok, msg = m.delete_skill("builtin_x")
    assert not ok  # 内置不可删
    assert "内置" in msg


def test_enabled_filter(skills_dir, monkeypatch):
    m = SkillManager()
    m.create_skill("a", "A", "描述a")
    m.create_skill("b", "B", "描述b")
    from config.settings import settings
    # 用真实 set_role_skills 写入角色白名单
    settings.set_role_skills("code_writer", ["a"])
    try:
        visible = m.list_skills_for_role("code_writer")
        assert [s.id for s in visible] == ["a"]
        assert len(m.list_skills_for_role("")) == 2
        m.set_enabled("b", False)
        assert len(m.list_skills_for_role("")) == 1
    finally:
        settings.set_role_skills("code_writer", [])
