"""skill_manager 补测：加载去重 / 空名称匹配 / CRUD 边界 / 删除异常等防御分支"""
import pytest

import modules.thinking.skills.manager as smod
from modules.thinking.skills.manager import SkillManager
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


def _skill_obj(**kw):
    base = dict(
        id="sk", name="Review", description="代码审查代码检查工具",
        keywords=["审查", "review"], trigger={"include": ["检查代码"], "min_score": 2},
        enabled=True,
    )
    base.update(kw)
    return Skill(**base)


# ── 加载：顶层 SKILL.md 跳过 / 去重 / yaml 异常 ───────────────────────

def test_load_skills_skips_top_level_skill_md(tmp_path):
    (tmp_path / "SKILL.md").write_text("顶层说明书", encoding="utf-8")
    _write_skill(tmp_path, "nested", name="嵌套")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 1
    assert m.get_skill("nested") is not None


def test_load_skills_dedup_skill_md(tmp_path):
    _write_skill(tmp_path, "a", name="A", id="dup")
    _write_skill(tmp_path, "b", name="B", id="dup")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 1  # 同 id 只保留一个


def test_load_skills_dedup_yaml(tmp_path):
    _write_yaml(tmp_path, "x", id="same")
    _write_yaml(tmp_path, "y", id="same")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 1


def test_load_skills_bad_yaml_skipped(tmp_path):
    (tmp_path / "bad.yaml").write_text("key: [unclosed", encoding="utf-8")
    _write_yaml(tmp_path, "good", name="好")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 1


def test_load_skills_yaml_non_dict(tmp_path):
    (tmp_path / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
    m = _make_manager(tmp_path)
    assert m.load_skills(str(tmp_path)) == 0


def test_list_skills_auto_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "auto", name="自动")
    m = _make_manager(tmp_path)
    assert len(m.list_skills()) == 1


def test_search_skills_auto_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "auto", name="自动", description="数据库优化")
    m = _make_manager(tmp_path)
    assert len(m.search_skills("数据库")) == 1


# ── 匹配：空名称 / 空描述 / 描述多词 ──────────────────────────────────

def test_match_skill_empty_name_desc():
    m = SkillManager()
    s = _skill_obj(trigger={}, name="", description="")
    m._skills = {s.id: s}
    m._loaded = True
    m.list_skills_for_role = lambda role="": list(m._skills.values())
    assert m.match_skill("检查代码") is None


def test_match_skill_description_multi_word():
    m = SkillManager()
    s = _skill_obj(trigger={}, name="", description="query optimize database tune")
    m._skills = {s.id: s}
    m._loaded = True
    m.list_skills_for_role = lambda role="": list(m._skills.values())
    assert m.match_skill("please query and optimize my database") is s


# ── create_skill 边界 ──────────────────────────────────────────────────

def test_create_skill_duplicate_id(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    m = _make_manager(tmp_path)
    m._skills = {"exists": Skill(id="exists", name="已存在")}
    m._loaded = True
    ok, err = m.create_skill("exists", "x")
    assert ok is False and "已存在" in err


def test_create_skill_dir_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    d = tmp_path / "ondisk"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("已存在", encoding="utf-8")
    m = _make_manager(tmp_path)
    ok, err = m.create_skill("ondisk", "x")
    assert ok is False and "目录已存在" in err


def test_create_skill_with_tool_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    m = _make_manager(tmp_path)
    ok, err = m.create_skill("tooled", "带规则", tool_rules={"block_tags": ["dangerous"]})
    assert ok is True and err == ""
    text = (tmp_path / "tooled" / "SKILL.md").read_text(encoding="utf-8")
    assert "dangerous" in text


def test_create_skill_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    orig_write = smod.Path.write_text

    def boom(self, *a, **k):
        if "wonwrite" in str(self):
            raise OSError("readonly fs")
        return orig_write(self, *a, **k)

    monkeypatch.setattr(smod.Path, "write_text", boom)
    m = _make_manager(tmp_path)
    ok, err = m.create_skill("wonwrite", "x")
    assert ok is False and "写入失败" in err


# ── update_skill 边界 ──────────────────────────────────────────────────

def test_update_skill_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    m = _make_manager(tmp_path)
    m._skills = {"gone": Skill(id="gone", name="G", source="skill_md",
                               path=str(tmp_path / "gone" / "SKILL.md"))}
    m._loaded = True
    ok, err = m.update_skill("gone", name="新")
    assert ok is False and "源文件不存在" in err


def test_update_skill_no_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    d = tmp_path / "plain"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("纯文本正文没有 front matter", encoding="utf-8")
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    ok, err = m.update_skill("plain", name="改名")
    assert ok is True and err == ""
    assert m.get_skill("plain").name == "改名"


def test_update_skill_incomplete_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    d = tmp_path / "half"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nkey: v", encoding="utf-8")
    m = _make_manager(tmp_path)
    m._skills = {"half": Skill(id="half", name="H", source="skill_md",
                               path=str(d / "SKILL.md"))}
    m._loaded = True
    ok, err = m.update_skill("half", name="新")
    assert ok is True and err == ""


def test_update_skill_bad_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    d = tmp_path / "badfm"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nkey: [unclosed\n\nbody\n---", encoding="utf-8")
    m = _make_manager(tmp_path)
    m._skills = {"badfm": Skill(id="badfm", name="B", source="skill_md",
                                path=str(d / "SKILL.md"))}
    m._loaded = True
    ok, err = m.update_skill("badfm", name="新")  # front 解析失败 → 空 front 继续
    assert ok is True and err == ""


def test_update_skill_tool_rules_set(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "tr", name="规则")
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))
    ok, err = m.update_skill("tr", tool_rules={"allow_tools": ["web_search"]})
    assert ok is True and err == ""
    assert m.get_skill("tr").tool_rules == {"allow_tools": ["web_search"]}


def test_update_skill_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    _write_skill(tmp_path, "nowrite", name="只读")
    m = _make_manager(tmp_path)
    m.load_skills(str(tmp_path))

    orig_write = smod.Path.write_text

    def boom(self, *a, **k):
        if "nowrite" in str(self):
            raise OSError("readonly fs")
        return orig_write(self, *a, **k)

    monkeypatch.setattr(smod.Path, "write_text", boom)
    ok, err = m.update_skill("nowrite", name="改")
    assert ok is False and "写入失败" in err


# ── delete_skill 异常路径 ──────────────────────────────────────────────

def test_delete_skill_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    m = _make_manager(tmp_path)
    m._skills = {"ghost": Skill(id="ghost", name="G", source="skill_md",
                                path=str(tmp_path / "ghost" / "SKILL.md"))}
    m._loaded = True
    ok, err = m.delete_skill("ghost")  # 文件不存在 → 跳过 unlink，rmdir OSError 静默
    assert ok is True and err == ""


def test_delete_skill_unlink_error(tmp_path, monkeypatch):
    monkeypatch.setattr(smod, "_SKILLS_DIR", tmp_path)
    d = tmp_path / "dirskill"
    d.mkdir(parents=True)
    m = _make_manager(tmp_path)
    m._skills = {"dirskill": Skill(id="dirskill", name="D", source="skill_md", path=str(d))}
    m._loaded = True
    ok, err = m.delete_skill("dirskill")  # unlink 目录 → IsADirectoryError → 删除失败
    assert ok is False and "删除失败" in err
