"""
注意力控制工具

提供注意力水平调整功能
支持V1（标量）和V2（多维度向量）两种模式
"""
from typing import Dict, Any, Optional
from infra.tool_manager import ToolRegistry

_attention_manager = None
_attention_engine = None  # V2引擎


def set_attention_manager(manager) -> None:
    """设置注意力管理器（由集成思考器注入）"""
    global _attention_manager
    _attention_manager = manager


def _get_attention_engine():
    """获取V2注意力引擎"""
    global _attention_engine
    if _attention_engine is None:
        try:
            from modules.attention.core.v2.attention_engine import create_attention_engine
            _attention_engine = create_attention_engine()
        except Exception:
            pass
    return _attention_engine


@ToolRegistry.register(
    name="set_attention_level",
    description="设置任务重要性敏感度。调整关键词匹配的阈值，越高对紧急/任务关键词越敏感。",
    params={"level": "0-1的数值，0.6=默认，越高对紧急关键词越敏感"},
    source="security"
)
def set_attention_level(level: float = None, **kwargs) -> str:
    """
    设置注意力水平

    Args:
        level: 0-1的数值，越高越敏感

    Returns:
        设置结果
    """
    if level is None:
        level = kwargs.get("level")

    if level is None:
        return "[错误] 必须提供 level 参数"

    try:
        level = float(level)
        if not 0 <= level <= 1:
            return "[错误] level 必须在 0-1 之间"

        # 实际写入运行时配置，影响 AttentionCore 的行为
        try:
            from config.settings import settings
            object.__setattr__(settings, "ATTENTION_FORCE_STATIC_LEVEL", level)
        except Exception:
            pass

        level_names = {
            0.0: "极度迟钝",
            0.2: "迟钝",
            0.4: "一般",
            0.6: "敏感",
            0.8: "高度敏感",
            1.0: "极度敏感"
        }

        nearest = min(level_names.keys(), key=lambda x: abs(x - level))
        level_name = level_names.get(nearest, f"等级{nearest}")

        return f"[成功] 注意力水平已调整为 {level:.2f} ({level_name})"
    except (TypeError, ValueError):
        return "[错误] level 必须是 0-1 的数值"


@ToolRegistry.register(
    name="get_attention_level",
    description="获取当前注意力水平设置",
    source="security"
)
def get_attention_level() -> str:
    """获取当前注意力水平"""
    try:
        from config.settings import settings
        level = getattr(settings, "ATTENTION_FORCE_STATIC_LEVEL", None)
        if level is not None:
            level_names = {
                0.0: "极度迟钝", 0.2: "迟钝", 0.4: "一般",
                0.6: "敏感", 0.8: "高度敏感", 1.0: "极度敏感",
            }
            nearest = min(level_names.keys(), key=lambda x: abs(x - level))
            level_name = level_names.get(nearest, f"等级{nearest}")
            return f"[当前] 注意力水平: {level:.2f} ({level_name})"
    except Exception:
        pass

    if _attention_manager is not None:
        level = _attention_manager.intensity_threshold
        level_names = {
            0.0: "极度迟钝", 0.2: "迟钝", 0.4: "一般",
            0.6: "敏感", 0.8: "高度敏感", 1.0: "极度敏感",
        }
        nearest = min(level_names.keys(), key=lambda x: abs(x - level))
        level_name = level_names.get(nearest, f"等级{nearest}")
        return f"[当前] 注意力水平: {level:.2f} ({level_name})"

    return "[信息] 使用默认注意力 0.6"


# ==============================================================================
# V2 注意力工具
# ==============================================================================

@ToolRegistry.register(
    name="get_attention_state",
    description="获取当前注意力状态（V2多维度分析）",
    params={},
    source="security"
)
def get_attention_state() -> str:
    """获取V2注意力状态"""
    engine = _get_attention_engine()
    if engine is None:
        return "[信息] V2注意力引擎未初始化"
    
    try:
        state = engine.get_state()
        vector = state.get("current_vector", {})
        
        lines = [
            "【注意力状态 V2】",
            f"语义相关性: {vector.get('semantic', 0):.2f}",
            f"时间敏感性: {vector.get('temporal', 0):.2f}",
            f"任务重要性: {vector.get('task', 0):.2f}",
            f"情感强度: {vector.get('emotion', 0):.2f}",
            f"模态权重: {vector.get('modality', 0):.2f}",
            f"来源: {vector.get('source', 'unknown')}",
            f"置信度: {vector.get('confidence', 1):.2f}",
        ]
        
        # 资源分配信息
        resource_stats = state.get("resource_stats", {})
        if resource_stats:
            lines.append("\n【资源分配】")
            lines.append(f"总分配次数: {resource_stats.get('total_allocations', 0)}")
            tier_dist = resource_stats.get("tier_distribution", {})
            if tier_dist:
                lines.append(f"模型使用: {tier_dist}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"[错误] 获取注意力状态失败: {e}"


@ToolRegistry.register(
    name="set_task_stage",
    description="设置当前任务阶段（影响注意力衰减策略）",
    params={"stage": "任务阶段: exploration/focus/execution/review"},
    source="security"
)
def set_task_stage(stage: str = "exploration") -> str:
    """设置任务阶段"""
    engine = _get_attention_engine()
    if engine is None:
        return "[信息] V2注意力引擎未初始化"
    
    valid_stages = ["exploration", "focus", "execution", "review"]
    if stage not in valid_stages:
        return f"[错误] 无效的阶段: {stage}。支持: {', '.join(valid_stages)}"
    
    try:
        engine.decay.set_stage(stage)
        return f"[成功] 任务阶段已设置为: {stage}"
    except Exception as e:
        return f"[错误] 设置任务阶段失败: {e}"


@ToolRegistry.register(
    name="set_cognitive_load",
    description="设置认知负荷（影响注意力衰减速率）",
    params={"load": "认知负荷 0-1，0=空闲，1=满负荷"},
    source="security"
)
def set_cognitive_load(load: float = 0.5) -> str:
    """设置认知负荷"""
    engine = _get_attention_engine()
    if engine is None:
        return "[信息] V2注意力引擎未初始化"
    
    try:
        load = float(load)
        if not 0 <= load <= 1:
            return "[错误] load 必须在 0-1 之间"
        
        engine.decay.set_cognitive_load(load)
        return f"[成功] 认知负荷已设置为: {load:.2f}"
    except (TypeError, ValueError):
        return "[错误] load 必须是数值"


@ToolRegistry.register(
    name="get_attention_explanation",
    description="获取注意力决策的解释（推理链、影响因素）",
    params={},
    source="security"
)
def get_attention_explanation() -> str:
    """获取注意力决策解释"""
    engine = _get_attention_engine()
    if engine is None:
        return "[信息] V2注意力引擎未初始化"
    
    try:
        explanation = engine.explain_current_state()
        
        lines = ["【注意力决策解释】"]
        
        summary = explanation.get("summary", "")
        if summary:
            lines.append(f"摘要: {summary}")
        
        reasoning_chain = explanation.get("reasoning_chain", [])
        if reasoning_chain:
            lines.append("\n推理链:")
            for i, step in enumerate(reasoning_chain, 1):
                lines.append(f"  {i}. {step}")
        
        recommendations = explanation.get("recommendations", [])
        if recommendations:
            lines.append("\n建议:")
            for rec in recommendations:
                lines.append(f"  - {rec}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"[错误] 获取注意力解释失败: {e}"
