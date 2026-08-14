"""skill_manager 测试：加载 / 匹配 / CRUD / per-role 可见性"""
import pytest

from modules.thinking.skills.manager import SkillManager, _get_skills_dir
from modules.thinking.skills.skill import Skill


def _write_skill(tmp_path, skill_id, **front):
    d = tmp_path / skill_id
    d.mkdir(parents=True, exist_ok=True)
    fm = {
        "name": front.pop("name", skill_id),
        "keywords": front.pop("keywords", []),
        "trigger": front.pop("trigger", {}),
        "enabled": front.pop("enabled", True),
    }
    fm.update(front)
    import yaml
    body = front.pop("description", "这是说明书正文")
    text = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n\n" + body
    (d / "SKILL.md").write_text(text, encoding="utf-8")


def _write_yaml(tmp_path, skill_id, **front):
    import yaml
    fm = {"id": skill_id, "name": front.pop("name", skill_id)}
    fm.update(front)
    (tmp_path / f"{skill_id}.yaml").write_text(
        yaml.safe_dump(fm, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _make_manager(tmp_path):
    m = SkillManager()
    m._skills = {}
    m._loaded = False
    return m


# ── 加载 ───────────────────────────────────────────────────────────────

def test_load_skills_md_and_yaml(tmp_path):
    _write_skill(tmp_path, "code_review", name="代码审查", description="审查代码说明书")
    _write_yaml(tmp_path, "legacy_skill", description="旧格式")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 2
    assert m.get_skill("code_review").name == "代码审查"
    assert m.get_skill("legacy_skill").source == "yaml"


def test_load_skills_missing_dir(tmp_path):
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path / "不存在")) == 0


def test_load_skills_md_without_frontmatter(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "SKILL.md").write_text("只有正文没有 front matter", encoding="utf-8")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 1
    assert m.get_skill("plain").description == "只有正文没有 front matter"


def test_load_skills_bad_file_skipped(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nkey: [unclosed\n---", encoding="utf-8")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 0


def test_reload(tmp_path, monkeypatch):
    import modules.thinking.skills.manager as smod
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "a", name="A")
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    assert m.reload() == 1


def test_get_skill_auto_loads(tmp_path, monkeypatch):
    import modules.thinking.skills.manager as smod
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "b", name="B")
    m = _make_manager(tmp_path)
    skill = m.get_skill("b")  # 未显式加载，自动加载
    assert skill is not None


def test_list_skills_and_search(tmp_path):
    _write_skill(tmp_path, "alpha", name="Alpha", description="数据库优化技能", keywords=["数据库"])
    _write_skill(tmp_path, "beta", name="Beta", keywords=["网络"])
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    assert len(m.list_skills()) == 2
    assert any(s.id == "alpha" for s in m.search_skills("数据库"))
    assert len(m.search_skills("数据库")) == 1
    assert len(m.search_skills("")) == 2
    assert len(m.search_skills("网络")) == 1


# ── 匹配 ───────────────────────────────────────────────────────────────

def _skill_obj(**kw):
    base = dict(
        id="sk", name="Review", description="代码审查代码检查工具",
        keywords=["审查", "review"], trigger={"include": ["检查代码"], "min_score": 2},
        enabled=True,
    )
    base.update(kw)
    return Skill(**base)


