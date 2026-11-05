"""跨模块端口注册表 —— 让模块之间通过契约协作，而不是互相 import。

为什么需要它
    logiclink 的 L3 调用图要求「声明的接线 == 真实的 import」。管理面对感知/安全
    的只读查询、输出系统对安全校验器的依赖，都属于横切关切：用 `eq`（顺序数据流）
    表达不了，直接 import 又会被记为 E302 未声明耦合。用注册表做依赖倒置后：

        提供方（perception / security_system）在模块加载时 register(...)
        消费方（management / output_system）运行时 resolve(...)
        两者都只依赖 infra，彼此之间零 import 边

约定
    - 注册名用 "<领域>.<能力>"，如 "perception.system"、"security.validators"。
    - resolve 找不到返回 default；消费方必须自行降级，**不得**回退成 import 实现模块，
      否则依赖边会重新出现。
    - 提供方缺失属于正常状态（模块未加载），不是错误。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict

_LOCK = threading.RLock()
_PROVIDERS: Dict[str, Any] = {}


class _Factory:
    """把零参 callable 标记为工厂，避免与「本身就是可调用对象」的端口混淆。"""

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn

    def build(self) -> Any:
        return self._fn()


def register(name: str, provider: Any) -> None:
    """登记端口提供方；重复登记以最后一次为准。"""
    if not name:
        raise ValueError("端口名不能为空")
    with _LOCK:
        _PROVIDERS[name] = provider


def unregister(name: str) -> None:
    with _LOCK:
        _PROVIDERS.pop(name, None)


def as_factory(fn: Callable[[], Any]) -> _Factory:
    """把零参工厂包成可登记的值（延迟构造，保留提供方的单例时机）。"""
    return _Factory(fn)


def resolve(name: str, default: Any = None) -> Any:
    """查询端口；登记的是工厂就调用它。未登记返回 default。"""
    with _LOCK:
        provider = _PROVIDERS.get(name)
    if provider is None:
        return default
    if isinstance(provider, _Factory):
        return provider.build()
    return provider


def registered(name: str) -> bool:
    return resolve(name) is not None


def clear() -> None:
    """清空全部端口（仅供测试）。"""
    with _LOCK:
        _PROVIDERS.clear()


def snapshot() -> Dict[str, Any]:
    """当前已登记端口（调试/诊断用）。"""
    with _LOCK:
        return dict(_PROVIDERS)


__all__ = [
    "register",
    "unregister",
    "as_factory",
    "resolve",
    "registered",
    "clear",
    "snapshot",
]
