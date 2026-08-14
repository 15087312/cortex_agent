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


# ── 防御分支：保存失败 / 解析兜底 / 相似规则去重 / 无变化不写盘 ─────────────

def test_save_failure_cleans_tmp_and_raises(tmp_path, monkeypatch):
    """save 写盘失败 → 清理临时文件并重抛（85-87）"""
    vs = _vs(tmp_path)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        vs.save("新内容")
    # 临时文件已清理
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_get_values_dict_no_sections(tmp_path):
    """内容无 section → 返回空 dict（108->111）"""
    vs = _vs(tmp_path)
    vs.save("只是一段普通文本")
    assert vs.get_values_dict() == {}


def test_add_rule_similar_existing_rejected(tmp_path):
    """与已有规则完全相同时拒绝新增（125-126）"""
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 始终对用户保持诚实\n")
    vs.add_rule("原则", "始终对用户保持诚实")
    content = vs.load()
    assert content.count("- 始终对用户保持诚实") == 1


def test_add_rule_section_not_found_appends_end(tmp_path):
    """目标 section 不存在 → 规则追加到文件末尾（132->142）"""
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 诚实\n")
    vs.add_rule("新章节", "始终对用户保持善良真诚")
    content = vs.load()
    assert content.rstrip().endswith("- 始终对用户保持善良真诚")


def test_add_rule_already_present_skips(tmp_path):
    """规则文本已存在（非 - 行）→ 不重复插入（143->exit）"""
    vs = _vs(tmp_path)
    vs.save("备注 - 始终对用户保持诚实\n")
    vs.add_rule("原则", "始终对用户保持诚实")
    content = vs.load()
    assert content.count("- 始终对用户保持诚实") == 1


def test_remove_rule_multiple_sections(tmp_path):
    """目标 section 后还有其它 section → 正确切换（160-161）"""
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 诚实\n[规则]\n- 简洁\n")
    vs.remove_rule("原则", "诚实")
    content = vs.load()
    assert "- 诚实" not in content
    assert "- 简洁" in content  # 后续 section 不受影响


def test_update_rule_no_match_no_save(tmp_path):
    """old_rule 不存在 → 内容不变，不写盘（174->exit）"""
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 诚实\n")
    vs.update_rule("原则", "不存在的规则", "替换")
    assert "- 诚实" in vs.load()


def test_cleanup_skips_empty_section(tmp_path):
    """某 section 全部无效 → 整体跳过（190）"""
    vs = _vs(tmp_path)
    vs.save("[原则]\n- 短\n[进化记录]\n- 旧记录\n")
    vs.cleanup()
    content = vs.load()
    assert "[原则]" not in content


def test_is_valid_rule_generic_regex(tmp_path):
    assert ValueSystem._is_valid_rule("当前回复无需修改任何内容") is False  # 泛化话术


def test_is_valid_rule_avoid_short(tmp_path):
    assert ValueSystem._is_valid_rule("避免:乱改系统配置") is False  # 避免: 开头但过短


def test_rules_too_similar_empty_sets():
    assert ValueSystem._rules_too_similar("", "x") is False
    assert ValueSystem._rules_too_similar("x", "") is False
