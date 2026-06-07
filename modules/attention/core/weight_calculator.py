"""

ji x
注意力权重计算核心逻辑
"""
from typing import List, Dict, Any
from config.attention_config import AttentionWeightConfig, get_attention_config
from modules.attention.interface import create_memory_attention_scorer
from modules.attention.utils.weight_normalizer import WeightNormalizer
from utils.logger import setup_logger


class WeightCalculator:
    """注意力权重计算器
    
    结合 AttentionWeightConfig 配置，计算综合注意力权重。
    """
    
    def __init__(self, config: AttentionWeightConfig = None):
        """初始化权重计算器
        
        Args:
            config: 注意力权重配置，如果为 None 则使用默认配置
        """
        self.config = config or get_attention_config()
        self.scorer = create_memory_attention_scorer()
        self.normalizer = WeightNormalizer()
        self.logger = setup_logger("weight_calculator")
        self.logger.info("WeightCalculator 初始化完成")
    
    def calculate(self, input_data: Dict[str, Any]) -> float:
        """计算综合权重
        
        input_data 可包含:
        - base_weight: 基础权重
        - sound_level: 声音紧急度
        - visual_level: 视觉紧急度
        - task_priority: 任务优先级
        - emotion_intensity: 情绪强度
        
        Args:
            input_data: 包含各维度权重因子的字典
            
        Returns:
            归一化后的综合权重 (0-1)
        """
        try:
            # 获取各维度值，默认为 0
            base_weight = input_data.get("base_weight", self.config.base_weight)
            sound_level = input_data.get("sound_level", 0.0)
            visual_level = input_data.get("visual_level", 0.0)
            task_priority = input_data.get("task_priority", 0.0)
            emotion_intensity = input_data.get("emotion_intensity", 0.0)
            
            # 使用配置中的各因子权重进行加权计算
            # 基础权重直接参与
            weighted_sum = base_weight
            
            # 声音权重
            if sound_level > 0:
                weighted_sum += sound_level * self.config.sound_weight_factor
            
            # 视觉权重
            if visual_level > 0:
                weighted_sum += visual_level * self.config.visual_weight_factor
            
            # 任务优先级权重
            if task_priority > 0:
                weighted_sum += task_priority * self.config.task_priority_weight
            
            # 情绪权重
            if emotion_intensity > 0:
                weighted_sum += emotion_intensity * self.config.emotion_weight_factor
            
            # 使用配置中的 normalization_method 进行归一化
            normalization_method = self.config.normalization_method
            
            if normalization_method == "softmax":
                # 简化的 softmax 风格归一化（单值情况）
                import math
                # 将值映射到 0-1 范围，使用 sigmoid 风格的压缩
                normalized_weight = 1 / (1 + math.exp(-weighted_sum + 1))
            elif normalization_method == "min_max":
                # Min-Max 归一化（假设输入范围 0-5）
                normalized_weight = max(0.0, min(1.0, weighted_sum / 5.0))
            elif normalization_method == "z_score":
                # Z-Score 风格（简化为线性映射）
                normalized_weight = max(0.0, min(1.0, (weighted_sum + 2) / 4))
            else:
                # 默认使用简单截断
                normalized_weight = max(0.0, min(1.0, weighted_sum))
            
            self.logger.debug(f"权重计算: 输入={input_data}, 结果={normalized_weight:.4f}")
            
            return round(normalized_weight, 4)
            
        except Exception as e:
            self.logger.error(f"权重计算失败: {e}")
            # 失败时返回基础权重
            return self.config.base_weight
    
    async def calculate_memory_weights(
        self, 
        query: str, 
        memories: List[Dict[str, Any]],
        attention_level: float = 0.6
    ) -> List[Dict[str, Any]]:
        """计算记忆的注意力权重（委托给 MemoryAttentionScorer）
        
        Args:
            query: 查询文本
            memories: 记忆列表
            attention_level: 注意力水平 (0-1)，默认 0.6
            
        Returns:
            带有注意力权重的记忆列表
        """
        try:
            self.logger.info(f"开始计算记忆权重，查询: {query[:50]}..., 记忆数: {len(memories)}")
            
            # 委托给 MemoryAttentionScorer 进行评分
            results = await self.scorer.score_memories(query, memories, attention_level)
            
            self.logger.info(f"记忆权重计算完成，返回 {len(results)} 条")
            return results
            
        except Exception as e:
            self.logger.error(f"记忆权重计算失败: {e}")
            # 失败时返回原始记忆列表（不带权重）
            return memories
