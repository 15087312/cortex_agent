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

# ── 端口注册（供 management / thinking 取用，避免其 import 本模块）──
# 包成具名函数：import 时执行一次，测试隔离 fixture 亦可重放
# （端口表是进程级全局状态，测试间需重建；见 tests/conftest.py）。
from infra.ports import as_factory, register as _register_port


def _register_perception_ports() -> None:
    _register_port("perception.system", as_factory(lambda: get_perception_system()))
    _register_port("perception.manager", perception_manager)
    # 供 thinking 侧取用（避免 thinking → perception 的模块边；thinking 的扫描根
    # 已回到包根，这条边会如实被 E302 检出，故必须走端口而非 import）
    _register_port("perception.integrator", as_factory(lambda: _get_integrator()))
    _register_port("perception.activity_notifier", as_factory(lambda: _get_notifier()))


_register_perception_ports()


def _get_integrator():
    from .integration import get_perception_integrator
    return get_perception_integrator()


def _get_notifier():
    from .difference import get_detector
    return get_detector()
