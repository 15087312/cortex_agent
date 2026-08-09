"""ValueFormatter 测试（此前 0% 覆盖）：价值观上下文构建"""
from modules.thinking.value_formatter import ValueFormatter


class _FakeValueSystem:
    """ValueSystem 接口实现替身（真实方法，ValueSystem 未在代码库实现）"""

    def __init__(self, sections=None, valid=True):
        self._sections = sections or {"核心准则": ["诚实", "负责"], "进化记录": ["旧"]}
        self._valid = valid
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        return "诚实、负责"

    def get_values_dict(self):
        return self._sections

    def _is_valid_rule(self, rule):
        return self._valid


def _fmt(values=None, valid=True):
    vs = _FakeValueSystem(sections=values, valid=valid)
    return ValueFormatter(vs), vs


def test_build_context():
    fmt, vs = _fmt()
    out = fmt.build_context()
    assert "AI 核心价值观" in out
    assert "诚实" in out
    assert vs.load_calls == 1


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
