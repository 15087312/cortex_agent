"""技能定义 — 描述一个完整的角色技能

技能 = 角色 + 规章 + 流程

每个技能定义了：
- 角色：模型扮演的身份和性格
- 规章：必须遵守的规则（硬约束）
- 流程：标准操作步骤（SOP）
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class SkillRule:
    """技能规章 — 一条硬约束"""
    id: str = ""
    content: str = ""
    severity: str = "must"  # must | should | may


@dataclass
class WorkflowStep:
    """流程步骤 — SOP 中的一个步骤"""
    step: int = 0
    name: str = ""
    description: str = ""
    output: str = ""  # 期望产出


@dataclass
class Skill:
    """完整技能定义"""
    id: str = ""
    name: str = ""
    description: str = ""
    keywords: List[str] = field(default_factory=list)

    # 角色
    role: str = ""
    personality: str = ""
    speaking_style: str = ""
    expertise: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)

    # 规章
    rules: List[SkillRule] = field(default_factory=list)

    # 流程
    workflow: List[WorkflowStep] = field(default_factory=list)

    # 元数据
    examples: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def format_rules(self) -> str:
        """格式化规章为 prompt 文本"""
        if not self.rules:
            return ""
        lines = []
        for i, rule in enumerate(self.rules, 1):
            severity_icon = {"must": "🔴", "should": "🟡", "may": "🟢"}.get(
                rule.severity, "⚪"
            )
            lines.append(f"  {severity_icon} {i}. {rule.content}")
        return "\n".join(lines)

    def format_workflow(self) -> str:
        """格式化流程为 prompt 文本"""
        if not self.workflow:
            return ""
        lines = []
        for step in self.workflow:
            line = f"  第{step.step}步【{step.name}】{step.description}"
            if step.output:
                line += f"\n    → 产出: {step.output}"
            lines.append(line)
        return "\n".join(lines)

    def to_context_block(self) -> str:
        """生成完整的技能上下文块，注入 prompt"""
        parts = [f"══════ 技能: {self.name} ══════"]

        if self.description:
            parts.append(f"\n📋 {self.description}")

        if self.role:
            parts.append(f"\n🎭 角色: {self.role}")

        if self.personality:
            parts.append(f"\n🧠 人设: {self.personality}")

        if self.speaking_style:
            parts.append(f"\n💬 说话风格: {self.speaking_style}")

        if self.expertise:
            parts.append(f"\n✅ 擅长: {', '.join(self.expertise)}")

        if self.weaknesses:
            parts.append(f"\n❌ 不擅长: {', '.join(self.weaknesses)}")

        rules_text = self.format_rules()
        if rules_text:
            parts.append(f"\n📜 规章（必须遵守）:\n{rules_text}")

        workflow_text = self.format_workflow()
        if workflow_text:
            parts.append(f"\n🔄 标准流程:\n{workflow_text}")

        if self.examples:
            parts.append("\n💡 参考示例:")
            for ex in self.examples:
                parts.append(f"  - {ex}")

        parts.append(f"\n══════ 技能结束 ══════")
        return "\n".join(parts)
