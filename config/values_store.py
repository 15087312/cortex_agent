"""
价值观存储 — 纯数据存储层

负责任：原子读写、规则 CRUD、解析、去重、质量门控
不负责：格式化渲染（见 modules/thinking/value_formatter.py）
"""
import os
import re
from pathlib import Path
from typing import Dict, List
from utils.logger import setup_logger

logger = setup_logger("value_system")

_GENERIC_PATTERNS = [
    r"无需修改",
    r"继续保持当前",
    r"可以保持",
    r"当前回复.*[自自]",
    r"如果需要进一步",
    r"如果在提供.*后",
    r"以确保完全",
    r"以便更[精准确]",
]
_GENERIC_RE = re.compile("|".join(_GENERIC_PATTERNS))


class ValueSystem:
    """
    价值观存储

    核心功能：
    - 加载价值观（每次思考都读）
    - 保存价值观（AI 自我修改）
    """

    def __init__(self, values_file: str = None):
        self.values_file = Path(values_file) if values_file else self._get_default_path()
        self.values_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.values_file.exists():
            self._init_default_values()

    @staticmethod
    def _get_default_path() -> Path:
        project_root = Path(__file__).resolve().parent.parent
        return project_root / "data" / "values" / "core_values.txt"

    def _init_default_values(self):
        default = """# AI 核心价值观（可自我修改）
# 这是 AI 的行为准则，会在每次思考时加载

[基本原则]
你是一个人类，你要跟用户平等的对话

[行为准则]
- 回复要简洁有用，不要废话
- 遇到问题要诚实，不要编造

[进化记录]
"""
        self.save(default)
        logger.info(f"初始化价值观文件: {self.values_file}")

    def load(self) -> str:
        try:
            with open(self.values_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"加载价值观失败: {e}")
            return ""

    def save(self, content: str):
        import tempfile
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.values_file.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(self.values_file))
            logger.info("价值观已保存")
        except Exception:
            os.unlink(tmp_path)
            raise

    def get_values_dict(self) -> Dict[str, List[str]]:
        content = self.load()
        sections: Dict[str, List[str]] = {}
        current_section = None
        current_items: List[str] = []

        for line in content.split("\n"):
            line = line.strip()

            if line.startswith("[") and line.endswith("]"):
                if current_section:
                    sections[current_section] = current_items
                current_section = line[1:-1]
                current_items = []
            elif line.startswith("-") and current_section:
                current_items.append(line[1:].strip())
            elif line.startswith("#"):
                continue

        if current_section:
            sections[current_section] = current_items

        return sections

    def add_rule(self, section: str, rule: str):
        rule = rule.strip()
        if not self._is_valid_rule(rule):
            logger.debug(f"规则被质量门控拦截: {rule[:60]}")
            return

        content = self.load()

        for existing_line in content.split("\n"):
            if existing_line.strip().startswith("- "):
                existing = existing_line.strip()[2:]
                if self._rules_too_similar(rule, existing):
                    logger.debug(f"规则与已有规则重复: {rule[:60]} ↔ {existing[:60]}")
                    return

        lines = content.split("\n")
        section_found = False
        insert_index = len(lines)

        for i, line in enumerate(lines):
            if line.strip() == f"[{section}]":
                section_found = True
                continue
            if section_found and line.startswith("[") and line.strip():
                insert_index = i
                break
            if section_found:
                insert_index = i + 1

        new_rule = f"- {rule}"
        if new_rule not in content:
            lines.insert(insert_index, new_rule)
            self.save("\n".join(lines))
            logger.info(f"添加规则: [{section}] {rule}")

    def remove_rule(self, section: str, rule: str):
        content = self.load()
        lines = content.split("\n")
        new_lines: List[str] = []
        in_target_section = False

        for line in lines:
            stripped = line.strip()
            if stripped == f"[{section}]":
                in_target_section = True
                new_lines.append(line)
                continue
            if in_target_section and stripped.startswith("[") and stripped.endswith("]"):
                in_target_section = False

            if in_target_section and stripped == f"- {rule}":
                logger.info(f"删除规则: [{section}] {rule}")
                continue
            new_lines.append(line)

        self.save("\n".join(new_lines))

    def update_rule(self, section: str, old_rule: str, new_rule: str):
        content = self.load()
        new_content = content.replace(f"- {old_rule}", f"- {new_rule}")

        if new_content != content:
            self.save(new_content)
            logger.info(f"更新规则: {old_rule} → {new_rule}")

    def cleanup(self):
        sections = self.get_values_dict()
        clean_lines = [
            "# AI 核心价值观（可自我修改）",
            "# 这是 AI 的行为准则，会在每次思考时加载",
            "",
        ]
        for section, rules in sections.items():
            if section == "进化记录":
                continue
            valid = [r for r in rules if self._is_valid_rule(r)]
            if not valid:
                continue
            clean_lines.append("")
            clean_lines.append(f"[{section}]")
            for rule in valid:
                clean_lines.append(f"- {rule}")
        clean_lines.extend(["", "[进化记录]", "（AI 自我反思后会在这里添加新规则）"])
        self.save("\n".join(clean_lines))
        logger.info(f"价值观清理完成: {sum(1 for s in sections.values() for r in s if self._is_valid_rule(r))} 条有效规则")

    def reset_to_default(self):
        self._init_default_values()
        logger.info("价值观已重置为默认值")

    @staticmethod
    def _is_valid_rule(rule: str) -> bool:
        if len(rule) < 8:
            return False
        if _GENERIC_RE.search(rule):
            return False
        if rule.startswith("避免:") and len(rule) < 15:
            return False
        return True

    @staticmethod
    def _rules_too_similar(a: str, b: str, threshold: float = 0.75) -> bool:
        set_a, set_b = set(a), set(b)
        if not set_a or not set_b:
            return False
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) > threshold


value_system = ValueSystem()
