"""
上下文管理器 — 统一决定当前认知窗口

职责边界：
- 输入：current_goal/current_state + RetrievalResult + 外部事件/引导
- 调用 AttentionScorer 对候选上下文排序
- 输出：WorkingContext 和最终 prompt
- 格式化：外部引导、专家上下文、委托状态等上下文段的格式化

LLM 是纯推理器；上下文选择、记忆召回、专家结果回流都在这里完成。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import setup_logger
from infra.prompts import prompt_manager
from modules.attention.interface import create_memory_attention_scorer, MemoryAttentionScoringPort
from modules.memory.retriever import MemoryRetriever, RetrievalResult


_shared_memory_attention_scorer: Optional[MemoryAttentionScoringPort] = None


def get_shared_memory_attention_scorer() -> MemoryAttentionScoringPort:
    """Return the process-wide scorer used by short-lived context managers."""
    global _shared_memory_attention_scorer
    if _shared_memory_attention_scorer is None:
        _shared_memory_attention_scorer = create_memory_attention_scorer()
    return _shared_memory_attention_scorer


@dataclass
class WorkingContext:
    """当前认知窗口 — 最终允许进入 LLM 的上下文"""
    current_goal: str
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
    """上下文操作系统。"""

    def __init__(
        self,
        memory_manager=None,
        retriever: Optional[MemoryRetriever] = None,
        scorer: Optional[MemoryAttentionScoringPort] = None,
        max_recent: int = 5,
        max_related: int = 3,
        max_long_term: int = 3,
    ):
        self.memory = memory_manager
        self.retriever = retriever or MemoryRetriever(memory_manager=memory_manager)
        self.scorer = scorer or create_memory_attention_scorer()
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
        current_state = current_state or {}
        retrieval = await self.retriever.retrieve(current_goal)
        selected = await self._select_memories(current_goal, retrieval, attention_level)

        working = WorkingContext(
            current_goal=current_goal,
            notebook_status=current_state.get("notebook_status", {}),
            selected_memories=selected,
            selected_events=current_state.get("environment_events", []),
            selected_goals=current_state.get("selected_goals", []),
            history_output=current_state.get("history_output", ""),
            available_tools=current_state.get("available_tools", ""),
            expert_context=current_state.get("expert_context", ""),
            delegation_status=current_state.get("delegation_status", ""),
            external_guidance=current_state.get("external_guidance", ""),
            priority_score=self._calculate_priority_score(selected),
            metadata={"retrieval_stats": retrieval.stats},
        )
        self._format_memory_sections(working, retrieval)
        return working

    async def build_memory_context(
        self,
        query: str,
        attention_level: float = 0.6,
    ) -> str:
        """构建纯记忆上下文（不含专家 prompt 模板），供 load_private_context 使用。

        只返回记忆相关段落：近期上下文、相关记忆、长期记忆参考。
        """
        working = await self.build_working_context(
            current_goal=query,
            attention_level=attention_level,
        )
        parts = []
        if working.recent_context and working.recent_context != "无近期上下文":
            parts.append(f"【近期对话】\n{working.recent_context}")
        if working.related_memories and working.related_memories != "无相关记忆":
            parts.append(f"【相关记忆】\n{working.related_memories}")
        if working.long_term_reference and working.long_term_reference != "无长期记忆参考":
            parts.append(f"【长期记忆】\n{working.long_term_reference}")
        return "\n\n".join(parts)

    async def build_prompt(
        self,
        current_goal: str,
        current_state: Optional[Dict[str, Any]] = None,
        attention_level: float = 0.6,
        attention_vector=None,  # Optional[AttentionVector] (V2)
    ) -> str:
        # 如果有V2向量，将其信息注入上下文
        v2_context = ""
        if attention_vector is not None:
            try:
                from modules.attention.core.v2.attention_vector import AttentionVector
                if isinstance(attention_vector, AttentionVector):
                    # 生成V2上下文信息
                    v2_parts = [
                        f"语义相关性: {attention_vector.semantic:.2f}",
                        f"时间敏感性: {attention_vector.temporal:.2f}",
                        f"任务重要性: {attention_vector.task:.2f}",
                        f"情感强度: {attention_vector.emotion:.2f}",
                        f"模态权重: {attention_vector.modality:.2f}",
                    ]
                    v2_context = f"【注意力状态】{', '.join(v2_parts)}"
            except Exception:
                pass

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
        
        # 注入V2注意力上下文
        if v2_context:
            prompt += "\n\n" + v2_context
        
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
            logger.debug(f"[ValueSystem] 注入行为准则失败 (非致命): {e}")
        return prompt

    # ------------------------------------------------------------------
    # 上下文段格式化
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
        """从 CognitiveBlackboard 读取其他模型的最新输出，注入到当前轮上下文。

        Args:
            blackboard: CognitiveBlackboard 实例，None 时不产生输出
            caller_tier: 调用者的 tier，用于排除自身输出
            last_read_count: 上次读取时的条目数

        Returns:
            (格式化文本, 新的读取位置)
        """
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
        """构建委托状态摘要，告知模型已发起的委托及其状态。

        目的：
        1. 防止模型重复委托已完成的任务
        2. 让模型知道哪些委托还在等待中
        3. 已完成/已回复的委托 → 模型应处理结果而非重新委托
        """
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

    async def _select_memories(
        self,
        query: str,
        retrieval: RetrievalResult,
        attention_level: float,
    ) -> List[Dict[str, Any]]:
        selected = []

        # 热记忆：原始近期事件，最多保留最近 max_recent 条
        recent = retrieval.recent_memory[-self.max_recent:]
        selected.extend(recent)

        # 温/冷记忆：候选上下文进入 Attention System 排序
        candidates = retrieval.warm_memory + retrieval.long_term_matches
        if candidates:
            try:
                scored = await self.scorer.score_memories(
                    query=query,
                    memories=candidates,
                    attention_level=attention_level,
                )
                selected.extend(scored)
            except Exception as e:
                self.logger.debug("注意力排序失败，降级使用候选前几条: %s", e)
                selected.extend(candidates[: self.max_related + self.max_long_term])

        return selected

    def _format_memory_sections(
        self, working: WorkingContext, retrieval: RetrievalResult) -> None:
        recent_lines = []
        for item in retrieval.recent_memory[-self.max_recent:]:
            content = item.get("content", "")
            if content:
                recent_lines.append(f"- {str(content)}")
        working.recent_context = "\n".join(recent_lines) if recent_lines else "无近期上下文"

        warm_selected = [m for m in working.selected_memories if m.get("source") == "warm_memory"]
        related_lines = []
        for item in warm_selected[: self.max_related]:
            content = item.get("content", "")
            score = item.get("attention_score", 0)
            if content:
                related_lines.append(f"- {str(content)} (相关度: {score:.2f})")
        working.related_memories = "\n".join(related_lines) if related_lines else "无相关记忆"

        long_selected = [m for m in working.selected_memories if m.get("source") == "long_term"]
        long_lines = []
        for item in long_selected[: self.max_long_term]:
            content = item.get("content", "")
            score = item.get("attention_score", 0)
            if content:
                long_lines.append(f"- {str(content)} (相关度: {score:.2f})")
        working.long_term_reference = "\n".join(long_lines) if long_lines else "无长期记忆参考"

    def _calculate_priority_score(self, memories: List[Dict[str, Any]]) -> float:
        if not memories:
            return 0.0
        scores = [float(m.get("attention_score", 0.5)) for m in memories]
        return sum(scores) / len(scores)

    # ------------------------------------------------------------------
    # 记忆上下文加载/存储（从 orchestrator 移入，归入上下文管理）
    # ------------------------------------------------------------------

    @staticmethod
    async def load_context(
        user_input: str,
        context: List[Dict],
        session_id: str = "",
    ) -> Tuple[str, Any]:
        """兼容入口：默认加载公共记忆上下文。"""
        return await ContextManager.load_shared_context(user_input, context, session_id)

    @staticmethod
    async def load_shared_context(
        user_input: str,
        context: List[Dict],
        session_id: str = "",
    ) -> Tuple[str, Any]:
        """加载 shared/global 记忆上下文，供 CognitiveBlackboard 公共窗口使用。"""
        mm = None
        try:
            from modules.memory.core.memory_manager import MemoryManager
            mm = MemoryManager.get_instance(model_id="shared")
            mm.set_session_id(session_id or "")
            mm.set_owner("shared")
            retriever = MemoryRetriever(
                memory_manager=mm,
                scopes=["shared", "global"],
                owner="shared",
            )
            cm = ContextManager(
                memory_manager=mm,
                retriever=retriever,
                scorer=get_shared_memory_attention_scorer(),
            )
            prompt = await cm.build_memory_context(query=user_input)
            text = prompt or ""
            return text, mm
        except Exception as e:
            logger = setup_logger("context_manager")
            logger.debug(f"[公共记忆上下文] 加载失败: {e}")
            return "", mm

    @staticmethod
    async def load_private_context(
        user_input: str,
        model_id: str,
        session_id: str = "",
        model_role: str = "",
        memory_manager: Any = None,
    ) -> Tuple[str, Any]:
        """加载指定模型可见的 private/global 记忆上下文，仅用于该模型 prompt。

        Args:
            memory_manager: 可选的已有 MemoryManager 实例，避免重复创建（复用提升性能）
        """
        mm = memory_manager
        try:
            from modules.memory.core.memory_manager import MemoryManager
            if mm is None:
                mm = MemoryManager.get_instance(model_id=model_id)
            mm.set_session_id(session_id or "")
            mm.set_owner(model_id)
            retriever = MemoryRetriever(
                memory_manager=mm,
                scopes=["private", "global"],
                owner=model_id,
            )
            cm = ContextManager(
                memory_manager=mm,
                retriever=retriever,
                scorer=get_shared_memory_attention_scorer(),
                max_recent=3,
                max_related=2,
                max_long_term=2,
            )
            goal = f"{model_role}: {user_input}" if model_role else user_input
            prompt = await cm.build_memory_context(query=goal)
            text = prompt or ""
            return text, mm
        except Exception as e:
            logger = setup_logger("context_manager")
            logger.debug(f"[私有记忆上下文] 加载失败: {e}")
            return "", mm

    @staticmethod
    def inject_to_dialog(
        blackboard: Any,
        memory_context_text: str,
    ) -> None:
        """将记忆上下文写入 CognitiveBlackboard，供大模型读取。"""
        if not blackboard or not memory_context_text or not memory_context_text.strip():
            return
        try:
            blackboard.write_thought(
                model_id="system",
                tier="system",
                content=f"【公共会话记忆上下文】\n{memory_context_text}",
                metadata={"context_type": "shared_memory_context", "scope": "shared"},
            )
        except Exception as e:
            logger = setup_logger("context_manager")
            logger.debug(f"[记忆上下文] 注入 CognitiveBlackboard 失败: {e}")

    @staticmethod
    def save_memory(
        mm: Any,
        session_id: str,
        user_input: str,
        response: str,
        gcm_pool: Any = None,
        turns: int = 0,
    ) -> None:
        """存储对话到记忆，并同步到 GCM。"""
        try:
            if mm is None:
                from modules.memory.core.memory_manager import MemoryManager
                mm = MemoryManager()
            mm.set_session_id(session_id or "")
            mm.save_dialog_turn(
                user_input=user_input,
                assistant_response=response,
                metadata={"scope": "shared", "owner": "shared"},
                scope="shared",
            )

            if gcm_pool and response:
                from modules.thinking.context.wire import sync_model_call
                sync_model_call(
                    gcm_pool,
                    "continuous_thinker",
                    response,
                    metadata={
                        "turns": turns,
                        "user_input": user_input,
                    },
                    importance=0.7,
                )
        except Exception as e:
            logger = setup_logger("context_manager")
            logger.debug(f"[记忆] 存储/GCM 同步失败: {e}")


