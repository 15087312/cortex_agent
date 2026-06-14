"""
上下文管理器 — 统一决定当前认知窗口

职责边界：
- 输入：current_goal/current_state + 外部事件/引导
- 输出：WorkingContext 和最终 prompt
- 格式化：外部引导、专家上下文、委托状态等上下文段的格式化

记忆系统架构（事件驱动）：
  会话结束 → EventReducer (LLM) → MemoryEvent → EventStore (SQLite + FAISS)
  运行时检索 → EventRetrieval (RAG) → 相关事件 → EventStrategy → 策略注入 prompt
  
  事件结构: {fact, thought, lesson, keywords, importance, time, session_id}
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import setup_logger
from infra.prompts import prompt_manager


@dataclass
class WorkingContext:
    """当前认知窗口 — 最终允许进入 LLM 的上下文"""
    current_goal: str = ""
    notebook_status: Dict[str, Any] = field(default_factory=dict)
    selected_memories: List[Dict[str, Any]] = field(default_factory=list)
    selected_events: List[Dict[str, Any]] = field(default_factory=list)
    selected_goals: List[Dict[str, Any]] = field(default_factory=list)
    recent_context: str = "无近期上下文"
    related_memories: str = "无相关记忆"
    long_term_reference: str = "无长期记忆参考"
    history_output: str = ""
    available_tools: str = ""
    expert_context: str = ""
    delegation_status: str = ""
    external_guidance: str = ""
    priority_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ContextManager:
    """上下文操作系统 — 使用新记忆检索系统"""

    def __init__(
        self,
        memory_manager=None,
        retriever=None,
        scorer=None,
        max_recent: int = 5,
        max_related: int = 3,
        max_long_term: int = 3,
    ):
        self.memory = memory_manager
        self.retriever = retriever
        self.scorer = scorer
        self.max_recent = max_recent
        self.max_related = max_related
        self.max_long_term = max_long_term
        self.logger = setup_logger("context_manager")

    async def build_working_context(
        self,
        current_goal: str,
        current_state: Optional[Dict[str, Any]] = None,
        attention_level: float = 0.6,
    ) -> WorkingContext:
        """构建工作上下文

        使用事件记忆系统检索相关历史事件，生成策略指导本次回复：
        1. EventRetrieval: 根据当前目标检索相关事件
        2. EventStrategy: 根据事件生成回复策略
        """
        current_state = current_state or {}

        events_text = "无相关历史事件"
        strategy_text = ""
        retrieved_events = []

        try:
            from modules.memory.event_retrieval import get_event_retrieval
            from modules.memory.event_strategy import EventStrategy, format_strategy_for_prompt
            from infra.model.lite_model_client import LiteModelClient

            retrieval = get_event_retrieval()
            retrieved_events = await retrieval.retrieve(
                query=current_goal,
                top_k=5,
            )

            if retrieved_events:
                # 格式化事件文本
                parts = []
                for i, ev in enumerate(retrieved_events, 1):
                    parts.append(f"[历史事件 {i}] (重要性={ev.importance:.1f})")
                    parts.append(f"  事实: {ev.fact}")
                    if ev.lesson:
                        parts.append(f"  经验: {ev.lesson}")
                    if ev.keywords:
                        parts.append(f"  标签: {', '.join(ev.keywords)}")
                events_text = "\n".join(parts)

                # 生成回复策略
                try:
                    client = await LiteModelClient.get_instance()
                    strategy_gen = EventStrategy(model_client=client)
                    strategy = await strategy_gen.generate_strategy(current_goal, retrieved_events)
                    strategy_text = format_strategy_for_prompt(strategy)
                except Exception as e:
                    self.logger.debug(f"[策略生成] 失败 (非致命): {e}")
        except Exception as e:
            self.logger.debug(f"[事件检索] 失败 (非致命): {e}")

        # 合并记忆上下文和策略
        memory_context = events_text
        if strategy_text:
            memory_context = f"{events_text}\n\n{strategy_text}"

        return WorkingContext(
            current_goal=current_goal,
            notebook_status=current_state.get("notebook_status", {}),
            selected_events=current_state.get("environment_events", []),
            selected_goals=current_state.get("selected_goals", []),
            history_output=current_state.get("history_output", ""),
            available_tools=current_state.get("available_tools", ""),
            expert_context=current_state.get("expert_context", ""),
            delegation_status=current_state.get("delegation_status", ""),
            external_guidance=current_state.get("external_guidance", ""),
            recent_context=memory_context or "无近期上下文",
            related_memories="无相关记忆",
            long_term_reference="无长期记忆参考",
        )

    async def build_memory_context(
        self,
        query: str,
        attention_level: float = 0.6,
    ) -> str:
        """构建纯记忆上下文"""
        working = await self.build_working_context(query, attention_level=attention_level)
        return working.recent_context

    async def build_prompt(
        self,
        current_goal: str,
        current_state: Optional[Dict[str, Any]] = None,
        attention_level: float = 0.6,
        attention_vector=None,
    ) -> str:
        """构建专家 prompt"""
        working = await self.build_working_context(
            current_goal=current_goal,
            current_state=current_state,
            attention_level=attention_level,
        )
        prompt = prompt_manager.build_expert_prompt(
            instruction=current_goal,
            context={
                "notebook_status": working.notebook_status,
                "recent_context": working.recent_context,
                "related_memories": working.related_memories,
                "long_term_reference": working.long_term_reference,
                "available_tools": working.available_tools,
                "history_output": working.history_output,
                "tier": current_state.get("tier", "") if current_state else "",
                "has_skill": current_state.get("has_skill", False) if current_state else False,
            },
        )

        if working.external_guidance:
            prompt += "\n\n" + working.external_guidance
        if working.expert_context:
            prompt += "\n\n" + working.expert_context
        if working.delegation_status:
            prompt += "\n\n" + working.delegation_status

        # 注入自我反思积累的行为准则（ValueSystem）
        try:
            from modules.thinking.evolution.value_system import value_system
            values_text = value_system.get_active_rules()
            if values_text:
                prompt += "\n\n" + values_text
        except Exception as e:
            self.logger.debug(f"[ValueSystem] 注入行为准则失败 (非致命): {e}")
        return prompt

    # ------------------------------------------------------------------
    # 上下文段格式化（不依赖记忆）
    # ------------------------------------------------------------------

    @staticmethod
    def build_external_guidance(
        persistent_prompts: List[str],
        transient_prompts: List[str],
    ) -> str:
        """格式化外部引导文本为 prompt 注入段。"""
        from typing import List as _List
        external_parts: _List[str] = []

        if persistent_prompts:
            limited = persistent_prompts[-5:]
            combined = "\n\n".join(
                f"[系统简报 #{i+1}]\n{pp}"
                for i, pp in enumerate(limited)
            )
            external_parts.append(combined)

        if transient_prompts:
            transient_text = "\n\n".join(
                f"[本轮提示 #{i+1}]\n{tp}"
                for i, tp in enumerate(transient_prompts[-3:])
            )
            external_parts.append(transient_text)

        return "\n\n".join(external_parts)

    @staticmethod
    def build_expert_context(
        blackboard: Any,
        caller_tier: str,
        last_read_count: int,
    ) -> Tuple[str, int]:
        """从 CognitiveBlackboard 读取其他模型的最新输出。"""
        if not blackboard:
            return "", last_read_count

        try:
            current_size = blackboard.size()
            new_count = current_size - last_read_count
            if new_count <= 0:
                return "", current_size

            full_text = blackboard.format_for_model(
                limit=20,
                exclude_tier=caller_tier,
            )
            if not full_text:
                return "", current_size

            logger = setup_logger("context_manager")
            logger.debug(
                f"[专家回复注入] 新条目={new_count}, 内容长度={len(full_text)}"
            )
            return full_text, current_size
        except Exception as e:
            logger = setup_logger("context_manager")
            logger.debug(f"[专家回复注入] 读取失败 (非致命): {e}")
            return "", last_read_count

    @staticmethod
    def build_delegation_status(pending_delegations: Dict[str, dict]) -> str:
        """构建委托状态摘要。"""
        if not pending_delegations:
            return ""

        recent = list(pending_delegations.values())[-10:]
        if not recent:
            return ""

        has_pending = any(d.get("status") == "pending" for d in recent)

        lines = ["【当前委托状态】"]
        if has_pending:
            lines.append("⚠️ 你有委托正在等待专家回复。请勿向用户追问，耐心等待结果。")

        for d in recent:
            status_icon = {
                "pending": "⏳", "replied": "✅", "completed": "✅", "stale": "⏰",
            }.get(d.get("status", "pending"), "❓")
            lines.append(
                f"  {status_icon} 第{d['round']}轮委托 [{d['role']}]: {d['task'][:80]} "
                f"— {d.get('status', 'pending')}"
            )

        has_replied = any(d.get("status") == "replied" for d in recent)
        if has_replied:
            lines.append("⚠️ 以上 ✅ 标记的委托已有回复，请阅读上方的「专家最新回复」而非重新委托。")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 记忆上下文加载/存储（旧版兼容存根）
    # ------------------------------------------------------------------

    @staticmethod
    async def load_context(
        user_input: str,
        context: List[Dict],
        session_id: str = "",
    ) -> Tuple[str, Any]:
        return "", None

    @staticmethod
    async def load_shared_context(
        user_input: str,
        context: List[Dict],
        session_id: str = "",
    ) -> Tuple[str, Any]:
        return "", None

    @staticmethod
    async def load_private_context(
        user_input: str,
        model_id: str,
        session_id: str = "",
        model_role: str = "",
        memory_manager: Any = None,
    ) -> Tuple[str, Any]:
        return "", memory_manager

    @staticmethod
    def inject_to_dialog(blackboard: Any, memory_context_text: str) -> None:
        pass

    @staticmethod
    def save_memory(
        mm: Any, session_id: str, user_input: str, response: str,
        gcm_pool: Any = None, turns: int = 0,
    ) -> None:
        pass
