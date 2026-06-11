"""
注意力系统 V2 演示

展示新注意力模块的核心功能：
1. 多维度注意力向量
2. 跨模态融合
3. 自适应衰减
4. 资源分配
5. 可解释性
"""
import asyncio
from modules.attention.core.v2 import (
    AttentionVector,
    AttentionVectorFactory,
    CrossModalFusion,
    AdaptiveDecay,
    ResourceAllocator,
    AttentionExplainer,
    AttentionEngine,
    DecayConfig,
    ResourceBudget,
)


def demo_attention_vector():
    """演示注意力向量"""
    print("=" * 60)
    print("1. 多维度注意力向量")
    print("=" * 60)
    
    # 创建向量
    vector = AttentionVector(
        semantic=0.8,
        temporal=0.6,
        task=0.9,
        emotion=0.3,
        modality=0.7,
    )
    
    print(f"向量: {vector.to_dict()}")
    print(f"模长: {vector.magnitude():.3f}")
    print(f"标量: {vector.to_scalar():.3f}")
    
    # 归一化
    normalized = vector.normalize()
    print(f"归一化: {normalized.to_dict()}")
    
    # 从文本创建
    text_vector = AttentionVectorFactory.from_text_importance(
        importance_score=0.7,
        text="紧急：系统故障，需要立即修复"
    )
    print(f"文本向量: {text_vector.to_dict()}")
    print()


def demo_cross_modal_fusion():
    """演示跨模态融合"""
    print("=" * 60)
    print("2. 跨模态注意力融合")
    print("=" * 60)
    
    # 创建不同模态的向量
    text_vec = AttentionVector(
        semantic=0.8,
        temporal=0.5,
        task=0.7,
        emotion=0.2,
        modality=0.9,
        source="text",
    )
    
    visual_vec = AttentionVector(
        semantic=0.6,
        temporal=0.7,
        task=0.5,
        emotion=0.4,
        modality=0.8,
        source="visual",
    )
    
    audio_vec = AttentionVector(
        semantic=0.5,
        temporal=0.6,
        task=0.4,
        emotion=0.6,
        modality=0.7,
        source="audio",
    )
    
    # 加权平均融合
    fusion_avg = CrossModalFusion(strategy="weighted_avg")
    fused_avg = fusion_avg.fuse([text_vec, visual_vec, audio_vec])
    print(f"加权平均融合: {fused_avg.to_dict()}")
    
    # 门控融合
    fusion_gated = CrossModalFusion(strategy="gated")
    fused_gated = fusion_gated.fuse([text_vec, visual_vec, audio_vec])
    print(f"门控融合: {fused_gated.to_dict()}")
    
    # 注意力池化
    fusion_pooling = CrossModalFusion(strategy="pooling")
    fused_pooling = fusion_pooling.fuse([text_vec, visual_vec, audio_vec])
    print(f"注意力池化: {fused_pooling.to_dict()}")
    print()


def demo_adaptive_decay():
    """演示自适应衰减"""
    print("=" * 60)
    print("3. 自适应注意力衰减")
    print("=" * 60)
    
    # 创建衰减器
    decay = AdaptiveDecay()
    
    # 设置任务阶段
    decay.set_stage("exploration")
    print(f"当前阶段: {decay.current_stage}")
    
    # 创建向量
    vector = AttentionVector(
        semantic=0.8,
        temporal=0.7,
        task=0.6,
        emotion=0.3,
        modality=0.5,
    )
    
    # 应用衰减
    decayed = decay.apply_decay(vector, time_elapsed=120.0)
    print(f"原始向量: {vector.to_dict()}")
    print(f"衰减后: {decayed.to_dict()}")
    
    # 检测注意力转移
    shift = decay.detect_attention_shift(decayed, vector)
    print(f"注意力转移: {shift}")
    print()


