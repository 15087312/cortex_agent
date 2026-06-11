"""
注意力驱动的资源分配

根据注意力状态动态分配计算资源：
1. Token预算分配
2. 模型选择
3. 并行度控制
4. 缓存策略
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from utils.logger import setup_logger
from modules.attention.core.v2.attention_vector import AttentionVector

logger = setup_logger("resource_allocator")


@dataclass
class ResourceBudget:
    """资源预算"""
    # Token预算
    total_tokens: int = 4096
    allocated_tokens: int = 0
    
    # 计算预算
    max_latency_ms: float = 5000.0
    allocated_latency_ms: float = 0.0
    
    # 内存预算
    max_cache_size: int = 1000
    allocated_cache: int = 0
    
    # 并行度
    max_parallel: int = 3
    current_parallel: int = 1


@dataclass
class AllocationResult:
    """分配结果"""
    token_budget: int
    latency_budget_ms: float
    cache_budget: int
    parallelism: int
    model_tier: str  # "large", "medium", "small", "lite"
    priority: int    # 1-10
    rationale: str


class ResourceAllocator:
    """注意力驱动的资源分配器"""
    
    def __init__(self, total_budget: Optional[ResourceBudget] = None):
        self.total_budget = total_budget or ResourceBudget()
        self.allocation_history: List[AllocationResult] = []
        self.logger = setup_logger("resource_allocator")
        
        # 配置参数
        self.model_tiers = {
            "large": {"min_tokens": 2048, "max_latency_ms": 10000, "cost_factor": 1.0},
            "medium": {"min_tokens": 1024, "max_latency_ms": 5000, "cost_factor": 0.5},
            "small": {"min_tokens": 512, "max_latency_ms": 2000, "cost_factor": 0.2},
            "lite": {"min_tokens": 256, "max_latency_ms": 1000, "cost_factor": 0.1},
        }
    
    def allocate(
        self,
        attention_vector: AttentionVector,
        task_type: str = "general",
        context_size: int = 0
    ) -> AllocationResult:
        """根据注意力状态分配资源"""
        # 计算优先级
        priority = self._compute_priority(attention_vector, task_type)
        
        # 选择模型层级
        model_tier = self._select_model_tier(attention_vector, priority)
        
        # 分配Token预算
        token_budget = self._allocate_tokens(attention_vector, model_tier, context_size)
        
        # 分配延迟预算
        latency_budget = self._allocate_latency(attention_vector, model_tier)
        
        # 分配缓存预算
        cache_budget = self._allocate_cache(attention_vector)
        
        # 分配并行度
        parallelism = self._allocate_parallelism(attention_vector, task_type)
        
        # 生成分配理由
        rationale = self._generate_rationale(
            attention_vector, priority, model_tier, token_budget
        )
        
        result = AllocationResult(
            token_budget=token_budget,
            latency_budget_ms=latency_budget,
            cache_budget=cache_budget,
            parallelism=parallelism,
            model_tier=model_tier,
            priority=priority,
            rationale=rationale,
        )
        
        self.allocation_history.append(result)
        self.logger.debug(f"资源分配完成: {rationale}")
        
        return result
    
    def _compute_priority(
        self,
        vector: AttentionVector,
        task_type: str
    ) -> int:
        """计算任务优先级 (1-10)"""
        # 基础优先级（基于任务维度）
        base_priority = int(vector.task * 10)
        
        # 任务类型调整
        type_adjustments = {
            "critical": 3,
            "urgent": 2,
            "important": 1,
            "normal": 0,
            "background": -1,
            "low": -2,
        }
        adjustment = type_adjustments.get(task_type, 0)
        
        # 情感调整（高情感强度增加优先级）
        emotion_adjustment = int(vector.emotion * 2)
        
        priority = base_priority + adjustment + emotion_adjustment
        
        return max(1, min(10, priority))
    
    def _select_model_tier(
        self,
        vector: AttentionVector,
        priority: int
    ) -> str:
        """选择模型层级"""
        # 高优先级或高语义复杂度使用大模型
        if priority >= 8 or vector.semantic >= 0.8:
            return "large"
        
        # 中等优先级使用中等模型
        if priority >= 5 or vector.semantic >= 0.5:
            return "medium"
        
        # 低优先级或简单任务使用小模型
        if priority >= 3:
            return "small"
        
        # 最低优先级使用轻量模型
        return "lite"
    
    def _allocate_tokens(
        self,
        vector: AttentionVector,
        model_tier: str,
        context_size: int
    ) -> int:
        """分配Token预算"""
        tier_config = self.model_tiers[model_tier]
        base_tokens = tier_config["min_tokens"]
        
        # 根据语义复杂度调整
        semantic_factor = 1.0 + (vector.semantic * 0.5)
        
        # 根据上下文大小调整
        context_factor = 1.0 + (context_size / 1000)
        
        # 计算分配的Token
        allocated = int(base_tokens * semantic_factor * context_factor)
        
        # 确保最低分配（至少分配base_tokens的50%）
        min_allocation = int(base_tokens * 0.5)
        allocated = max(allocated, min_allocation)
        
        # 限制在总预算内
        max_available = self.total_budget.total_tokens - self.total_budget.allocated_tokens
        allocated = min(allocated, max_available)
        
        # 更新已分配预算
        self.total_budget.allocated_tokens += allocated
        
        return allocated
    
    def _allocate_latency(
        self,
        vector: AttentionVector,
        model_tier: str
    ) -> float:
        """分配延迟预算"""
        tier_config = self.model_tiers[model_tier]
        base_latency = tier_config["max_latency_ms"]
        
        # 根据时间维度调整（时间敏感任务需要更低延迟）
        time_factor = 1.0 - (vector.temporal * 0.3)
        
        allocated = base_latency * time_factor
        
        return allocated
    
    def _allocate_cache(self, vector: AttentionVector) -> int:
        """分配缓存预算"""
        base_cache = 100
        
        # 根据模态权重调整（视觉/音频需要更多缓存）
        modality_factor = 1.0 + (vector.modality * 0.5)
        
        allocated = int(base_cache * modality_factor)
        
        return allocated
    
    def _allocate_parallelism(
        self,
        vector: AttentionVector,
        task_type: str
    ) -> int:
        """分配并行度"""
        # 基础并行度
        base_parallel = 1
        
        # 根据任务类型调整
        if task_type in ["parallel", "batch"]:
            base_parallel = 2
        
        # 根据注意力状态调整
        if vector.task >= 0.8 and vector.semantic >= 0.7:
            base_parallel = min(3, base_parallel + 1)
        
        return min(base_parallel, self.total_budget.max_parallel)
    
    def _generate_rationale(
        self,
        vector: AttentionVector,
        priority: int,
        model_tier: str,
        token_budget: int
    ) -> str:
        """生成分配理由"""
        reasons = []
        
        if priority >= 8:
            reasons.append("高优先级任务")
        elif priority >= 5:
            reasons.append("中等优先级任务")
        else:
            reasons.append("低优先级任务")
        
        if vector.semantic >= 0.7:
            reasons.append("高语义复杂度")
        
        if vector.temporal >= 0.7:
            reasons.append("时间敏感")
        
        if vector.emotion >= 0.5:
            reasons.append("高情感强度")
        
        reasons.append(f"使用{model_tier}模型")
        reasons.append(f"分配{token_budget} tokens")
        
        return "; ".join(reasons)
    
    def reset_budget(self):
        """重置预算"""
        self.total_budget.allocated_tokens = 0
        self.total_budget.allocated_latency_ms = 0.0
        self.total_budget.allocated_cache = 0
        self.total_budget.current_parallel = 1
    
    def get_allocation_stats(self) -> Dict[str, Any]:
        """获取分配统计"""
        if not self.allocation_history:
            return {"total_allocations": 0}
        
        tier_counts = {}
        for result in self.allocation_history:
            tier_counts[result.model_tier] = tier_counts.get(result.model_tier, 0) + 1
        
        avg_tokens = sum(r.token_budget for r in self.allocation_history) / len(self.allocation_history)
        avg_priority = sum(r.priority for r in self.allocation_history) / len(self.allocation_history)
        
        return {
            "total_allocations": len(self.allocation_history),
            "tier_distribution": tier_counts,
            "avg_tokens": avg_tokens,
            "avg_priority": avg_priority,
            "current_budget": {
                "total_tokens": self.total_budget.total_tokens,
                "allocated_tokens": self.total_budget.allocated_tokens,
                "remaining_tokens": self.total_budget.total_tokens - self.total_budget.allocated_tokens,
            }
        }