"""ValueSystem 真实实现测试（此前 0% 覆盖，test_value_formatter 一直用替身）"""
import pytest

from config.values_store import ValueSystem


def _vs(tmp_path):
    return ValueSystem(values_file=str(tmp_path / "values.txt"))


# ── 初始化 / 读写 ───────────────────────────────────────────────────────────

def test_init_creates_default(tmp_path):
    vs = _vs(tmp_path)
    content = vs.load()
    assert "AI 核心价值观" in content
    assert "[行为准则]" in content


def test_save_and_load_roundtrip(tmp_path):
    vs = _vs(tmp_path)
    vs.save("自定义内容")
    assert vs.load() == "自定义内容"


def test_load_missing_returns_empty():
    vs = ValueSystem.__new__(ValueSystem)
    vs.values_file = __import__("pathlib").Path("/不存在/x.txt")
    assert vs.load() == ""


# ── get_values_dict 解析 ────────────────────────────────────────────────────

def test_get_values_dict_parses_sections(tmp_path):
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 诚实\n- 负责\n[规则]\n- 简洁\n")
    d = vs.get_values_dict()
    assert d["原则"] == ["诚实", "负责"]
    assert d["规则"] == ["简洁"]


def test_get_values_dict_skips_comments(tmp_path):
    vs = _vs(tmp_path)
    vs.save("# 注释\n[原则]\n- 诚实\n")
    d = vs.get_values_dict()
    assert "诚实" in d["原则"]


# ── add_rule / remove_rule / update_rule ────────────────────────────────────

def test_add_rule(tmp_path):
    vs = _vs(tmp_path)
    vs.add_rule("行为准则", "遇到问题要主动承认")
    content = vs.load()
    assert "- 遇到问题要主动承认" in content


def test_add_rule_quality_gate_short(tmp_path):
    vs = _vs(tmp_path)
    vs.add_rule("行为准则", "短")  # 太短，被门控
    assert "- 短" not in vs.load()


def test_add_rule_dedupe(tmp_path):
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 诚实\n")
    vs.add_rule("原则", "诚实")  # 相似，不重复加
    content = vs.load()
    assert content.count("- 诚实") == 1


def test_remove_rule(tmp_path):
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 诚实\n- 负责\n")
    vs.remove_rule("原则", "诚实")
    content = vs.load()
    assert "- 诚实" not in content
    assert "- 负责" in content


def test_update_rule(tmp_path):
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 诚实\n")
    vs.update_rule("原则", "诚实", "保持诚实")
    assert "- 保持诚实" in vs.load()


# ── cleanup / reset ─────────────────────────────────────────────────────────

def test_cleanup_removes_invalid(tmp_path):
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 始终对用户保持诚实\n- 太短\n[进化记录]\n- 旧记录\n")
    vs.cleanup()
    content = vs.load()
    assert "始终对用户保持诚实" in content
    assert "太短" not in content
    assert "旧记录" not in content  # 进化记录被保留段


def test_reset_to_default(tmp_path):
    vs = _vs(tmp_path)
    vs.save("自定义")
    vs.reset_to_default()
    assert "AI 核心价值观" in vs.load()


# ── 质量门控 ────────────────────────────────────────────────────────────────

def test_is_valid_rule():
    assert ValueSystem._is_valid_rule("这是一条足够长的有效规则") is True
    assert ValueSystem._is_valid_rule("短") is False


def test_rules_too_similar():
    assert ValueSystem._rules_too_similar("始终对用户保持诚实", "始终对用户保持诚实") is True
    assert ValueSystem._rules_too_similar("始终对用户保持诚实", "完全不同的规则内容") is False
