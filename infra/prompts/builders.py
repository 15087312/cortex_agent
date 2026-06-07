"""
动态提示词构建器 - 支持模板填充和动态拼接
"""
from typing import Dict, List, Optional, Any
from .constraints import AntiRepetitionConstraints
import html


def _sanitize_user_input(text: str) -> str:
    """SEC: 清理用户输入，防止 prompt 注入"""
    # 转义 XML/HTML 特殊字符
    text = html.escape(text, quote=True)
    # 替换中文方括号防止伪造段落标题
    text = text.replace("【", "[").replace("】", "]")
    # 用严格分隔符包裹，防止模型混淆系统指令和用户输入
    return f"=== USER INPUT START ===\n{text}\n=== USER INPUT END ==="


class BasePromptBuilder:
    """提示词构建器基类"""

    def __init__(self, template: str = ""):
        self.template = template
        self._fragments: List[tuple] = []
        self._variables: Dict[str, Any] = {}

    def add_section(self, title: str, content: str, priority: int = 0):
        """添加片段"""
        self._fragments.append((priority, title, content))
        return self

    def set_variable(self, key: str, value: Any):
        """设置变量"""
        self._variables[key] = value
        return self

    def build(self) -> str:
        """构建最终提示词"""
        parts = [self.template] if self.template else []

        self._fragments.sort(key=lambda x: x[0])
        for _, title, content in self._fragments:
            if content:
                parts.append(f"\n\n【{title}】\n{content}")

        return "".join(parts)


class LargeModelPromptBuilder(BasePromptBuilder):
    """主模型提示词构建器"""

    def __init__(self, template: str = ""):
        super().__init__(template)
        self._memory_context = ""
        self._supervisor_report = ""
        self._expert_results: List[str] = []
        self._user_input = ""

    def with_memory_context(self, context: str) -> "LargeModelPromptBuilder":
        """设置记忆上下文"""
        self._memory_context = context
        return self

    def with_supervisor_report(self, report: str) -> "LargeModelPromptBuilder":
        """设置主管汇报"""
        self._supervisor_report = report
        return self

    def with_expert_results(self, results: List[str]) -> "LargeModelPromptBuilder":
        """设置专家结果"""
        self._expert_results = results
        return self

    def with_user_input(self, user_input: str) -> "LargeModelPromptBuilder":
        """设置用户输入"""
        self._user_input = user_input
        return self

    def build(self) -> str:
        """构建主模型完整提示词"""
        parts = [self.template] if self.template else []

        if self._memory_context:
            parts.append(f"\n\n【近期对话上下文】\n{self._memory_context}")

        if self._supervisor_report:
            parts.append(f"\n\n【主管模型汇报】\n{self._supervisor_report}")

        if self._expert_results:
            parts.append(f"\n\n【专家执行结果】\n" + "\n---\n".join(self._expert_results))

        if self._user_input:
            parts.append(f"\n\n【用户最新输入】\n{_sanitize_user_input(self._user_input)}")

        parts.append("\n\n请基于以上信息，向用户提供自然的回复。")

        return "".join(parts)


class MediumModelPromptBuilder(BasePromptBuilder):
    """主管模型提示词构建器"""

    def __init__(self, template: str = ""):
        super().__init__(template)
        self._available_experts: List[str] = []
        self._task_history: List[str] = []
        self._user_input = ""

    def with_available_experts(self, experts: List[str]) -> "MediumModelPromptBuilder":
        """设置可用专家"""
        self._available_experts = experts
        return self

    def with_task_history(self, history: List[str]) -> "MediumModelPromptBuilder":
        """设置任务历史"""
        self._task_history = history
        return self

    def with_user_input(self, user_input: str) -> "MediumModelPromptBuilder":
        """设置用户输入"""
        self._user_input = user_input
        return self

    def build(self) -> str:
        """构建主管模型完整提示词"""
        parts = [self.template] if self.template else []

        if self._available_experts:
            parts.append(f"\n\n【可用专家列表】\n" + "\n".join(f"- {e}" for e in self._available_experts))

        if self._task_history:
            parts.append(f"\n\n【已完成任务】\n" + "\n".join(f"- {t}" for t in self._task_history[-5:]))

        if self._user_input:
            parts.append(f"\n\n【当前用户输入】\n{_sanitize_user_input(self._user_input)}")

        parts.append("\n\n请分析任务并决定是否需要调度专家。")
        parts.append("\n\n【输出要求】")
        parts.append("\n1. 直接输出分析结果，禁止使用'好的'、'我现在需要'等开场白")
        parts.append("\n2. 严格按照汇报格式输出，不要添加额外解释")

        return "".join(parts)


