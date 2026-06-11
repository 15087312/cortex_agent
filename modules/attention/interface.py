"""
注意力接口

支持V1（标量）和V2（多维度向量）两种模式。

V1模式：
- attention_level: 标量，控制记忆检索阈值

V2模式：
- AttentionVector: 多维度向量（semantic, temporal, task, emotion, modality）
- AttentionState: 完整注意力状态（向量 + 资源分配 + 解释）
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Protocol, runtime_checkable


@dataclass
class AttentionDecision:
    """任务重要性决策（V1兼容）"""
    focus: str = ""
    related_memory: List[str] = field(default_factory=list)
    importance_score: float = 0.5
    importance_reasons: List[str] = field(default_factory=list)
    attention_level: float = 0.6
    # V2扩展字段
    attention_vector: Optional[Any] = None  # AttentionVector (V2)
    allocation: Optional[Any] = None        # AllocationResult (V2)
    explanation: Optional[Dict[str, Any]] = None  # V2可解释性


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
            focus=getattr(decision, 'focus', ''),
            related_memory=getattr(decision, 'related_memory', []),
            importance_score=getattr(decision, 'importance_score', 0.5),
            importance_reasons=getattr(decision, 'importance_reasons', []),
            attention_level=getattr(decision, 'attention_level', 0.6),
        )


class AttentionV2Adapter(AttentionInterface):
    """V2注意力适配器 - 将 AttentionEngine 适配为 AttentionInterface"""

    def __init__(self, engine=None):
        """初始化V2适配器
        
        Args:
            engine: AttentionEngine实例，如果为None则自动创建
        """
        if engine is None:
            from modules.attention.core.v2.attention_engine import create_attention_engine
            engine = create_attention_engine()
        self._engine = engine

    def analyze(
        self,
        user_input: str,
        context: Optional[List[Dict]] = None,
        short_term_memory: Optional[List[str]] = None
    ) -> AttentionDecision:
        """使用V2引擎分析输入"""
        # 处理感知事件（如果有的话）
        perception_events = []
        if context:
            for msg in context:
                if isinstance(msg, dict) and msg.get("role") == "system":
                    content = msg.get("content", "")
                    if "【环境感知】" in content:
                        # 提取感知事件
                        perception_events.append({
                            "type": "text",
                            "confidence": 0.8,
                            "urgency": 0.5,
                        })

        # 使用V2引擎处理
        state = self._engine.process_input(
            user_input=user_input,
            context=context,
            perception_events=perception_events if perception_events else None,
        )

        # 转换为AttentionDecision
        return AttentionDecision(
            focus=user_input[:50],
            related_memory=[],
            importance_score=state.current_vector.to_scalar(),
            importance_reasons=["V2多维度分析"],
            attention_level=state.current_vector.to_scalar(),
            attention_vector=state.current_vector,
            allocation=state.allocation,
            explanation=state.explanation,
        )

    @property
    def engine(self):
        """获取底层V2引擎"""
        return self._engine


def create_attention_interface(attention_core=None, use_v2: bool = False) -> AttentionInterface:
    """Create an attention interface.
    
    Args:
        attention_core: V1核心实例（仅V1模式使用）
        use_v2: 是否使用V2引擎
    
    Returns:
        AttentionInterface实例
    """
    if use_v2:
        return AttentionV2Adapter()
    
    if attention_core is None:
        from modules.attention.core.attention_core import AttentionCore
        attention_core = AttentionCore()
    
    return AttentionAdapter(attention_core)


def get_attention_engine():
    """获取全局V2注意力引擎（延迟初始化）"""
    from modules.attention.core.v2.attention_engine import AttentionEngine
    from modules.core.singleton import Singleton
    
    class AttentionEngineSingleton(Singleton):
        _instance = None
        
        def __init__(self):
            if AttentionEngineSingleton._instance is None:
                AttentionEngineSingleton._instance = AttentionEngine()
        
        @classmethod
        def get_instance(cls):
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance._engine if hasattr(cls._instance, '_engine') else cls._instance
    
    return AttentionEngineSingleton.get_instance()