def demo_resource_allocator():
    """演示资源分配"""
    print("=" * 60)
    print("4. 注意力驱动的资源分配")
    print("=" * 60)
    
    # 创建分配器
    allocator = ResourceAllocator()
    
    # 高优先级任务
    high_priority_vector = AttentionVector(
        semantic=0.9,
        temporal=0.8,
        task=0.9,
        emotion=0.5,
        modality=0.7,
    )
    
    allocation_high = allocator.allocate(
        high_priority_vector,
        task_type="critical",
        context_size=2000
    )
    
    print("高优先级任务分配:")
    print(f"  模型层级: {allocation_high.model_tier}")
    print(f"  Token预算: {allocation_high.token_budget}")
    print(f"  优先级: {allocation_high.priority}")
    print(f"  理由: {allocation_high.rationale}")
    
    # 低优先级任务
    low_priority_vector = AttentionVector(
        semantic=0.3,
        temporal=0.2,
        task=0.2,
        emotion=0.1,
        modality=0.4,
    )
    
    allocation_low = allocator.allocate(
        low_priority_vector,
        task_type="background"
    )
    
    print("\n低优先级任务分配:")
    print(f"  模型层级: {allocation_low.model_tier}")
    print(f"  Token预算: {allocation_low.token_budget}")
    print(f"  优先级: {allocation_low.priority}")
    print(f"  理由: {allocation_low.rationale}")
    print()


def demo_attention_explainer():
    """演示可解释性"""
    print("=" * 60)
    print("5. 注意力可解释性")
    print("=" * 60)
    
    # 创建解释器
    explainer = AttentionExplainer()
    
    # 创建向量
    vector = AttentionVector(
        semantic=0.8,
        temporal=0.6,
        task=0.9,
        emotion=0.4,
        modality=0.7,
    )
    
    # 生成解释
    explanation = explainer.explain_decision(vector)
    
    print("决策解释:")
    print(f"  摘要: {explanation['summary']}")
    print(f"  维度解释:")
    for dim, info in explanation['dimensions'].items():
        print(f"    {dim}: {info['value']:.2f} - {info['interpretation']}")
    print(f"  推理链:")
    for step in explanation['reasoning_chain']:
        print(f"    - {step}")
    print(f"  建议:")
    for rec in explanation['recommendations']:
        print(f"    - {rec}")
    print()


def demo_attention_engine():
    """演示完整引擎"""
    print("=" * 60)
    print("6. 完整注意力引擎")
    print("=" * 60)
    
    # 创建引擎
    engine = AttentionEngine(
        fusion_strategy="gated",
        decay_config=DecayConfig(),
    )
    
    # 处理输入
    state = engine.process_input(
        user_input="紧急：系统出现严重故障，需要立即修复数据库连接问题",
        context=[{"role": "user", "content": "之前的问题解决了吗？"}],
        perception_events=[
            {"type": "visual", "confidence": 0.8, "urgency": 0.6},
            {"type": "audio", "confidence": 0.7, "urgency": 0.5},
        ],
        task_type="critical",
        context_size=1500,
    )
    
    print("处理结果:")
    print(f"  当前向量: {state.current_vector.to_dict()}")
    print(f"  历史长度: {len(state.history)}")
    print(f"  资源分配:")
    if state.allocation:
        print(f"    模型: {state.allocation.model_tier}")
        print(f"    Token: {state.allocation.token_budget}")
        print(f"    优先级: {state.allocation.priority}")
    print(f"  解释摘要: {state.explanation.get('summary', 'N/A')}")
    
    # 获取引擎状态
    engine_state = engine.get_state()
    print(f"\n引擎状态:")
    print(f"  历史长度: {engine_state['history_length']}")
    print(f"  资源统计: {engine_state['resource_stats']}")
    print()


async def main():
    """主函数"""
    print("注意力系统 V2 演示")
    print("=" * 60)
    
    demo_attention_vector()
    demo_cross_modal_fusion()
    demo_adaptive_decay()
    demo_resource_allocator()
    demo_attention_explainer()
    demo_attention_engine()
    
    print("=" * 60)
    print("演示完成！")


if __name__ == "__main__":
    asyncio.run(main())