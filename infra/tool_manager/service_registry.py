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

    重复注册会覆盖旧实现（用于测试 mock 或热替换）。
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
