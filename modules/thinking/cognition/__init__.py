"""
认知黑板模块 - Blackboard Architecture 实现

核心组件：
- CognitiveBlackboard：唯一的认知状态源
- ContextSlicer：为每个 tier 生成定制化上下文切片
- TurnContext / TurnState：轮次生命周期（定义在 context/pool.py）
"""
from modules.thinking.context.pool import TurnState, TurnContext
from .blackboard import CognitiveBlackboard, BlackboardSnapshot
from .context_slicer import ContextSlicer

__all__ = [
    "TurnState",
    "TurnContext",
    "CognitiveBlackboard",
    "BlackboardSnapshot",
    "ContextSlicer",
]
