"""业务能力注册表 — infra 工具层经此获取业务服务，避免直接 import modules。

设计动机：
  工具层（infra.tool_manager）需要调用业务层（modules.*）的能力，
  若直接 import 会形成 infra → modules 的逆向依赖环。
  通过本端口，依赖方向反转为 modules → infra（单向、正常方向）：

      infra.tool_manager.tools ──get_capability()──▶ 端口 ◀──register── modules

  modules 侧在应用装配层（bootstrap）统一注册能力实现；
  infra 工具层获取能力时若未注册则返回 None，调用方显式降级，
  而不是 ImportError 或静默失败。
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from utils.logger import get_logger

logger = get_logger(__name__)

# 能力名 → 提供者（工厂 callable，惰性调用返回实际服务）
_capabilities: Dict[str, Callable[..., Any]] = {}


def register_capability(name: str, provider: Callable[..., Any]) -> None:
    """注册一项业务能力。provider 为无参 callable，返回实际服务/实例。

    重复注册会覆盖旧实现（用于测试 mock 或热替换）。装配层的注册是不可逆的
    import/启动副作用；需要「随作用域生灭」的接管走 :func:`provide_capability`。
    """
    _capabilities[name] = provider
    logger.debug(f"[capability] 已注册: {name}")


def get_capability(name: str) -> Any:
    """获取业务能力。未注册返回 None（调用方负责降级提示）。"""
    return _capabilities.get(name)


def has_capability(name: str) -> bool:
    return name in _capabilities


def registered_names() -> tuple:
    return tuple(sorted(_capabilities.keys()))


def unregister_capability(name: str) -> None:
    """注销能力（主要供测试清理使用）。"""
    _capabilities.pop(name, None)


def provide_capability(name: str, provider: Callable[..., Any]) -> Callable[[], None]:
    """接管一个能力端口，返回把自己放回去的操作。

    ``register_capability`` 无条件覆盖，它的 docstring 就写了「用于测试 mock 或
    热替换」——但覆盖要能退回去才叫热替换，否则「热替换」和「永久改掉」无法区分。
    端口在 infra 侧，所以撤销操作由端口自己给出（它才知道原值是什么），调用方
    （modules.shared.plugin_api.Ctx）只负责挂上时效。

    撤销按身份判断：若这个端口后来又被别处接管，本次撤销什么都不做。
    """
    had = name in _capabilities
    previous = _capabilities.get(name)
    _capabilities[name] = provider
    logger.info(
        f"[capability] 接管端口: {name}" + ("（遮蔽原实现）" if had else "（新端口）")
    )

    def undo() -> None:
        if _capabilities.get(name) is not provider:
            return
        if had and previous is not None:
            _capabilities[name] = previous
        else:
            _capabilities.pop(name, None)

    return undo
