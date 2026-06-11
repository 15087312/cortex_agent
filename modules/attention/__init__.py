"""
注意力系统 - 权重计算、优先级调度

模块门面只导出接口和工厂函数，避免外部模块直接依赖具体实现。

V1 模块（现有）：
- AttentionDecision: 注意力决策
- AttentionInterface: 注意力接口
- MemoryAttentionScoringPort: 记忆打分接口

V2 模块（新增）：
- AttentionVector: 多维度注意力向量
- CrossModalFusion: 跨模态融合
- AdaptiveDecay: 自适应衰减
- ResourceAllocator: 资源分配
- AttentionExplainer: 可解释性
- AttentionEngine: 注意力引擎
"""
from modules.attention.interface import (
    AttentionDecision,
    AttentionInterface,
    MemoryAttentionScoringPort,
    create_attention_interface,
    create_memory_attention_scorer,
)

# V2 模块导出
from modules.attention.core.v2 import (
    AttentionVector,
    CrossModalFusion,
    AdaptiveDecay,
    ResourceAllocator,
    AttentionExplainer,
    AttentionEngine,
)

__all__ = [
    # V1
    "AttentionDecision",
    "AttentionInterface",
    "MemoryAttentionScoringPort",
    "create_attention_interface",
    "create_memory_attention_scorer",
    # V2
    "AttentionVector",
    "CrossModalFusion",
    "AdaptiveDecay",
    "ResourceAllocator",
    "AttentionExplainer",
    "AttentionEngine",
]