def test_match_skill_by_include(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    s = _skill_obj()
    m._skills = {s.id: s}
    m._loaded = True
    monkeypatch.setattr(m, "list_skills_for_role", lambda role="": list(m._skills.values()))
    assert m.match_skill("请检查代码质量") == s
    assert m.match_skill("不相关的输入") is None
    assert m.match_skill("") is None


def test_match_skill_exclude_wins(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    s = _skill_obj(trigger={"include": ["检查代码"], "exclude": ["跳过"], "min_score": 2})
    m._skills = {s.id: s}
    m._loaded = True
    monkeypatch.setattr(m, "list_skills_for_role", lambda role="": list(m._skills.values()))
    assert m.match_skill("跳过这个检查代码的请求") is None


def test_match_skill_disabled_and_score():
    m = SkillManager()
    s = _skill_obj(enabled=False)
    m._skills = {s.id: s}
    m._loaded = True
    m.list_skills_for_role = lambda role="": list(m._skills.values())
    assert m.match_skill("检查代码") is None


def test_match_skill_min_score_not_met():
    m = SkillManager()
    s = _skill_obj(trigger={"include": ["检查代码"], "min_score": 10})
    m._skills = {s.id: s}
    m._loaded = True
    m.list_skills_for_role = lambda role="": list(m._skills.values())
    assert m.match_skill("检查代码") is None


def test_match_skill_by_name_keyword_desc():
    m = SkillManager()
    s = _skill_obj(trigger={})
    m._skills = {s.id: s}
    m._loaded = True
    m.list_skills_for_role = lambda role="": list(m._skills.values())
    # 名称 "Review" 命中
    assert m.match_skill("我要 Review") == s


# ── per-role 可见性 ────────────────────────────────────────────────────

def test_list_skills_for_role(monkeypatch, tmp_path):
    from modules.thinking.skills.manager import SkillManager as SM
    from modules.thinking.skills.skill import Skill
    from config.settings import Settings
    m = _make_manager(tmp_path)
    s1 = Skill(id="s1", name="S1", enabled=True)
    s2 = Skill(id="s2", name="S2", enabled=True)
    m._skills = {"s1": s1, "s2": s2}
    m._loaded = True
    monkeypatch.setattr(Settings, "get_role_skills", lambda self, role: ["s1"])
    assert [s.id for s in m.list_skills_for_role("role_x")] == ["s1"]
    monkeypatch.setattr(Settings, "get_role_skills", lambda self, role: ["*"])
    assert len(m.list_skills_for_role("role_x")) == 2
    assert len(m.list_skills_for_role("")) == 2


def test_list_skills_for_role_settings_error(tmp_path, monkeypatch):
    from modules.thinking.skills.skill import Skill
    from config.settings import Settings
    m = _make_manager(tmp_path)
    s1 = Skill(id="s1", name="S1", enabled=True)
    m._skills = {"s1": s1}
    m._loaded = True
    monkeypatch.setattr(Settings, "get_role_skills", lambda self, role: (_ for _ in ()).throw(RuntimeError("no")))
    assert [s.id for s in m.list_skills_for_role("r")] == ["s1"]


# ── CRUD ───────────────────────────────────────────────────────────────

def test_create_skill(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.thinking.skills.manager._SKILLS_DIR", tmp_path)
    m = _make_manager(tmp_path)
    ok, err = m.create_skill("new_skill", "新技能", description="说明", keywords=["k"], trigger={"include": ["x"]})
    assert ok is True and err == ""
    # id 校验
    ok, err = m.create_skill("Bad ID!", "x")
    assert ok is False and "仅允许" in err
    # 名称必填
    ok, err = m.create_skill("valid_id", "   ")
    assert ok is False and "名称" in err
    # 目录已存在
    ok, err = m.create_skill("existing", "x")
    assert ok is True


def test_update_skill(tmp_path, monkeypatch):
    import modules.thinking.skills.manager as smod
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "upd", name="旧名", keywords=["a"])
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    ok, err = m.update_skill("upd", name="新名", description="新正文", keywords=["b"], enabled=False, trigger={"include": ["t"]})
    assert ok is True
    assert m.get_skill("upd").name == "新名"
    # 不存在
    ok, err = m.update_skill("none", name="x")
    assert ok is False
    # tool_rules 清空
    ok, _ = m.update_skill("upd", tool_rules="")
    assert ok is True


def test_set_enabled(tmp_path, monkeypatch):
    import modules.thinking.skills.manager as smod
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "sw", name="开关")
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    assert m.set_enabled("sw", False)[0] is True
    assert m.get_skill("sw").enabled is False


def test_delete_skill(tmp_path, monkeypatch):
    import modules.thinking.skills.manager as smod
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "delme", name="可删")
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    ok, err = m.delete_skill("delme")
    assert ok is True and err == ""
    # 不存在
    ok, err = m.delete_skill("none")
    assert ok is False
    # yaml 保护
    _write_yaml(tmp_path, "protected", name="P")
    m.load_skills(str(tmp_path))
    ok, err = m.delete_skill("protected")
    assert ok is False and "受保护" in err
    # builtin 保护
    from modules.thinking.skills.skill import Skill
    builtin = Skill(id="built", name="B", metadata={"type": "builtin"}, source="skill_md", path=str(tmp_path / "b" / "SKILL.md"))
    m._skills["built"] = builtin
    ok, err = m.delete_skill("built")
    assert ok is False and "内置" in err


def test_to_listing(tmp_path):
    _write_skill(tmp_path, "listing", name="列")
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    listing = m.to_listing()
    assert any(s["id"] == "listing" for s in listing)


# ── 加载 helper ────────────────────────────────────────────────────────

def test_load_yaml_requires_pyyaml(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    import sys
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert m._load_yaml(tmp_path / "x.yaml") is None
    assert m._load_skill_md(tmp_path / "x" / "SKILL.md") is None


def test_load_yaml_invalid(tmp_path):
    import yaml
    (tmp_path / "bad.yaml").write_text("not: [valid: yaml", encoding="utf-8")
    m = _make_manager(tmp_path)
    with pytest.raises(yaml.YAMLError):
        m._load_yaml(tmp_path / "bad.yaml")


def test_load_skill_md_invalid_frontmatter(tmp_path):
    d = tmp_path / "badmd"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nkey: value\n", encoding="utf-8")  # 只有开头无闭合
    m = _make_manager(tmp_path)
    assert m._load_skill_md(d / "SKILL.md") is None


def test_load_skill_md_non_dict_front(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "SKILL.md").write_text("---\n- just\n- a\n- list\n---\nbody", encoding="utf-8")
    m = _make_manager(tmp_path)
    assert m._load_skill_md(d / "SKILL.md") is None


def test_skill_prompt_blocks():
    s = Skill(id="s", name="名", description="说明正文", keywords=["k"])
    block = s.to_prompt_block()
    assert "技能: 名" in block and "说明正文" in block and "技能结束" in block
    sugg = s.to_suggestion_block()
    assert "可激活技能: 名" in sugg
    assert "说明正文" in sugg


def test_skills_dir_resolve():
    assert _get_skills_dir().name == "skills"
