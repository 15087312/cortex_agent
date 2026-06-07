"""
认知黑板模块 - Blackboard Architecture 实现

核心思想：
- CognitiveBlackboard：唯一的认知状态源，替代分散的 SharedDialog._blackboard + ContinuousThinker 字段
- ContextSlicer：为每个 tier 生成定制化上下文切片，替代全量 format_for_model
- SessionLifecycle：显式状态机，替代隐式 reset 逻辑
- DomainEvent：领域事件，驱动状态转移

架构优势：
- N 而非 N² 复杂度（每个 Agent 只读自己的切片）
- 状态唯一源（无分散状态）
- 显式生命周期（可追踪、可测试）
- 事件驱动（响应式而非轮询式）
"""

from .domain_events import DomainEventType, DomainEvent
from .turn_context import TurnState, TurnContext
from .blackboard import CognitiveBlackboard, BlackboardSnapshot
from .context_slicer import ContextSlicer
from .session_lifecycle import SessionLifecycle

__all__ = [
    "DomainEventType",
    "DomainEvent",
    "TurnState",
    "TurnContext",
    "CognitiveBlackboard",
    "BlackboardSnapshot",
    "ContextSlicer",
    "SessionLifecycle",
]