class SmallModelPromptBuilder(BasePromptBuilder):
    """专家小模型提示词构建器 - 防复读核心"""

    def __init__(self, template: str = "", constraints: Optional[AntiRepetitionConstraints] = None):
        super().__init__(template)
        self._task = ""
        self._task_notebook = ""
        self._short_term_memory: List[str] = []
        self._constraints: List[str] = []
        self._history_output = ""
        self._anti_repetition = constraints or AntiRepetitionConstraints()

    def with_task(self, task: str) -> "SmallModelPromptBuilder":
        """设置任务"""
        self._task = task
        return self

    def with_task_notebook(self, notebook: str) -> "SmallModelPromptBuilder":
        """设置任务记事本"""
        self._task_notebook = notebook
        return self

    def with_short_term_memory(self, memory: List[str]) -> "SmallModelPromptBuilder":
        """设置短期记忆"""
        self._short_term_memory = memory
        return self

    def with_constraints(self, constraints: List[str]) -> "SmallModelPromptBuilder":
        """设置额外约束"""
        self._constraints = constraints
        return self

    def with_history_output(self, history: str) -> "SmallModelPromptBuilder":
        """设置历史输出（用于去重）"""
        self._history_output = history
        return self

    def build(self) -> str:
        """构建专家小模型完整提示词"""
        parts = [self.template] if self.template else []

        parts.append(f"\n\n【当前任务】\n{self._task}")

        if self._task_notebook:
            parts.append(f"\n\n【任务进度记事本】\n{self._task_notebook}")

        if self._short_term_memory:
            mem_str = "\n".join(f"- {m[:100]}" for m in self._short_term_memory[-3:])
            parts.append(f"\n\n【短期记忆（最近思考，禁止复读）】\n{mem_str}")

        if self._history_output:
            parts.append(f"\n\n【历史输出（不得重复）】\n{self._history_output[-500:]}")

        all_constraints = self._constraints + [self._anti_repetition.generate_constraint_text()]
        if all_constraints:
            parts.append(f"\n\n【约束条件】\n" + "\n".join(f"- {c}" for c in all_constraints))

        parts.append("\n\n请开始执行任务，完成后使用 continue_thinking 工具结束循环并填写 result_summary")

        return "".join(parts)


class ExpertPromptBuilder(SmallModelPromptBuilder):
    """专家提示词构建器 - 用于 ContinuousThinker"""

    def __init__(self, template: str = "", constraints: Optional[AntiRepetitionConstraints] = None):
        super().__init__(template, constraints)
        self._instruction = ""
        self._context: Dict[str, Any] = {}

    def with_instruction(self, instruction: str) -> "ExpertPromptBuilder":
        """设置指令"""
        self._instruction = instruction
        return self

    def with_context(self, context: Dict[str, Any]) -> "ExpertPromptBuilder":
        """设置上下文"""
        self._context = context
        return self

    def build(self) -> str:
        """构建专家提示词（ContinuousThinker 专用）"""
        template = self.template or ""
        instruction = self._instruction
        notebook_status = self._context.get("notebook_status", "")
        recent_context = self._context.get("recent_context", "无近期上下文")
        available_tools = self._context.get(
            "available_tools",
            "1. 更新记事本\n2. 终止思考"
        )
        history_output = self._context.get("history_output", "")
        tier = self._context.get("tier", "")

        parts = [template]

        parts.append(f"\n\n【初始目标】\n{instruction}")
        parts.append(f"\n\n【可用工具与指令】\n{available_tools}")
        parts.append(f"\n\n【当前任务进度记事本】\n{notebook_status}")
        parts.append(f"\n\n【短期记忆（最近思考，禁止复读）】\n{recent_context}")

        # 中期记忆 (30分钟-7天，注意力评分排序)
        related_memories = self._context.get("related_memories", "")
        if related_memories and related_memories != "无相关记忆":
            parts.append(f"\n\n【相关历史记忆（中期）】\n{related_memories}")

        # 长期记忆参考
        long_term_reference = self._context.get("long_term_reference", "")
        if long_term_reference and long_term_reference != "无长期记忆参考":
            parts.append(f"\n\n【长期记忆参考】\n{long_term_reference}")

        if history_output:
            parts.append(f"\n\n【历史输出（不得重复）】\n{history_output}")

        # 根据 tier 自定义执行要求（技能激活时，由技能流程接管，此处只保留基础约束）
        has_skill = self._context.get("has_skill", False)
        parts.append("\n\n【执行要求】")
        if has_skill:
            parts.append("\n1. 严格遵循上方【技能规章】和【标准流程】执行任务，按步骤产出。")
            parts.append("\n2. 完成所有流程步骤后，使用 continue_thinking 工具返回 result_summary。")
            parts.append("\n3. 不要把内部控制协议写进自然语言回复。")
        elif tier == "supervisor":
            parts.append("\n1. 快速识别需要哪些专家来完成任务。")
            parts.append("\n2. 通过 delegate_task(role='...', task='...') 委托专家，参数名和role值必须正确。")
            parts.append("\n3. 如果delegate_task失败，检查role名称是否正确，查看可用角色列表('delegation')获取最新指导。")
            parts.append("\n4. 需要结束时使用 continue_thinking 工具返回 result_summary。")
        elif tier == "expert":
            parts.append("\n1. 只执行分配给你的那一件具体任务，不要扩展或建议其他方案。")
            parts.append("\n2. 达到完成标准后立即使用 continue_thinking 工具返回 result_summary。")
            parts.append("\n3. 不要把内部控制协议写进自然语言回复。")
        else:  # large tier
            parts.append("\n1. 理解需求，评估是否需要委托。")
            parts.append("\n2. 对简单问题立即回答，对复杂问题选择合适主管委托（如code_supervisor、query_supervisor）。")
            parts.append("\n3. 如果delegate_task失败，检查参数和role名称，查看可用角色列表('delegation')获取最新指导。")
            parts.append("\n4. 不要进行无意义的多轮分析，确定答案后立即停止。")

        parts.append("\n\n请开始执行：")

        return "".join(parts)
