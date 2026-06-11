"""
注意力引擎 V2

整合所有注意力组件，提供统一的注意力管理接口。
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
from utils.logger import setup_logger
from modules.attention.core.v2.attention_vector import AttentionVector, AttentionVectorFactory
from modules.attention.core.v2.cross_modal_fusion import CrossModalFusion, ModalityWeight
from modules.attention.core.v2.adaptive_decay import AdaptiveDecay, DecayConfig
from modules.attention.core.v2.resource_allocator import ResourceAllocator, ResourceBudget, AllocationResult
from modules.attention.core.v2.attention_explainer import AttentionExplainer

logger = setup_logger("attention_engine")


@dataclass
class AttentionState:
    """注意力状态"""
    current_vector: AttentionVector
    history: List[AttentionVector]
    allocation: Optional[AllocationResult]
    explanation: Optional[Dict[str, Any]]
    timestamp: str = datetime.now().isoformat()


class AttentionEngine:
    """注意力引擎 V2"""
    
    def __init__(
        self,
        fusion_strategy: str = "gated",
        decay_config: Optional[DecayConfig] = None,
        resource_budget: Optional[ResourceBudget] = None,
        max_history: int = 50,
    ):
        self.fusion = CrossModalFusion(strategy=fusion_strategy)
        self.decay = AdaptiveDecay(config=decay_config)
        self.resource_allocator = ResourceAllocator(total_budget=resource_budget)
        self.explainer = AttentionExplainer(max_history=max_history)
        
        self.current_vector = AttentionVector()
        self.history: List[AttentionVector] = []
        self.max_history = max_history
        
        self.logger = setup_logger("attention_engine")
        self.logger.info("AttentionEngine V2 初始化完成")
    
    def process_input(
        self,
        user_input: str,
        context: Optional[List[Dict]] = None,
        perception_events: Optional[List[Dict]] = None,
        task_type: str = "general",
        context_size: int = 0,
    ) -> AttentionState:
        """处理输入，生成注意力状态"""
        # 1. 创建输入向量
        input_vector = AttentionVectorFactory.from_text_importance(
            importance_score=0.5,  # 将由 AttentionCore 计算
            text=user_input
        )
        
        # 2. 收集感知事件向量
        modality_vectors = [input_vector]
        if perception_events:
            for event in perception_events:
                event_vector = AttentionVectorFactory.from_perception_event(
                    event_type=event.get("type", "system"),
                    confidence=event.get("confidence", 0.5),
                    urgency=event.get("urgency", 0.5)
                )
                modality_vectors.append(event_vector)
        
        # 3. 跨模态融合
        if len(modality_vectors) > 1:
            fused_vector = self.fusion.fuse(modality_vectors)
        else:
            fused_vector = input_vector
        
        # 4. 应用自适应衰减
        if self.history:
            time_elapsed = self._compute_time_elapsed()
            decayed_vector = self.decay.apply_decay(fused_vector, time_elapsed)
        else:
            decayed_vector = fused_vector
        
        # 5. 更新历史
        self.history.append(decayed_vector)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        
        # 6. 更新当前向量
        self.current_vector = decayed_vector
        
        # 7. 资源分配
        allocation = self.resource_allocator.allocate(
            attention_vector=decayed_vector,
            task_type=task_type,
            context_size=context_size
        )
        
        # 8. 生成解释
        explanation = self.explainer.explain_decision(
            attention_vector=decayed_vector,
            context={"user_input": user_input}
        )
        
        # 9. 记录决策
        self.explainer.record_decision(
            input_summary=user_input[:100],
            attention_vector=decayed_vector,
            factors={
                "semantic": decayed_vector.semantic,
                "temporal": decayed_vector.temporal,
                "task": decayed_vector.task,
            },
            reasoning_chain=explanation.get("reasoning_chain", []),
            outcome=f"分配{allocation.model_tier}模型，{allocation.token_budget} tokens",
            confidence=decayed_vector.confidence,
        )
        
        return AttentionState(
            current_vector=decayed_vector,
            history=self.history.copy(),
            allocation=allocation,
            explanation=explanation,
        )
    
    def process_memory_score(
        self,
        semantic_score: float,
        time_decay: float,
        importance: float
    ) -> AttentionVector:
        """处理记忆评分"""
        vector = AttentionVectorFactory.from_memory_score(
            semantic_score=semantic_score,
            time_decay=time_decay,
            importance=importance
        )
        
        return vector
    
    def fuse_vectors(
        self,
        vectors: List[AttentionVector],
        weights: Optional[ModalityWeight] = None
    ) -> AttentionVector:
        """融合多个向量"""
        return self.fusion.fuse(vectors, weights)
    
    def apply_decay_to_vector(
        self,
        vector: AttentionVector,
        time_elapsed: float
    ) -> AttentionVector:
        """对单个向量应用衰减"""
        return self.decay.apply_decay(vector, time_elapsed)
    
    def allocate_resources(
        self,
        vector: AttentionVector,
        task_type: str = "general",
        context_size: int = 0
    ) -> AllocationResult:
        """分配资源"""
        return self.resource_allocator.allocate(vector, task_type, context_size)
    
    def explain_current_state(self) -> Dict[str, Any]:
        """解释当前状态"""
        return self.explainer.explain_decision(self.current_vector)
    
    def get_state(self) -> Dict[str, Any]:
        """获取引擎状态"""
        return {
            "current_vector": self.current_vector.to_dict(),
            "history_length": len(self.history),
            "decay_state": self.decay.get_state(),
            "resource_stats": self.resource_allocator.get_allocation_stats(),
            "explanation_patterns": self.explainer.analyze_patterns(),
        }
    
    def reset(self):
        """重置引擎状态"""
        self.current_vector = AttentionVector()
        self.history.clear()
        self.resource_allocator.reset_budget()
        self.logger.info("AttentionEngine 已重置")
    
    def _compute_time_elapsed(self) -> float:
        """计算自上次更新以来的时间（秒）"""
        if not self.history:
            return 0.0
        
        # 简化实现：返回固定值
        return 60.0


def create_attention_engine(
    fusion_strategy: str = "gated",
    use_adaptive_decay: bool = True,
    resource_budget: Optional[ResourceBudget] = None,
) -> AttentionEngine:
    """创建注意力引擎实例"""
    decay_config = DecayConfig() if use_adaptive_decay else None
    
    return AttentionEngine(
        fusion_strategy=fusion_strategy,
        decay_config=decay_config,
        resource_budget=resource_budget,
    )