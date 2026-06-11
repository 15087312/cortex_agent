"""
自适应注意力衰减

根据任务阶段、信息新鲜度、认知负荷动态调整注意力衰减率。
支持：
1. 阶段感知衰减（不同任务阶段使用不同衰减率）
2. 新鲜度感知衰减（新信息衰减慢，旧信息衰减快）
3. 认知负荷感知衰减（负荷高时加速衰减）
"""
import math
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from utils.logger import setup_logger
from modules.attention.core.v2.attention_vector import AttentionVector

logger = setup_logger("adaptive_decay")


@dataclass
class DecayConfig:
    """衰减配置"""
    # 基础衰减率
    base_decay_rate: float = 0.1
    
    # 阶段特定衰减率
    stage_decay_rates: Dict[str, float] = None
    
    # 新鲜度阈值
    freshness_threshold: float = 300.0  # 5分钟（秒）
    
    # 认知负荷衰减因子
    cognitive_load_factor: float = 1.5
    
    # 最小衰减率
    min_decay_rate: float = 0.01
    
    # 最大衰减率
    max_decay_rate: float = 1.0
    
    def __post_init__(self):
        if self.stage_decay_rates is None:
            self.stage_decay_rates = {
                "exploration": 0.05,   # 探索阶段衰减慢
                "focus": 0.15,         # 聚焦阶段衰减中等
                "execution": 0.2,      # 执行阶段衰减快
                "review": 0.1,         # 回顾阶段衰减中等
            }


class AdaptiveDecay:
    """自适应注意力衰减器"""
    
    def __init__(self, config: Optional[DecayConfig] = None):
        self.config = config or DecayConfig()
        self.current_stage = "exploration"
        self.cognitive_load = 0.5  # 0-1
        self.attention_history: List[AttentionVector] = []
        self.logger = setup_logger("adaptive_decay")
    
    def set_stage(self, stage: str):
        """设置当前任务阶段"""
        if stage in self.config.stage_decay_rates:
            self.current_stage = stage
            self.logger.debug(f"任务阶段切换为: {stage}")
        else:
            self.logger.warning(f"未知任务阶段: {stage}")
    
    def set_cognitive_load(self, load: float):
        """设置认知负荷 (0-1)"""
        self.cognitive_load = max(0.0, min(1.0, load))
    
    def compute_decay_rate(
        self,
        information_age: float,
        importance: float = 0.5,
        source: str = "text"
    ) -> float:
        """计算自适应衰减率"""
        # 基础衰减率
        base_rate = self.config.base_decay_rate
        
        # 阶段调整
        stage_rate = self.config.stage_decay_rates.get(
            self.current_stage, base_rate
        )
        
        # 新鲜度调整
        freshness_factor = self._compute_freshness_factor(information_age)
        
        # 认知负荷调整
        load_factor = 1.0 + (self.cognitive_load * self.config.cognitive_load_factor)
        
        # 重要性调整（重要信息衰减慢）
        importance_factor = 1.0 - (importance * 0.3)
        
        # 来源调整
        source_factor = self._compute_source_factor(source)
        
        # 综合衰减率
        decay_rate = stage_rate * freshness_factor * load_factor * importance_factor * source_factor
        
        # 限制在合理范围
        decay_rate = max(self.config.min_decay_rate, min(self.config.max_decay_rate, decay_rate))
        
        return decay_rate
    
    def _compute_freshness_factor(self, information_age: float) -> float:
        """计算新鲜度因子"""
        if information_age < self.config.freshness_threshold:
            # 新鲜信息衰减慢
            return 0.5
        elif information_age < self.config.freshness_threshold * 2:
            # 中等新鲜度
            return 1.0
        else:
            # 旧信息衰减快
            return 1.5
    
    def _compute_source_factor(self, source: str) -> float:
        """计算来源因子"""
        source_factors = {
            "text": 1.0,
            "visual": 0.8,    # 视觉信息衰减慢
            "audio": 0.9,     # 音频信息衰减中等
            "system": 0.7,    # 系统信息衰减慢
            "memory": 1.2,    # 记忆信息衰减快
        }
        return source_factors.get(source, 1.0)
    
    def apply_decay(
        self,
        vector: AttentionVector,
        time_elapsed: float,
        information_age: Optional[float] = None
    ) -> AttentionVector:
        """应用自适应衰减"""
        if information_age is None:
            information_age = time_elapsed
        
        decay_rate = self.compute_decay_rate(
            information_age=information_age,
            importance=vector.task,
            source=vector.source
        )
        
        return vector.apply_decay(decay_rate, time_elapsed)
    
    def batch_apply_decay(
        self,
        vectors: List[AttentionVector],
        time_elapsed: float,
        information_ages: Optional[List[float]] = None
    ) -> List[AttentionVector]:
        """批量应用衰减"""
        if information_ages is None:
            information_ages = [time_elapsed] * len(vectors)
        
        decayed_vectors = []
        for vec, age in zip(vectors, information_ages):
            decayed = self.apply_decay(vec, time_elapsed, age)
            decayed_vectors.append(decayed)
        
        return decayed_vectors
    
    def compute_attention_schedule(
        self,
        initial_vectors: List[AttentionVector],
        time_horizon: float,
        interval: float = 60.0
    ) -> List[List[AttentionVector]]:
        """计算注意力衰减时间表"""
        schedule = []
        current_vectors = initial_vectors.copy()
        
        time_points = [i * interval for i in range(int(time_horizon / interval) + 1)]
        
        for t in time_points:
            if t == 0:
                schedule.append(current_vectors.copy())
            else:
                decayed = self.batch_apply_decay(current_vectors, interval)
                current_vectors = decayed
                schedule.append(current_vectors.copy())
        
        return schedule
    
    def detect_attention_shift(
        self,
        current_vector: AttentionVector,
        previous_vector: AttentionVector,
        threshold: float = 0.3
    ) -> Dict[str, Any]:
        """检测注意力转移"""
        # 计算各维度变化
        changes = {
            "semantic": abs(current_vector.semantic - previous_vector.semantic),
            "temporal": abs(current_vector.temporal - previous_vector.temporal),
            "task": abs(current_vector.task - previous_vector.task),
            "emotion": abs(current_vector.emotion - previous_vector.emotion),
            "modality": abs(current_vector.modality - previous_vector.modality),
        }
        
        # 检测显著变化
        significant_shifts = {
            dim: change for dim, change in changes.items()
            if change > threshold
        }
        
        # 计算总变化
        total_change = sum(changes.values())
        
        return {
            "total_change": total_change,
            "changes": changes,
            "significant_shifts": significant_shifts,
            "has_shift": len(significant_shifts) > 0,
            "shift_magnitude": len(significant_shifts) / len(changes),
        }
    
    def get_state(self) -> Dict[str, Any]:
        """获取衰减器状态"""
        return {
            "current_stage": self.current_stage,
            "cognitive_load": self.cognitive_load,
            "config": {
                "base_decay_rate": self.config.base_decay_rate,
                "freshness_threshold": self.config.freshness_threshold,
                "cognitive_load_factor": self.config.cognitive_load_factor,
            }
        }