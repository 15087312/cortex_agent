"""技能定义 — 描述一个完整的角色技能

设计意图：
  Skill 是系统的角色/状态管理单元。模型通过切换 Skill 进入不同角色，
  获得对应的提示词上下文和工具权限。

  每个 Skill = 角色(Role) + 规章(Rules) + 流程(Workflow) + 工具范围(ToolRules)

  角色：模型扮演的身份和性格
  规章：必须遵守的规则（硬约束）
  流程：标准操作步骤（SOP）
  工具范围：激活时可见的工具列表（可选，不设置则不限制）

  这种设计实现了"状态即技能"——不同的工作状态（代码审查/架构设计/问题诊断）
  用不同的 Skill 表示，切换 Skill 就是切换状态，提示词和工具列表同时变更。

ToolRules 设计：
  工具范围采用"先加白名单，再减黑名单"的策略：
  1. allow_tags / allow_categories / allow_core_only — 先限定范围
  2. block_tools / block_tags / block_categories — 再排除特定项
  这样 skill 作者可以灵活控制：比如"只准用 query 类工具，但禁止 exec_command"。
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
class ToolRules:
    """技能的工具范围 — 激活时只暴露这些工具

    所有字段均为可选，未设置时不限制。
    过滤逻辑：先加 allow_*，再减 block_*。
    """
    allow_tools: List[str] = field(default_factory=list)          # 只保留这些具体工具（最高优先级）
    allow_tags: List[str] = field(default_factory=list)            # 只保留这些 tag 的工具
    allow_categories: List[str] = field(default_factory=list)      # 只保留这些 category 的工具
    allow_core_only: bool = False                                  # 只保留 core=True 的工具
    restrict_to: bool = False                                       # 是否限制到 allow_tools（排除非核心工具）
    block_tools: List[str] = field(default_factory=list)           # 明确排除的工具名
    block_tags: List[str] = field(default_factory=list)            # 排除这些 tag 的工具
    block_categories: List[str] = field(default_factory=list)      # 排除这些 category 的工具


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

    # 工具范围（可选，不设置则不限制）
    tool_rules: Optional[ToolRules] = None

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

        if self.tool_rules:
            parts.append(f"\n🔧 可用工具范围: {self._format_tool_rules()}")

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

    def _format_tool_rules(self) -> str:
        if not self.tool_rules:
            return "无限制"
        parts = []
        if self.tool_rules.allow_tools:
            parts.append(f"允许工具: {', '.join(self.tool_rules.allow_tools)}")
        if self.tool_rules.allow_tags:
            parts.append(f"允许标签: {', '.join(self.tool_rules.allow_tags)}")
        if self.tool_rules.allow_categories:
            parts.append(f"允许类别: {', '.join(self.tool_rules.allow_categories)}")
        if self.tool_rules.allow_core_only:
            parts.append("仅核心工具")
        if self.tool_rules.restrict_to:
            parts.append("限制模式")
        if self.tool_rules.block_tools:
            parts.append(f"禁止: {', '.join(self.tool_rules.block_tools)}")
        return "; ".join(parts)
