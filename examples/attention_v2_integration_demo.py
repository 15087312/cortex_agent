"""
注意力V2集成演示

展示如何使用V2注意力系统
"""
import asyncio
from modules.attention.core.v2 import (
    AttentionEngine,
    AttentionVector,
    create_attention_engine,
)


def demo_v2_basic():
    """演示V2基本使用"""
    print("=" * 60)
    print("V2注意力系统 - 基本使用")
    print("=" * 60)
    
    # 创建引擎
    engine = create_attention_engine()
    
    # 处理输入
    state = engine.process_input(
        user_input="紧急：系统出现严重故障，需要立即修复数据库连接问题",
        context=[{"role": "user", "content": "之前的问题解决了吗？"}],
        perception_events=[
            {"type": "visual", "confidence": 0.8, "urgency": 0.6},
        ],
        task_type="critical",
        context_size=1500,
    )
    
    print("\n【处理结果】")
    print(f"注意力向量: {state.current_vector.to_dict()}")
    print(f"标量值: {state.current_vector.to_scalar():.2f}")
    
    if state.allocation:
        print(f"\n【资源分配】")
        print(f"模型层级: {state.allocation.model_tier}")
        print(f"Token预算: {state.allocation.token_budget}")
        print(f"优先级: {state.allocation.priority}")
    
    print(f"\n【决策解释】")
    if state.explanation:
        print(f"摘要: {state.explanation.get('summary', 'N/A')}")
        reasoning = state.explanation.get("reasoning_chain", [])
        if reasoning:
            print("推理链:")
            for step in reasoning:
                print(f"  - {step}")


def demo_v2_interface():
    """演示V2适配器"""
    print("\n" + "=" * 60)
    print("V2注意力适配器")
    print("=" * 60)
    
    from modules.attention.interface import AttentionV2Adapter
    
    # 创建V2适配器
    adapter = AttentionV2Adapter()
    
    # 使用V1接口调用
    decision = adapter.analyze(
        user_input="学习一下如何使用chrome搜索",
        context=[],
        short_term_memory=[],
    )
    
    print(f"\n【AttentionDecision】")
    print(f"importance_score: {decision.importance_score:.2f}")
    print(f"attention_level: {decision.attention_level:.2f}")
    print(f"has_attention_vector: {decision.attention_vector is not None}")
    print(f"has_allocation: {decision.allocation is not None}")
    
    if decision.attention_vector:
        print(f"\n【AttentionVector】")
        vec = decision.attention_vector
        print(f"semantic: {vec.semantic:.2f}")
        print(f"temporal: {vec.temporal:.2f}")
        print(f"task: {vec.task:.2f}")
        print(f"emotion: {vec.emotion:.2f}")
        print(f"modality: {vec.modality:.2f}")


def demo_v2_tools():
    """演示V2工具"""
    print("\n" + "=" * 60)
    print("V2注意力工具")
    print("=" * 60)
    
    from infra.tool_manager.tools.attention import (
        get_attention_state,
        set_task_stage,
        set_cognitive_load,
        get_attention_explanation,
    )
    
    # 先处理一个输入以生成状态
    engine = create_attention_engine()
    engine.process_input(
        user_input="测试输入",
        task_type="normal",
    )
    
    print("\n【get_attention_state】")
    result = get_attention_state()
    print(result)
    
    print("\n【set_task_stage】")
    result = set_task_stage("focus")
    print(result)
    
    print("\n【set_cognitive_load】")
    result = set_cognitive_load(0.7)
    print(result)
    
    print("\n【get_attention_explanation】")
    result = get_attention_explanation()
    print(result)


def demo_memory_scoring():
    """演示V2记忆打分"""
    print("\n" + "=" * 60)
    print("V2记忆打分")
    print("=" * 60)
    
    engine = create_attention_engine()
    
    # 模拟记忆
    memories = [
        {"content": "用户之前问过数据库连接问题", "importance": 0.8},
        {"content": "系统配置文件位置", "importance": 0.5},
        {"content": "紧急：服务器宕机", "importance": 0.9},
    ]
    
    # 处理输入
    state = engine.process_input(
        user_input="数据库连接失败怎么办",
        task_type="urgent",
    )
    
    print(f"\n当前注意力向量: {state.current_vector.to_dict()}")
    
    # 使用注意力向量对记忆评分
    scored = []
    for mem in memories:
        # 简单模拟：基于语义相关性评分
        score = state.current_vector.semantic * mem["importance"]
        scored.append({**mem, "attention_score": score})
    
    # 按分数排序
    scored.sort(key=lambda x: x["attention_score"], reverse=True)
    
    print("\n【记忆打分结果】")
    for item in scored:
        print(f"  [{item['attention_score']:.2f}] {item['content']}")


def main():
    """主函数"""
    print("注意力V2集成演示")
    print("=" * 60)
    
    demo_v2_basic()
    demo_v2_interface()
    demo_v2_tools()
    demo_memory_scoring()
    
    print("\n" + "=" * 60)
    print("演示完成！")


if __name__ == "__main__":
    main()