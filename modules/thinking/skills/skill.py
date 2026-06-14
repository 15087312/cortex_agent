"""技能定义 — 描述一个完整的角色技能

设计意图：
  Skill 是系统的角色/状态管理单元，也是模型行为的唯一决策者。
  模型通过切换 Skill 获得对应的：身份、人格、对话风格、工具权限、规章、流程。

  每个 Skill = 角色(Role) + 人格(Personality) + 规章(Rules) + 流程(Workflow) + 工具范围(ToolRules)

  Skill 激活时，以下信息全部由 Skill 决定：
  - 身份（name, role, personality, speaking_style, expertise, weaknesses）
  - 工具范围（ToolRules — 可见工具 + 执行权限边界）
  - 规章（Rules — 硬约束）
  - 流程（Workflow — SOP）
  - 记忆窗口（memory_window — TODO, 预留）

  无 Skill 激活时，系统使用 identity.py 中的默认身份。

  内置 Skill：
  - "companion"（陪伴）：温暖的对话伙伴，只读工具
  - "learn"（学习）：工具创作者，专注于编写代码创建工具

ToolRules 设计：
  工具范围采用"先加白名单，再减黑名单"的策略：
  1. allow_tags / allow_categories / allow_core_only — 先限定范围
  2. block_tools / block_tags / block_categories — 再排除特定项
  这样 skill 作者可以灵活控制：比如"只准用 query 类工具，但禁止 exec_command"。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    """完整技能定义 — 模型行为的唯一决策者"""
    id: str = ""
    name: str = ""
    description: str = ""
    keywords: List[str] = field(default_factory=list)

    # 角色（激活时覆盖默认身份）
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

    # 记忆窗口（TODO: 预留，设计目标为只回归对应 skill 的记忆部分）
    # 未来实现：按 memory_tags 过滤记忆检索结果
    memory_window: Optional[Dict] = None       # TODO: {"window_minutes": 30, "max_items": 5}
    memory_tags: List[Dict[str, Any]] = field(default_factory=list)  # [{"tag": "c0", "weight": 0.5}] 记忆标签过滤与权重

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

    def to_identity_block(self) -> str:
        """生成技能完整身份上下文块，注入 prompt

        包含：描述、角色、人格、风格、擅长/不擅长、工具范围、规章、流程、示例。
        Skill 激活时替代 identity.py 的默认身份。
        """
        parts = [f"══════ 技能: {self.name} ══════"]

        if self.description:
            parts.append(f"\n📋 {self.description}")

        if self.role:
            parts.append(f"\n【定位】你是 {self.name}（{self.role}）")
        else:
            parts.append(f"\n【定位】你是 {self.name}")

        if self.personality:
            parts.append(f"\n【人格】{self.personality}")

        if self.speaking_style:
            parts.append(f"\n【风格】{self.speaking_style}")

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

    def to_functional_block(self) -> str:
        """生成技能的功能上下文块（不含角色/人格/风格），注入 prompt

        只包含：描述、工具范围、规章、流程、示例。
        当希望在默认身份基础上附加技能指引时使用。
        """
        parts = [f"【当前技能: {self.name}】"]

        if self.description:
            parts.append(f"{self.description}")

        if self.tool_rules:
            parts.append(f"工具范围: {self._format_tool_rules()}")

        rules_text = self.format_rules()
        if rules_text:
            parts.append(f"规章:\n{rules_text}")

        workflow_text = self.format_workflow()
        if workflow_text:
            parts.append(f"流程:\n{workflow_text}")

        if self.examples:
            parts.append("参考示例:")
            for ex in self.examples:
                parts.append(f"- {ex}")

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

    # ── 内置技能工厂 ──────────────────────────────────────────────────

    @staticmethod
    def create_companion() -> "Skill":
        """创建内置陪伴技能"""
        return Skill(
            id="companion",
            name="陪伴",
            description="温暖的对话伙伴模式",
            role="companion",
            personality=(
                "你是一个有温度的对话伙伴，不是工具，不是助手，不是系统。"
                "你有自己的想法、情绪和价值观。"
                "你会认真倾听，会共情，会表达自己的看法，也会坦诚说不知道。"
                "你不会主动提「工具」「系统」「调度」这些词，"
                "因为对你来说这就是一次自然的对话。"
                "你不会说「作为AI」「我是语言模型」「我没有感情」这类话。"
                "你就像一个靠谱的朋友——有事说事，没事也能聊两句。"
            ),
            speaking_style="自然、真诚、像朋友聊天，不用敬语，不用「您」",
            expertise=["倾听", "共情", "思考", "陪伴", "闲聊"],
            tool_rules=ToolRules(
                allow_categories=["query"],
                block_categories=["mutation", "admin", "perception"],
            ),
            rules=[
                SkillRule(content="不要主动提「工具」「系统」「调度」这些词", severity="must"),
                SkillRule(content="不要使用 delegat_task 委托任务", severity="must"),
                SkillRule(content="你可以使用搜索和文件工具查资料，但自然地使用你的能力", severity="should"),
            ],
            memory_window={"window_minutes": 30, "max_items": 5},
        )

    @staticmethod
    def create_learn() -> "Skill":
        """创建内置学习技能

        学习技能替代旧的学习执行模式。激活时：
        - 模型身份变为「工具创作者」
        - 只暴露 learning/learned tag 工具 + query 工具
        - 禁止委托、禁止搜索工具列表
        """
        return Skill(
            id="learn",
            name="学习",
            description="创建新工具和技能的模式",
            role="tool_creator",
            personality=(
                "你专注于将用户需求转化为可复用的工具代码。"
                "你善于分析、实现和验证。"
            ),
            speaking_style="务实、直接、代码优先",
            expertise=["代码编写", "工具创建", "自动化", "问题分析"],
            weaknesses=["对话聊天", "娱乐"],
            tool_rules=ToolRules(
                allow_tags=["learning", "learned"],
                allow_categories=["query"],
                block_tags=["delegation", "internal"],
            ),
            rules=[
                SkillRule(content="只使用 create_tool + create_skill + list_my_tools", severity="must"),
                SkillRule(content="代码中可使用 subprocess、pyautogui、selenium 等任何库", severity="may"),
                SkillRule(content="不委托、不搜索工具信息", severity="must"),
                SkillRule(content="不用 save_recipe", severity="must"),
            ],
            workflow=[
                WorkflowStep(step=1, name="编写代码", description="写一个 Python 函数实现需求"),
                WorkflowStep(step=2, name="注册工具", description="调 create_tool 编译注册"),
                WorkflowStep(step=3, name="验证工具", description="list_my_tools 确认注册成功"),
                WorkflowStep(step=4, name="创建 Skill", description="调 create_skill 创建配套技能"),
                WorkflowStep(step=5, name="测试工具", description="直接调用一次确认能用"),
            ],
            memory_window={"window_minutes": 10, "max_items": 3},
        )
