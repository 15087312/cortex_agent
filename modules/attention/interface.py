"""
注意力接口 - 解耦注意力决策

定义注意力决策的抽象接口
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Protocol, runtime_checkable


@dataclass
class AttentionDecision:
    """注意力决策"""
    focus: str
    active_modules: List[str]
    sleep_modules: List[str]
    priority_weights: Dict[str, float]
    related_memory: List[str]
    context_related: List[Dict]
    reasoning: str = ""  # 决策原因
    importance_score: float = 0.5
    importance_reasons: List[str] = field(default_factory=list)
    attention_level: float = 0.6


@runtime_checkable
class MemoryAttentionScoringPort(Protocol):
    """Protocol for scoring memory candidates by attention policy."""

    async def score_memories(
        self,
        query: str,
        memories: List[Dict[str, Any]],
        attention_level: float = None,
    ) -> List[Dict[str, Any]]:
        """Score, filter, and order candidate memories."""

    async def score_single(
        self,
        query: str,
        memory: Dict[str, Any],
        attention_level: float = None,
    ) -> Optional[Dict[str, Any]]:
        """Score a single candidate memory."""

    def get_last_effective_policy(self) -> Dict[str, Any]:
        """Return the last effective attention policy parameters."""


def create_memory_attention_scorer(attention_level: float = 0.6) -> MemoryAttentionScoringPort:
    """Create the default memory attention scorer.

    Concrete imports are delayed so consumers can depend on this attention
    module facade instead of the implementation class.
    """
    from modules.attention.core.memory_attention_scorer import MemoryAttentionScorer

    return MemoryAttentionScorer(attention_level=attention_level)


class AttentionInterface(ABC):
    """注意力接口"""

    @abstractmethod
    def analyze(
        self,
        user_input: str,
        context: Optional[List[Dict]] = None,
        short_term_memory: Optional[List[str]] = None
    ) -> AttentionDecision:
        """
        分析输入，产生注意力决策

        Args:
            user_input: 用户输入
            context: 对话上下文
            short_term_memory: 短期记忆

        Returns:
            注意力决策
        """
        pass


class AttentionAdapter(AttentionInterface):
    """注意力适配器 - 将现有 AttentionCore 适配为接口"""

    def __init__(self, attention_core):
        self._core = attention_core

    def analyze(
        self,
        user_input: str,
        context: Optional[List[Dict]] = None,
        short_term_memory: Optional[List[str]] = None
    ) -> AttentionDecision:
        """调用现有 AttentionCore"""
        decision = self._core.analyze(user_input, context, short_term_memory)

        return AttentionDecision(
            focus=decision.focus,
            active_modules=decision.active_modules,
            sleep_modules=decision.sleep_modules,
            priority_weights=decision.priority_weights,
            related_memory=decision.related_memory,
            context_related=decision.context_related,
            reasoning=getattr(decision, 'reasoning', ''),
            importance_score=getattr(decision, 'importance_score', 0.5),
            importance_reasons=getattr(decision, 'importance_reasons', []),
            attention_level=getattr(decision, 'attention_level', 0.6),
        )


def create_attention_interface(attention_core=None) -> AttentionInterface:
    """Create an attention interface from an existing or default attention core."""
    if attention_core is None:
        from modules.attention.core.attention_core import AttentionCore

        attention_core = AttentionCore()
    return AttentionAdapter(attention_core)