"""
注意力系统 V2 - 多维度、跨模态、自适应注意力机制

新特性：
1. 多维度注意力向量（语义、时间、任务、情感、模态）
2. 跨模态注意力融合（文本、视觉、语音）
3. 自适应注意力衰减
4. 注意力驱动的资源分配
5. 注意力可解释性
"""
from modules.attention.core.v2.attention_vector import AttentionVector
from modules.attention.core.v2.cross_modal_fusion import CrossModalFusion
from modules.attention.core.v2.adaptive_decay import AdaptiveDecay
from modules.attention.core.v2.resource_allocator import ResourceAllocator
from modules.attention.core.v2.attention_explainer import AttentionExplainer
from modules.attention.core.v2.attention_engine import AttentionEngine

__all__ = [
    "AttentionVector",
    "CrossModalFusion",
    "AdaptiveDecay",
    "ResourceAllocator",
    "AttentionExplainer",
    "AttentionEngine",
]