"""
注意力可解释性

记录和解释注意力决策的原因链，支持调试和审计。
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger
from modules.attention.core.v2.attention_vector import AttentionVector

logger = setup_logger("attention_explainer")


@dataclass
class AttentionDecisionRecord:
    """注意力决策记录"""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    input_summary: str = ""
    attention_vector: Optional[AttentionVector] = None
    factors: Dict[str, float] = field(default_factory=dict)
    reasoning_chain: List[str] = field(default_factory=list)
    outcome: str = ""
    confidence: float = 1.0


class AttentionExplainer:
    """注意力可解释性器"""
    
    def __init__(self, max_history: int = 100):
        self.decision_history: List[AttentionDecisionRecord] = []
        self.max_history = max_history
        self.logger = setup_logger("attention_explainer")
    
    def record_decision(
        self,
        input_summary: str,
        attention_vector: AttentionVector,
        factors: Dict[str, float],
        reasoning_chain: List[str],
        outcome: str,
        confidence: float = 1.0
    ) -> AttentionDecisionRecord:
        """记录注意力决策"""
        record = AttentionDecisionRecord(
            input_summary=input_summary,
            attention_vector=attention_vector,
            factors=factors,
            reasoning_chain=reasoning_chain,
            outcome=outcome,
            confidence=confidence,
        )
        
        self.decision_history.append(record)
        
        # 限制历史记录长度
        if len(self.decision_history) > self.max_history:
            self.decision_history = self.decision_history[-self.max_history:]
        
        self.logger.debug(f"记录注意力决策: {outcome}")
        return record
    
    def explain_decision(
        self,
        attention_vector: AttentionVector,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """解释注意力决策"""
        explanation = {
            "summary": self._generate_summary(attention_vector),
            "dimensions": self._explain_dimensions(attention_vector),
            "influencing_factors": self._identify_factors(attention_vector, context),
            "reasoning_chain": self._build_reasoning_chain(attention_vector, context),
            "recommendations": self._generate_recommendations(attention_vector),
        }
        
        return explanation
    
    def _generate_summary(self, vector: AttentionVector) -> str:
        """生成决策摘要"""
        dominant_dim = max(
            ["semantic", "temporal", "task", "emotion", "modality"],
            key=lambda d: getattr(vector, d)
        )
        
        dominant_value = getattr(vector, dominant_dim)
        
        if dominant_value >= 0.8:
            intensity = "非常强"
        elif dominant_value >= 0.6:
            intensity = "强"
        elif dominant_value >= 0.4:
            intensity = "中等"
        elif dominant_value >= 0.2:
            intensity = "弱"
        else:
            intensity = "非常弱"
        
        dim_names = {
            "semantic": "语义相关性",
            "temporal": "时间敏感性",
            "task": "任务重要性",
            "emotion": "情感强度",
            "modality": "模态权重",
        }
        
        return f"注意力主要集中在{dim_names[dominant_dim]}维度（{intensity}，{dominant_value:.2f}）"
    
    def _explain_dimensions(self, vector: AttentionVector) -> Dict[str, Any]:
        """解释各维度"""
        dimensions = {
            "semantic": {
                "value": vector.semantic,
                "interpretation": self._interpret_semantic(vector.semantic),
            },
            "temporal": {
                "value": vector.temporal,
                "interpretation": self._interpret_temporal(vector.temporal),
            },
            "task": {
                "value": vector.task,
                "interpretation": self._interpret_task(vector.task),
            },
            "emotion": {
                "value": vector.emotion,
                "interpretation": self._interpret_emotion(vector.emotion),
            },
            "modality": {
                "value": vector.modality,
                "interpretation": self._interpret_modality(vector.modality),
            },
        }
        
        return dimensions
    
    def _interpret_semantic(self, value: float) -> str:
        if value >= 0.8:
            return "输入与当前上下文高度相关"
        elif value >= 0.5:
            return "输入与当前上下文中度相关"
        else:
            return "输入与当前上下文相关性较低"
    
    def _interpret_temporal(self, value: float) -> str:
        if value >= 0.8:
            return "时间敏感性高，需要快速响应"
        elif value >= 0.5:
            return "时间敏感性中等"
        else:
            return "时间敏感性低，可延后处理"
    
    def _interpret_task(self, value: float) -> str:
        if value >= 0.8:
            return "任务优先级高，需要优先处理"
        elif value >= 0.5:
            return "任务优先级中等"
        else:
            return "任务优先级较低"
    
    def _interpret_emotion(self, value: float) -> str:
        if value >= 0.8:
            return "情感强度高，可能涉及紧急或重要事项"
        elif value >= 0.5:
            return "情感强度中等"
        else:
            return "情感强度低，较为平静"
    
    def _interpret_modality(self, value: float) -> str:
        if value >= 0.8:
            return "模态信息丰富，可能需要多模态处理"
        elif value >= 0.5:
            return "模态信息中等"
        else:
            return "模态信息较少"
    
    def _identify_factors(
        self,
        vector: AttentionVector,
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """识别影响因素"""
        factors = []
        
        # 语义因素
        if vector.semantic >= 0.7:
            factors.append({
                "name": "语义相关性",
                "impact": "high",
                "value": vector.semantic,
                "description": "输入与当前上下文高度相关",
            })
        
        # 时间因素
        if vector.temporal >= 0.7:
            factors.append({
                "name": "时间敏感性",
                "impact": "high",
                "value": vector.temporal,
                "description": "需要快速响应",
            })
        
        # 任务因素
        if vector.task >= 0.7:
            factors.append({
                "name": "任务重要性",
                "impact": "high",
                "value": vector.task,
                "description": "任务优先级高",
            })
        
        # 情感因素
        if vector.emotion >= 0.5:
            factors.append({
                "name": "情感强度",
                "impact": "medium",
                "value": vector.emotion,
                "description": "涉及情感内容",
            })
        
        # 上下文因素
        if context:
            if "urgency_keywords" in context:
                factors.append({
                    "name": "紧急关键词",
                    "impact": "high",
                    "value": 1.0,
                    "description": f"检测到紧急关键词: {context['urgency_keywords'][:3]}",
                })
        
        return factors
    
    def _build_reasoning_chain(
        self,
        vector: AttentionVector,
        context: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """构建推理链"""
        chain = []
        
        # 1. 输入分析
        chain.append("分析输入内容和上下文")
        
        # 2. 维度评估
        if vector.semantic >= 0.6:
            chain.append("评估语义相关性：高")
        if vector.temporal >= 0.6:
            chain.append("评估时间敏感性：高")
        if vector.task >= 0.6:
            chain.append("评估任务重要性：高")
        
        # 3. 综合判断
        if vector.to_scalar() >= 0.7:
            chain.append("综合判断：需要高注意力分配")
        elif vector.to_scalar() >= 0.4:
            chain.append("综合判断：中等注意力分配")
        else:
            chain.append("综合判断：低注意力分配")
        
        # 4. 资源分配建议
        if vector.to_scalar() >= 0.7:
            chain.append("建议：使用大模型，分配更多token")
        elif vector.to_scalar() >= 0.4:
            chain.append("建议：使用中等模型，适中token")
        else:
            chain.append("建议：使用小模型，节省token")
        
        return chain
    
    def _generate_recommendations(self, vector: AttentionVector) -> List[str]:
        """生成建议"""
        recommendations = []
        
        if vector.semantic >= 0.7:
            recommendations.append("优先处理语义相关的内容")
        
        if vector.temporal >= 0.7:
            recommendations.append("加速响应时间，减少延迟")
        
        if vector.task >= 0.7:
            recommendations.append("分配更多计算资源")
        
        if vector.emotion >= 0.5:
            recommendations.append("注意情感表达，保持同理心")
        
        if vector.to_scalar() < 0.3:
            recommendations.append("考虑降低处理优先级，节省资源")
        
        return recommendations
    
    def get_decision_history(
        self,
        limit: int = 10,
        min_confidence: float = 0.5
    ) -> List[Dict[str, Any]]:
        """获取决策历史"""
        filtered = [
            record for record in self.decision_history
            if record.confidence >= min_confidence
        ]
        
        recent = filtered[-limit:] if len(filtered) > limit else filtered
        
        return [
            {
                "timestamp": record.timestamp,
                "input_summary": record.input_summary,
                "outcome": record.outcome,
                "confidence": record.confidence,
                "factors_count": len(record.factors),
            }
            for record in recent
        ]
    
    def analyze_patterns(self) -> Dict[str, Any]:
        """分析决策模式"""
        if not self.decision_history:
            return {"total_decisions": 0}
        
        # 分析各维度的平均值
        avg_dimensions = {
            "semantic": 0.0,
            "temporal": 0.0,
            "task": 0.0,
            "emotion": 0.0,
            "modality": 0.0,
        }
        
        for record in self.decision_history:
            if record.attention_vector:
                for dim in avg_dimensions:
                    avg_dimensions[dim] += getattr(record.attention_vector, dim)
        
        n = len(self.decision_history)
        for dim in avg_dimensions:
            avg_dimensions[dim] /= n
        
        # 分析常见因素
        factor_counts = {}
        for record in self.decision_history:
            for factor in record.factors:
                name = factor.get("name", "unknown")
                factor_counts[name] = factor_counts.get(name, 0) + 1
        
        return {
            "total_decisions": n,
            "avg_dimensions": avg_dimensions,
            "common_factors": factor_counts,
            "avg_confidence": sum(r.confidence for r in self.decision_history) / n,
        }