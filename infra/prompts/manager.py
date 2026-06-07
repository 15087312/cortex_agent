"""
提示词管理器 - 统一管理所有模型的提示词
工业级设计：三层结构 + 动态拼接 + 防复读约束 + 价值观约束

三层架构：
├─ 1. 固定基础提示（系统身份/强约束/价值观）
├─ 2. 动态片段生成（外部传入，灵活拼接）
└─ 3. 统一构建接口（对外提供服务）
"""
from typing import Dict, Optional, List, Any
from pathlib import Path
from .registry import PromptRegistry, prompt_registry
from .builders import (
    LargeModelPromptBuilder,
    MediumModelPromptBuilder,
    SmallModelPromptBuilder,
    ExpertPromptBuilder
)
from .constraints import AntiRepetitionConstraints


_value_constraint_builder = None


def _get_value_constraint_builder():
    """获取价值观约束构建器"""
    global _value_constraint_builder
    if _value_constraint_builder is None:
        from modules.thinking.utils.value_constraint_builder import get_constraint_builder
        _value_constraint_builder = get_constraint_builder()
    return _value_constraint_builder


class PromptManager:
    """提示词管理器 - 单例模式"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not PromptManager._initialized:
            self._registry = prompt_registry
            self._base_prompts: Dict[str, str] = {}
            self._memory_context: List[str] = []
            self._constraints: List[str] = []
            self._role_overrides: Dict[str, str] = {}
            self._anti_repetition = AntiRepetitionConstraints()
            PromptManager._initialized = True
            self._load_templates()

    def _load_templates(self):
        """从文件加载模板"""
        templates_dir = Path(__file__).parent / "templates"
        if templates_dir.exists():
            self._registry.load_from_directory(str(templates_dir))

        self._base_prompts = {
            "large_model": self._registry.get("large_model") or self._get_large_model_default(),
            "medium_model": self._registry.get("medium_model") or self._get_medium_model_default(),
            "small_model": self._registry.get("small_model") or self._get_small_model_default(),
        }

    def _get_large_model_default(self) -> str:
        """主模型默认提示词"""
        return """你是本AI系统的【主模型】，是唯一与用户直接交互的出口。"""

    def _get_medium_model_default(self) -> str:
        """主管模型默认提示词"""
        return """你是系统内部的【主管模型】，不直接与用户对话，只负责任务分析、专家调度与进度汇报。"""

    def _get_small_model_default(self) -> str:
        """专家小模型默认提示词"""
        return """你是内部执行专家。请严格遵守以下所有规则：
1. 只输出核心结论，禁止解释、推理过程、客套话。
2. 严禁使用 Markdown、代码块、列表符号。
3. 严禁重复之前说过的内容。
4. 任务完成时，使用 continue_thinking 工具结束循环并填写 result_summary。"""

    def add_memory_context(self, context: str):
        self._memory_context.append(context)

    def set_memory_context(self, contexts: List[str]):
        self._memory_context = contexts

    def clear_memory_context(self):
        self._memory_context.clear()

    def add_constraint(self, constraint: str):
        self._constraints.append(constraint)

    def set_constraints(self, constraints: List[str]):
        self._constraints = constraints

    def clear_constraints(self):
        self._constraints.clear()

    def build_large_model_prompt(
        self,
        user_input: str,
        memory_context: Optional[str] = None,
        expert_results: Optional[List[str]] = None,
        supervisor_report: Optional[str] = None,
        include_values: bool = True
    ) -> str:
        builder = LargeModelPromptBuilder(self._base_prompts.get("large_model", ""))
        prompt = builder \
            .with_user_input(user_input) \
            .with_memory_context(memory_context or "") \
            .with_supervisor_report(supervisor_report or "") \
            .with_expert_results(expert_results or []) \
            .build()
        
        if include_values:
            constraint_builder = _get_value_constraint_builder()
            value_constraints = constraint_builder.build_full_constraint()
            prompt = prompt + "\n\n" + value_constraints
        
        return prompt

    def build_medium_model_prompt(
        self,
        user_input: str,
        available_experts: Optional[List[str]] = None,
        task_history: Optional[List[str]] = None
    ) -> str:
        builder = MediumModelPromptBuilder(self._base_prompts.get("medium_model", ""))
        return builder \
            .with_user_input(user_input) \
            .with_available_experts(available_experts or []) \
            .with_task_history(task_history or []) \
            .build()

    def build_small_model_prompt(
        self,
        task: str,
        task_notebook: Optional[str] = None,
        short_term_memory: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        history_output: Optional[str] = None
    ) -> str:
        builder = SmallModelPromptBuilder(
            self._base_prompts.get("small_model", ""),
            self._anti_repetition
        )
        return builder \
            .with_task(task) \
            .with_task_notebook(task_notebook or "") \
            .with_short_term_memory(short_term_memory or []) \
            .with_constraints(constraints or self._constraints) \
            .with_history_output(history_output or "") \
            .build()

    def build_expert_prompt(
        self,
        instruction: str,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        builder = ExpertPromptBuilder(
            self._base_prompts.get("small_model", ""),
            self._anti_repetition
        )
        return builder \
            .with_instruction(instruction) \
            .with_context(context or {}) \
            .build()

    def check_repetition(self, output: str) -> bool:
        return self._anti_repetition.check(output).passed

    def reload(self):
        self._registry.reload_all()
        self._load_templates()

    def get_tool_call_prompt(
        self,
        user_input: str,
        context: str = "",
        tool_list: List[str] = None
    ) -> str:
        """
        构建工具调用专用提示词
        强制模型输出JSON格式
        """
        tools_str = ", ".join(tool_list) if tool_list else "无可用工具"

        # SEC-15: Escape special delimiters to prevent prompt injection
        # Using XML-style tags to isolate user content from system instructions
        safe_user_input = user_input.replace("【", "[").replace("】", "]")
        safe_context = context.replace("【", "[").replace("】", "]") if context else "无"

        return f"""【上下文】
{safe_context}

【可用工具】
{tools_str}

【指令】
根据用户输入，判断是否需要调用工具。
- 需要调用：严格输出JSON格式，不许加任何文字、解释、注释
- 不需要调用：输出 {{"tool":"none","params":{{}}}}

【用户输入】
<user_content>
{safe_user_input}
</user_content>

【输出】（只许输出JSON）"""


prompt_manager = PromptManager()
