"""ValueFormatter 测试（此前 0% 覆盖）：价值观上下文构建"""
from unittest.mock import MagicMock

from modules.thinking.value_formatter import ValueFormatter


def _fmt(values=None):
    vs = MagicMock()
    vs.load.return_value = "诚实、负责"
    vs.get_values_dict.return_value = values or {"核心准则": ["诚实", "负责"], "进化记录": ["旧"]}
    vs._is_valid_rule.return_value = True
    return ValueFormatter(vs), vs


def test_build_context():
    fmt, vs = _fmt()
    out = fmt.build_context()
    assert "AI 核心价值观" in out
    assert "诚实" in out
    vs.load.assert_called_once()


def test_build_compact_context_skips_evolution():
    fmt, _ = _fmt()
    out = fmt.build_compact_context()
    assert "[核心准则]" in out
    assert "进化记录" not in out
    assert "诚实" in out


def test_get_active_rules_empty():
    fmt, _ = _fmt({"进化记录": ["只此一条"]})
    out = fmt.get_active_rules()
    assert out == ""  # 无有效准则
