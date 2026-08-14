"""value_tools 测试（此前 9% 覆盖）：价值观修改/查看全操作"""
import pytest

from config.values_store import ValueSystem
from infra.tool_manager.tools import value_tools


@pytest.fixture
def vs(tmp_path, monkeypatch):
    from config.values_store import value_system
    from modules.thinking.value_formatter import ValueFormatter
    from infra.tool_manager.service_registry import register_capability, unregister_capability
    tmp_vs = ValueSystem(values_file=str(tmp_path / "values.txt"))
    monkeypatch.setattr(value_tools, "_get_value_system", lambda: tmp_vs)
    # formatter 用同一实例（经能力端口注入，工具层不再直接 import modules）
    register_capability("value_formatter", lambda: ValueFormatter(tmp_vs))
    yield tmp_vs
    register_capability("value_formatter", lambda: ValueFormatter(value_system))


def _add(vs, section="行为准则", rule="始终对用户保持诚实并如实说明限制"):
    return value_tools.modify_value_system("add_rule", section=section, rule=rule)


def test_add_rule_success(vs):
    r = _add(vs)
    assert "已成功添加" in r
    assert "始终对用户保持诚实" in r


def test_add_rule_missing_params(vs):
    assert "需要 section 和 rule" in value_tools.modify_value_system("add_rule", section="行为准则")
    assert "需要 section 和 rule" in value_tools.modify_value_system("add_rule", rule="x")


def test_add_rule_quality_gate(vs):
    r = value_tools.modify_value_system("add_rule", section="行为准则", rule="短")
    assert "质量门控" in r


def test_add_rule_dedupe(vs):
    _add(vs)
    r = _add(vs)
    assert "过于相似" in r


def test_remove_rule(vs):
    _add(vs)
    r = value_tools.modify_value_system("remove_rule", section="行为准则", rule="始终对用户保持诚实并如实说明限制")
    assert "已成功从" in r


def test_update_rule(vs):
    _add(vs)
    r = value_tools.modify_value_system(
        "update_rule", section="行为准则",
        rule="始终对用户保持诚实并如实说明限制", new_rule="始终对用户保持诚实并如实说明限制和风险",
    )
    assert "已成功更新" in r


def test_cleanup_reset(vs):
    assert "已清理" in value_tools.modify_value_system("cleanup")
    assert "已重置" in value_tools.modify_value_system("reset")


def test_unknown_action(vs):
    assert "未知操作" in value_tools.modify_value_system("bogus")


def test_get_current_values_full(vs):
    out = value_tools.get_current_values("full")
    assert "完整价值观文本" in out


def test_get_current_values_compact(vs):
    _add(vs)
    out = value_tools.get_current_values("compact")
    assert "始终对用户保持诚实" in out


def test_get_current_values_sections(vs):
    _add(vs)
    out = value_tools.get_current_values("sections")
    assert "行为准则" in out


def test_get_current_values_unknown_format(vs):
    assert "未知格式" in value_tools.get_current_values("xml")
