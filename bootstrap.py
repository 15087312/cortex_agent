"""应用装配层 — 向 infra 业务能力端口注册 modules 实现。

依赖方向：modules → infra（单向、正常方向）。
业务能力（感知/记忆/技能/安全门等）由本模块在应用启动时统一注册到
infra.tool_manager.service_registry，infra 工具层通过 get_capability 获取，
不再直接 import modules，从而消除 infra → modules 的逆向依赖环。

各应用入口（api/main.py、cortex/main.py、frontend/main.py 等）启动时调用
register_business_capabilities()。
"""
from __future__ import annotations

from utils.logger import get_logger

logger = get_logger("app_bootstrap")

# 注册失败记录：_register_all 结束时统一告警，避免静默缺失（DI 启动期校验）
_REGISTER_FAILURES: list = []


def _safe_register(name: str, loader) -> None:
    """单个能力注册失败不影响其余能力（避免一个模块初始化异常拖垮启动）。"""
    from infra.tool_manager.service_registry import register_capability

    try:
        provider = loader()
        register_capability(name, provider)
    except Exception as e:
        _REGISTER_FAILURES.append((name, f"{type(e).__name__}: {e}"))
        logger.warning(f"[bootstrap] 能力 {name} 注册失败: {e}")


def _report_capability_status(expected: list) -> None:
    """启动期校验：报告缺失/失败的能力（fail-fast 提前暴露配置问题）。"""
    from infra.tool_manager.service_registry import registered_names

    registered = set(registered_names())
    missing = [n for n in expected if n not in registered]
    if _REGISTER_FAILURES or missing:
        logger.warning(
            f"[bootstrap] 能力注册不完整: "
            f"失败 {len(_REGISTER_FAILURES)} 个 {_REGISTER_FAILURES}，"
            f"缺失 {len(missing)} 个 {missing}"
        )
    else:
        logger.info(f"[bootstrap] 全部 {len(expected)} 个业务能力注册成功")


def register_business_capabilities() -> None:
    """注册全部业务能力到 infra 端口。可安全重复调用（重复注册覆盖）。"""
    # ── 黑板查询 ──
    def _blackboard_query():
        from modules.database.blackboard_repo import query_observations
        return lambda: query_observations

    # ── 技能管理 ──
    def _skill_manager():
        from modules.thinking.skills.manager import skill_manager
        return lambda: skill_manager

    # ── 事件检索（记忆）──
    def _event_retrieval():
        from modules.memory.event_retrieval import get_event_retrieval
        return get_event_retrieval

    # ── 文件历史 ──
    def _file_history():
        from modules.cortex.file_history import get_file_history
        return get_file_history

    # ── 触控点检测（感知）──
    def _touchpoint_detector():
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector
        return lambda: TouchpointDetector

    # ── 检测路由（感知）──
    def _detector_router():
        from modules.perception.screen import get_detector_router
        return get_detector_router

    # ── 值格式化 ──
    def _value_formatter():
        from modules.thinking.value_formatter import ValueFormatter
        from config.values_store import value_system
        return lambda: ValueFormatter(value_system)

    # ── 工具安全门 ──
    def _tool_security_gate():
        from modules.security_system.tool_security_gate import get_tool_security_gate
        return get_tool_security_gate

    # ── 回合图片（多模态直连）──
    def _turn_images():
        from modules.thinking.turn_images import get_turn_images, clear_turn_images
        return lambda: (get_turn_images, clear_turn_images)

    _safe_register("blackboard_query", _blackboard_query)
    _safe_register("skill_manager", _skill_manager)
    _safe_register("event_retrieval", _event_retrieval)
    _safe_register("file_history", _file_history)
    _safe_register("touchpoint_detector", _touchpoint_detector)
    _safe_register("detector_router", _detector_router)
    _safe_register("value_formatter", _value_formatter)
    _safe_register("tool_security_gate", _tool_security_gate)
    _safe_register("turn_images", _turn_images)

    _report_capability_status([
        "blackboard_query", "skill_manager", "event_retrieval", "file_history",
        "touchpoint_detector", "detector_router", "value_formatter",
        "tool_security_gate", "turn_images",
    ])
