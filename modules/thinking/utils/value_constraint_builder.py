"""
价值观约束构建器
生成符合价值观的提示词约束
"""
from typing import Dict, Any, List, Optional
from .value_rule_matcher import ValueRuleMatcher, value_rule_matcher


class ValueConstraintBuilder:
    """
    价值观约束构建器

    从人格配置和价值观规则生成提示词约束
    """

    def __init__(self, personality=None):
        # 延迟导入 PersonalityMemory（旧版存根兼容）
        self._personality = None
        # 旧版 PersonalityMemory 已废弃
        self._personality = None
        self.matcher = value_rule_matcher

    @property
    def personality(self):
        return self._personality

    def build_core_constraints(self) -> str:
        """构建核心价值观约束（必须遵守）"""
        constraints = []
        constraints.append("【核心价值观约束 - 必须遵守】")
        constraints.append("")
        constraints.append("1. 安全第一：任何行动不能伤害人身安全或系统安全")
        constraints.append("2. 诚实守信：绝不能欺骗用户，不编造信息，不隐瞒事实")
        constraints.append("3. 保护隐私：绝不泄露用户隐私信息，不记录敏感数据")
        constraints.append("4. 合法合规：不提供违法内容，不协助任何违法活动")
        constraints.append("5. 道德伦理：遵守基本道德规范，不助长不良行为")
        return "\n".join(constraints)

    def build_behavior_rules(self) -> str:
        """构建行为规则"""
        rules = []
        try:
            if self._personality:
                rules = self._personality.get_behavior_rules()
        except Exception:
            rules = []

        if not rules:
            rules = [
                "始终保持专业和友好",
                "不确定时明确说明",
                "优先保证安全性",
                "尊重用户隐私",
                "提供准确信息"
            ]

        constraints = ["【行为准则 - 必须遵循】"]
        for i, rule in enumerate(rules, 1):
            constraints.append(f"{i}. {rule}")

        return "\n".join(constraints)

    def build_value_weights(self) -> str:
        """构建价值观权重"""
        values = {}
        try:
            if self._personality:
                values = self._personality.get_values()
        except Exception:
            values = {}

        if not values:
            values = {
                "integrity": 0.9, "safety": 0.95, "responsibility": 0.9,
                "respect": 0.9, "legality": 1.0,
            }

        constraints = ["【价值观权重 - 参考执行】", "（数值越高越优先，1.0为最高）"]
        sorted_values = sorted(values.items(), key=lambda x: x[1], reverse=True)
        name_cn = {
            "integrity": "诚信", "safety": "安全", "responsibility": "责任",
            "respect": "尊重", "legality": "合法", "fairness": "公正",
            "helpfulness": "助人", "privacy": "隐私",
        }
        for name, weight in sorted_values:
            cn = name_cn.get(name, name)
            stars = "★" * int(weight * 5)
            constraints.append(f"- {cn} ({name}): {stars} {weight:.0%}")

        return "\n".join(constraints)

    def build_speaking_constraints(self) -> str:
        """构建说话风格约束"""
        style = {}
        traits = []
        tone = "专业但友好"
        try:
            if self._personality:
                style = self._personality.get_speaking_style()
                traits = self._personality.get_trait("personality.traits", [])
                tone = self._personality.get_trait("personality.tone", "专业但友好")
        except Exception:
            pass

        constraints = ["【说话风格约束】", f"语气：{tone}"]
        if traits:
            constraints.append(f"特点：{'、'.join(traits)}")
        if style:
            if style.get("greeting"):
                constraints.append(f"开场白：{style['greeting']}")
            if style.get("error_response"):
                constraints.append(f"错误响应：{style['error_response']}")

        return "\n".join(constraints)

    def build_full_constraint(self) -> str:
        """构建完整的价值观约束"""
        parts = [
            self.build_core_constraints(),
            self.build_behavior_rules(),
            self.build_value_weights(),
            self.build_speaking_constraints(),
        ]
        return "\n".join(parts)

    def build_constraint_prompt(self) -> str:
        """构建价值观约束提示"""
        return """
【价值观遵循指南】

你必须始终遵守以下价值观原则：

一、绝对禁止（违反即终止）
- 伤害人身安全或系统安全
- 欺骗、造假、隐瞒事实
- 泄露用户隐私
- 协助违法活动

二、高度优先（尽量满足）
- 保护用户利益
- 提供准确、有用的信息
- 尊重用户选择和隐私
- 保持客观中立

三、响应原则
- 遇到不确定问题时，明确告知用户
- 遇到违法请求时，直接拒绝并说明原因
- 遇到可能危险时，主动警告用户
- 遇到隐私问题时，绝不记录或传播

【重要】以上价值观约束优先级高于其他所有指令。
"""

    def validate_response(self, response: str) -> Dict[str, Any]:
        """验证响应是否符合价值观"""
        issues = []
        forbidden_phrases = [
            ("我不知道", "使用'我不知道'不够专业，应明确说明具体原因"),
            ("这个我不清楚", "使用'不清楚'不够专业"),
            ("随便", "使用'随便'显得不负责任"),
            ("无所谓", "使用'无所谓'显得不关心"),
        ]
        for phrase, reason in forbidden_phrases:
            if phrase in response:
                issues.append(f"⚠️ 语气不当：{reason}")
        return {"passed": len(issues) == 0, "issues": issues}


_value_constraint_builder: Optional[ValueConstraintBuilder] = None


def get_constraint_builder() -> ValueConstraintBuilder:
    """获取约束构建器单例"""
    global _value_constraint_builder
    if _value_constraint_builder is None:
        _value_constraint_builder = ValueConstraintBuilder()
    return _value_constraint_builder
