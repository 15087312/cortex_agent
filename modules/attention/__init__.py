"""
注意力系统 - 权重计算、优先级调度

模块门面只导出接口和工厂函数，避免外部模块直接依赖具体实现。
"""
from modules.attention.interface import (
    AttentionDecision,
    AttentionInterface,
    MemoryAttentionScoringPort,
    create_attention_interface,
    create_memory_attention_scorer,
)

__all__ = [
    "AttentionDecision",
    "AttentionInterface",
    "MemoryAttentionScoringPort",
    "create_attention_interface",
    "create_memory_attention_scorer",
]
