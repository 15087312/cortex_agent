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
    """端口优先取感知系统：接管 ``perception.system`` = 连本包内部的用法一起换。

    两个入口都得在端口路上才谈得上切换：管理面本来就经端口取，而 ``integration.py``
    是直接 import 本访问器的。之前端口只覆盖前者，插件接管后桌宠/集成器还在跑内置那
    套感知系统——同一个能力两套行为。provider 必须是 ``_builtin_perception_system``
    而不是本函数，否则端口→本函数→端口一调用就递归。
    """
    from infra.ports import resolve as _resolve_port

    system = _resolve_port("perception.system")
    if system is not None:
        return system
    return _builtin_perception_system()


def _builtin_perception_system():
    """内置实现（端口 provider 指向它）。按需导入，避免包根拖上重型子模块。"""
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
    _register_port("perception.system", as_factory(_builtin_perception_system))
    # manager 登记成工厂而不是现成的代理对象：代理在构造那一刻就把感知系统抓进了闭包，
    # 而 __all__ 里那个模块级 ``perception_manager`` 是本包 import 期就抓住的旧实现。
    # 登记工厂之后，接管 perception.system 的人不必再另外接管 perception.manager。
    _register_port("perception.manager", as_factory(_get_compat_proxy))
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
