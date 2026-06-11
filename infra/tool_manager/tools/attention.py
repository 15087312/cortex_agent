"""
注意力控制工具

提供注意力水平调整功能
"""
from typing import Dict, Any
from infra.tool_manager import ToolRegistry

_attention_manager = None


def set_attention_manager(manager) -> None:
    """设置注意力管理器（由集成思考器注入）"""
    global _attention_manager
    _attention_manager = manager


@ToolRegistry.register(
    name="set_attention_level",
    description="设置注意力水平（感知强度阈值），控制对环境变化的敏感程度",
    params={"level": "0-1的数值，越高越敏感"},
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
