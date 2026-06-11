"""
跨模态注意力融合

将不同模态（文本、视觉、语音）的注意力信号融合为统一的注意力状态。
支持多种融合策略：加权平均、门控机制、注意力池化。
"""
import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from utils.logger import setup_logger
from modules.attention.core.v2.attention_vector import AttentionVector

logger = setup_logger("cross_modal_fusion")


@dataclass
class ModalityWeight:
    """模态权重配置"""
    text: float = 0.4
    visual: float = 0.3
    audio: float = 0.2
    system: float = 0.1


class CrossModalFusion:
    """跨模态注意力融合器"""
    
    def __init__(self, strategy: str = "gated"):
        """
        Args:
            strategy: 融合策略
                - "weighted_avg": 加权平均
                - "gated": 门控机制（推荐）
                - "pooling": 注意力池化
        """
        self.strategy = strategy
        self.default_weights = ModalityWeight()
        self.gate_weights = {
            "text": 0.3,
            "visual": 0.3,
            "audio": 0.2,
            "system": 0.2,
        }
        self.logger = setup_logger("cross_modal_fusion")
    
    def fuse(
        self,
        vectors: List[AttentionVector],
        modality_weights: Optional[ModalityWeight] = None
    ) -> AttentionVector:
        """融合多个注意力向量"""
        if not vectors:
            return AttentionVector()
        
        if len(vectors) == 1:
            return vectors[0]
        
        weights = modality_weights or self.default_weights
        
        if self.strategy == "weighted_avg":
            result = self._weighted_average_fusion(vectors, weights)
        elif self.strategy == "gated":
            result = self._gated_fusion(vectors, weights)
        elif self.strategy == "pooling":
            result = self._pooling_fusion(vectors, weights)
        else:
            result = self._weighted_average_fusion(vectors, weights)
        
        # 归一化到0-1范围
        result.semantic = max(0.0, min(1.0, result.semantic))
        result.temporal = max(0.0, min(1.0, result.temporal))
        result.task = max(0.0, min(1.0, result.task))
        result.emotion = max(0.0, min(1.0, result.emotion))
        result.modality = max(0.0, min(1.0, result.modality))
        result.confidence = max(0.0, min(1.0, result.confidence))
        
        return result
    
    def _weighted_average_fusion(
        self,
        vectors: List[AttentionVector],
        weights: ModalityWeight
    ) -> AttentionVector:
        """加权平均融合"""
        modality_weight_map = {
            "text": weights.text,
            "visual": weights.visual,
            "audio": weights.audio,
            "system": weights.system,
        }
        
        total_weight = 0.0
        result = AttentionVector()
        
        for vec in vectors:
            w = modality_weight_map.get(vec.source, 0.1)
            total_weight += w
            
            result.semantic += vec.semantic * w
            result.temporal += vec.temporal * w
            result.task += vec.task * w
            result.emotion += vec.emotion * w
            result.modality += vec.modality * w
            result.confidence += vec.confidence * w
        
        if total_weight > 0:
            result.semantic /= total_weight
            result.temporal /= total_weight
            result.task /= total_weight
            result.emotion /= total_weight
            result.modality /= total_weight
            result.confidence /= total_weight
        
        result.source = "fused"
        return result
    
    def _gated_fusion(
        self,
        vectors: List[AttentionVector],
        weights: ModalityWeight
    ) -> AttentionVector:
        """门控融合机制"""
        # 计算每个模态的门控值
        gates = self._compute_gates(vectors)
        
        # 门控加权融合
        total_gate = sum(gates.values())
        if total_gate == 0:
            return AttentionVector()
        
        result = AttentionVector()
        for vec, gate in zip(vectors, gates.values()):
            normalized_gate = gate / total_gate
            
            result.semantic += vec.semantic * normalized_gate
            result.temporal += vec.temporal * normalized_gate
            result.task += vec.task * normalized_gate
            result.emotion += vec.emotion * normalized_gate
            result.modality += vec.modality * normalized_gate
            result.confidence += vec.confidence * normalized_gate
        
        result.source = "gated_fused"
        return result
    
    def _compute_gates(self, vectors: List[AttentionVector]) -> Dict[str, float]:
        """计算门控值"""
        gates = {}
        
        for vec in vectors:
            # 基于置信度和重要性的门控
            gate_value = (
                vec.confidence * 0.4 +
                vec.semantic * 0.3 +
                vec.task * 0.3
            )
            gates[vec.source] = gate_value
        
        return gates
    
    def _pooling_fusion(
        self,
        vectors: List[AttentionVector],
        weights: ModalityWeight
    ) -> AttentionVector:
        """注意力池化融合"""
        # 使用任务维度作为注意力权重
        attention_weights = [vec.task for vec in vectors]
        total_weight = sum(attention_weights)
        
        if total_weight == 0:
            return AttentionVector()
        
        result = AttentionVector()
        for vec, aw in zip(vectors, attention_weights):
            normalized_aw = aw / total_weight
            
            result.semantic += vec.semantic * normalized_aw
            result.temporal += vec.temporal * normalized_aw
            result.task += vec.task * normalized_aw
            result.emotion += vec.emotion * normalized_aw
            result.modality += vec.modality * normalized_aw
            result.confidence += vec.confidence * normalized_aw
        
        result.source = "pooled_fused"
        return result
    
    def fuse_with_history(
        self,
        current: AttentionVector,
        history: List[AttentionVector],
        history_weight: float = 0.3
    ) -> AttentionVector:
        """融合当前注意力和历史注意力"""
        if not history:
            return current
        
        # 计算历史平均
        history_avg = AttentionVector()
        for h in history:
            history_avg.semantic += h.semantic
            history_avg.temporal += h.temporal
            history_avg.task += h.task
            history_avg.emotion += h.emotion
            history_avg.modality += h.modality
            history_avg.confidence += h.confidence
        
        n = len(history)
        history_avg.semantic /= n
        history_avg.temporal /= n
        history_avg.task /= n
        history_avg.emotion /= n
        history_avg.modality /= n
        history_avg.confidence /= n
        
        # 加权融合
        return current.weighted_average(history_avg, history_weight)