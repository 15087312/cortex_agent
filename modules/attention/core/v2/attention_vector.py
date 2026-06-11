"""
多维度注意力向量

将标量注意力扩展为多维向量表示：
- semantic: 语义相关性
- temporal: 时间衰减
- task: 任务优先级
- emotion: 情感强度
- modality: 模态权重
"""
import math
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger("attention_vector")


@dataclass
class AttentionVector:
    """多维度注意力向量"""
    
    # 核心维度
    semantic: float = 0.5      # 语义相关性 (0-1)
    temporal: float = 0.5      # 时间衰减 (0-1)
    task: float = 0.5          # 任务优先级 (0-1)
    emotion: float = 0.0       # 情感强度 (0-1)
    modality: float = 0.5      # 模态权重 (0-1)
    
    # 元数据
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str = "unknown"    # 来源：text/visual/audio/system
    confidence: float = 1.0    # 置信度 (0-1)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "semantic": self.semantic,
            "temporal": self.temporal,
            "task": self.task,
            "emotion": self.emotion,
            "modality": self.modality,
            "timestamp": self.timestamp,
            "source": self.source,
            "confidence": self.confidence,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AttentionVector":
        """从字典创建"""
        return cls(
            semantic=data.get("semantic", 0.5),
            temporal=data.get("temporal", 0.5),
            task=data.get("task", 0.5),
            emotion=data.get("emotion", 0.0),
            modality=data.get("modality", 0.5),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            source=data.get("source", "unknown"),
            confidence=data.get("confidence", 1.0),
        )
    
    def magnitude(self) -> float:
        """计算向量模长"""
        return math.sqrt(
            self.semantic ** 2 +
            self.temporal ** 2 +
            self.task ** 2 +
            self.emotion ** 2 +
            self.modality ** 2
        )
    
    def normalize(self) -> "AttentionVector":
        """归一化向量"""
        mag = self.magnitude()
        if mag == 0:
            return AttentionVector()
        
        return AttentionVector(
            semantic=self.semantic / mag,
            temporal=self.temporal / mag,
            task=self.task / mag,
            emotion=self.emotion / mag,
            modality=self.modality / mag,
            timestamp=self.timestamp,
            source=self.source,
            confidence=self.confidence,
        )
    
    def dot(self, other: "AttentionVector") -> float:
        """点积"""
        return (
            self.semantic * other.semantic +
            self.temporal * other.temporal +
            self.task * other.task +
            self.emotion * other.emotion +
            self.modality * other.modality
        )
    
    def cosine_similarity(self, other: "AttentionVector") -> float:
        """余弦相似度"""
        dot_product = self.dot(other)
        mag_self = self.magnitude()
        mag_other = other.magnitude()
        
        if mag_self == 0 or mag_other == 0:
            return 0.0
        
        return dot_product / (mag_self * mag_other)
    
    def weighted_average(self, other: "AttentionVector", weight: float = 0.5) -> "AttentionVector":
        """加权平均"""
        return AttentionVector(
            semantic=self.semantic * (1 - weight) + other.semantic * weight,
            temporal=self.temporal * (1 - weight) + other.temporal * weight,
            task=self.task * (1 - weight) + other.task * weight,
            emotion=self.emotion * (1 - weight) + other.emotion * weight,
            modality=self.modality * (1 - weight) + other.modality * weight,
            timestamp=self.timestamp,
            source=self.source,
            confidence=self.confidence * (1 - weight) + other.confidence * weight,
        )
    
    def apply_decay(self, decay_rate: float, time_elapsed: float) -> "AttentionVector":
        """应用时间衰减"""
        decay_factor = math.exp(-decay_rate * time_elapsed)
        
        return AttentionVector(
            semantic=self.semantic,
            temporal=self.temporal * decay_factor,
            task=self.task,
            emotion=self.emotion * decay_factor,
            modality=self.modality,
            timestamp=self.timestamp,
            source=self.source,
            confidence=self.confidence * decay_factor,
        )
    
    def to_scalar(self, weights: Optional[Dict[str, float]] = None) -> float:
        """转换为标量（兼容旧系统）"""
        if weights is None:
            weights = {
                "semantic": 0.3,
                "temporal": 0.2,
                "task": 0.3,
                "emotion": 0.1,
                "modality": 0.1,
            }
        
        scalar = (
            self.semantic * weights.get("semantic", 0.3) +
            self.temporal * weights.get("temporal", 0.2) +
            self.task * weights.get("task", 0.3) +
            self.emotion * weights.get("emotion", 0.1) +
            self.modality * weights.get("modality", 0.1)
        )
        
        return max(0.0, min(1.0, scalar))


class AttentionVectorFactory:
    """注意力向量工厂"""
    
    @staticmethod
    def from_text_importance(importance_score: float, text: str) -> AttentionVector:
        """从文本重要性分数创建"""
        # 简单启发式：紧急关键词增加情感维度
        urgent_keywords = ["紧急", "立刻", "马上", "故障", "报错", "崩溃"]
        has_urgency = any(k in text for k in urgent_keywords)
        
        return AttentionVector(
            semantic=importance_score,
            temporal=0.5,
            task=importance_score,
            emotion=0.8 if has_urgency else 0.2,
            modality=0.9,  # 文本模态
            source="text",
        )
    
    @staticmethod
    def from_memory_score(
        semantic_score: float,
        time_decay: float,
        importance: float
    ) -> AttentionVector:
        """从记忆评分创建"""
        return AttentionVector(
            semantic=semantic_score,
            temporal=time_decay,
            task=importance,
            emotion=0.0,
            modality=0.5,
            source="memory",
        )
    
    @staticmethod
    def from_perception_event(
        event_type: str,
        confidence: float,
        urgency: float = 0.5
    ) -> AttentionVector:
        """从感知事件创建"""
        modality_map = {
            "visual": 0.9,
            "audio": 0.8,
            "text": 0.7,
            "file": 0.6,
            "system": 0.5,
        }
        
        return AttentionVector(
            semantic=confidence,
            temporal=0.8,  # 感知事件通常较新
            task=urgency,
            emotion=urgency * 0.5,
            modality=modality_map.get(event_type, 0.5),
            source=event_type,
            confidence=confidence,
        )