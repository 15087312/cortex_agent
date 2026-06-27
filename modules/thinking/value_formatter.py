"""
价值观格式化 — 纯渲染层

不负责存储，只负责从 ValueSystem 读取并格式化输出
"""
from typing import Dict, List


class ValueFormatter:
    """价值观渲染"""

    def __init__(self, value_system):
        self._vs = value_system

    def build_context(self) -> str:
        content = self._vs.load()
        return f"""
{'='*50}
[AI 核心价值观]（这是你的行为准则，必须遵守）
{'='*50}
{content}
{'='*50}
"""

    def build_compact_context(self) -> str:
        sections = self._vs.get_values_dict()
        lines = ["【价值观准则】"]
        for section, rules in sections.items():
            if section == "进化记录":
                continue
            if rules:
                lines.append(f"[{section}]")
                for rule in rules[:5]:
                    lines.append(f"  • {rule}")
        return "\n".join(lines)

    def get_active_rules(self, max_per_section: int = 8) -> str:
        sections = self._vs.get_values_dict()
        lines = ["【行为准则参考】"]
        for section, rules in sections.items():
            if section in ("进化记录",):
                continue
            valid = [r for r in rules if self._vs._is_valid_rule(r)]
            if not valid:
                continue
            lines.append(f"[{section}]")
            for rule in valid[:max_per_section]:
                lines.append(f"  • {rule}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def build_sections(self) -> str:
        sections = self._vs.get_values_dict()
        if not sections:
            return "（暂无规则）"
        lines = ["【价值观规则分类】"]
        for section, rules in sections.items():
            if section == "进化记录":
                continue
            if rules:
                lines.append(f"\n[{section}]")
                for rule in rules:
                    lines.append(f"  • {rule}")
        return "\n".join(lines)
