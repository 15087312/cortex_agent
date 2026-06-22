"""
注意力控制工具

提供注意力水平调整功能
"""
from typing import Dict, Any, Optional
from infra.tool_manager import ToolRegistry

_manager = None
_analyzer = None


def set_attention_manager(manager) -> None:
    """设置注意力管理器（由集成思考器注入）"""
    global _manager
    _manager = manager


def _get_analyzer():
    """获取 AttentionAnalyzer 实例"""
    global _analyzer
    if _analyzer is None:
        try:
            from modules.attention import create_attention_analyzer
            _analyzer = create_attention_analyzer()
        except Exception:
            pass
    return _analyzer


@ToolRegistry.register(
    name="set_attention_level",
    description="设置任务重要性敏感度。调整关键词匹配的阈值，越高对紧急/任务关键词越敏感。",
    params={"level": "0-1的数值，0.6=默认，越高对紧急关键词越敏感"},
    source="security"
)
def set_attention_level(level: float = None, **kwargs) -> str:
    """设置注意力水平"""
    if level is None:
        level = kwargs.get("level")

    if level is None:
        return "[错误] 必须提供 level 参数"

    try:
        level = float(level)
        if not 0 <= level <= 1:
            return "[错误] level 必须在 0-1 之间"

        try:
            from config.settings import settings
            object.__setattr__(settings, "ATTENTION_FORCE_STATIC_LEVEL", level)
        except Exception:
            pass

        level_names = {
            0.0: "极度迟钝", 0.2: "迟钝", 0.4: "一般",
            0.6: "敏感", 0.8: "高度敏感", 1.0: "极度敏感",
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

    if _manager is not None:
        level = _manager.intensity_threshold
        level_names = {
            0.0: "极度迟钝", 0.2: "迟钝", 0.4: "一般",
            0.6: "敏感", 0.8: "高度敏感", 1.0: "极度敏感",
        }
        nearest = min(level_names.keys(), key=lambda x: abs(x - level))
        level_name = level_names.get(nearest, f"等级{nearest}")
        return f"[当前] 注意力水平: {level:.2f} ({level_name})"

    return "[信息] 使用默认注意力 0.6"


@ToolRegistry.register(
    name="get_attention_state",
    description="获取当前注意力状态（多维度分析）",
    params={},
    source="security"
)
def get_attention_state() -> str:
    """获取注意力状态"""
    analyzer = _get_analyzer()
    if analyzer is None:
        return "[信息] 注意力分析器未初始化"

    try:
        # 使用默认输入进行分析，返回当前状态
        result = analyzer.analyze(user_input="")
        vector = result.vector

        lines = [
            "【注意力状态】",
            f"任务重要性: {result.importance_score:.2f}/1.0",
            f"语义相关性: {vector.semantic:.2f}",
            f"时间衰减: {vector.temporal:.2f}",
            f"任务优先级: {vector.task:.2f}",
            f"情感强度: {vector.emotion:.2f}",
            f"模态权重: {vector.modality:.2f}",
            f"置信度: {vector.confidence:.2f}",
        ]
        if result.importance_reasons:
            lines.append(f"分析依据: {', '.join(result.importance_reasons)}")

        return "\n".join(lines)
    except Exception as e:
        return f"[错误] 获取注意力状态失败: {e}"
