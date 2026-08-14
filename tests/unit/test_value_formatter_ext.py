"""value_formatter / perception_source 补充测试"""
from unittest.mock import MagicMock

from modules.thinking.value_formatter import ValueFormatter
from modules.thinking.context.sources.perception_source import PerceptionSource


class _VS:
    def __init__(self, sections=None, valid=True):
        self._sections = sections if sections is not None else {"核心准则": ["诚实"], "进化记录": ["旧"]}
        self._valid = valid
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        return "诚实、负责"

    def get_values_dict(self):
        return self._sections

    def _is_valid_rule(self, rule):
        return self._valid


def test_get_active_rules_with_valid():
    vs = _VS({"核心准则": ["诚实", "负责"], "进化记录": ["旧"]}, valid=True)
    fmt = ValueFormatter(vs)
    out = fmt.get_active_rules(max_per_section=8)
    assert "[核心准则]" in out
    assert "诚实" in out
    assert "进化记录" not in out


def test_get_active_rules_all_invalid():
    vs = _VS({"核心准则": ["诚实", "负责"]}, valid=False)
    fmt = ValueFormatter(vs)
    assert fmt.get_active_rules() == ""


def test_build_sections_normal():
    vs = _VS({"核心准则": ["诚实"], "进化记录": ["旧"]})
    fmt = ValueFormatter(vs)
    out = fmt.build_sections()
    assert "核心准则" in out
    assert "进化记录" not in out
    assert "诚实" in out


def test_build_sections_empty():
    vs = _VS({})
    fmt = ValueFormatter(vs)
    assert fmt.build_sections() == "（暂无规则）"


def test_build_compact_empty_section():
    vs = _VS({"空段": [], "核心准则": ["诚实"]})
    fmt = ValueFormatter(vs)
    out = fmt.build_compact_context()
    assert "[核心准则]" in out


# ── perception_source ──────────────────────────────────────────────────

async def test_perception_source_collect(monkeypatch):
    integrator = MagicMock()
    frag = MagicMock()
    integrator.pool.snapshot = MagicMock(return_value=frag)
    monkeypatch.setattr("modules.perception.integration.get_perception_integrator", lambda: integrator)
    ps = PerceptionSource()
    out = await ps.collect()
    assert out is frag
