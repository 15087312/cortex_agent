"""
感知模块 - 动态感知外部变化
"""
from .difference.detector import get_detector
from .difference.heartbeat import get_heartbeat

# 新的独立模块
from .change_event import ChangeEvent


# integration.py 可能包含重型可选依赖，使用 try/except 确保不阻塞模块导入
try:
    from .integration import PerceptionIntegrator
except Exception:
    PerceptionIntegrator = None  # type: ignore[misc, assignment]

# 新系统（按需导入）
def get_perception_system():
    from .setup import get_perception_system as _get
    return _get()

# 向后兼容：perception_manager 代理指向新系统
def _get_compat_proxy():
    """向后兼容代理 — 将新系统的接口映射到旧版 perception_manager"""

    ps = get_perception_system()

    class _CompatProxy:
        """旧版 perception_manager 兼容包装"""

        @property
        def _running(self):
            return ps._started

        def start_monitoring(self):
            """启动监控（兼容旧接口）"""
            if not ps._started:
                ps.setup()
                ps.start()

        def stop_monitoring(self):
            """停止监控（兼容旧接口）"""
            ps.stop()

    return _CompatProxy()

perception_manager = _get_compat_proxy()

__all__ = [
    "get_perception_system",
    "ChangeEvent",
    "PerceptionIntegrator",
    "perception_manager",
    "get_detector",
    "get_heartbeat",
]
