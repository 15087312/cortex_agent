"""
MCP 资源检测器 — 通过 MCP 协议获取外部资源并转换为感知事件

不继承 PerceptionDetector（它基于 ROI 图像处理），
而是直接对接事件总线，产生 MCP_RESOURCE_UPDATE 事件。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from utils.logger import setup_logger
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from infra.mcp.perception_client import MCPPerceptionClientManager

logger = setup_logger("mcp_detector")


class MCPResourceDetector:
    """MCP 资源检测器

    订阅 MCP server 的资源变更，产生 PerceptionEvent。
    通过 MCPPerceptionClientManager 管理多个 MCP 连接。
    """

    def __init__(self, manager: MCPPerceptionClientManager):
        self._manager = manager
        self._event_callback: Optional[Callable[[PerceptionEvent], None]] = None
        self._connected = False

    def set_event_callback(self, callback: Callable[[PerceptionEvent], None]):
        """设置事件回调（由事件总线注入）"""
        self._event_callback = callback

    def set_platform(self, platform: str):
        """设置平台信息"""
        self._platform = platform

    async def start(self) -> bool:
        """启动所有 MCP 连接"""
        self._manager.set_on_update(self._on_resource_update)

        count = await self._manager.connect_all()
        self._connected = count > 0

        if count:
            logger.info(f"[MCPDetector] {count} server(s) 已连接")

            # 订阅所有可用资源
            for uri in self._manager.get_all_uris():
                content = await self._manager.read_resource(uri)
                if content is not None:
                    self._emit_event(
                        event_type=PerceptionEventType.MCP_RESOURCE_UPDATE,
                        payload={
                            "uri": uri,
                            "content": content[:1000],
                            "action": "initial_read",
                        },
                    )

        return self._connected

    def _on_resource_update(self, server_name: str, uri: str, content: str):
        """MCP 资源变更回调"""
        self._emit_event(
            event_type=PerceptionEventType.MCP_RESOURCE_UPDATE,
            payload={
                "uri": uri,
                "server": server_name,
                "content": content[:1000],
                "action": "updated",
            },
        )

    def _emit_event(self, event_type: str, payload: Dict[str, Any],
                    importance: float = 0.5):
        """发出感知事件"""
        if not self._event_callback:
            return
        event = PerceptionEvent(
            event_type=event_type,
            timestamp=time.time(),
            platform=getattr(self, '_platform', 'unknown'),
            source="mcp",
            importance=importance,
            payload=payload,
        )
        try:
            self._event_callback(event)
        except Exception as e:
            logger.debug(f"[MCPDetector] 事件回调异常 (非致命): {e}")

    async def read_resource(self, uri: str) -> Optional[str]:
        """主动读取资源"""
        return await self._manager.read_resource(uri)

    def get_status(self) -> List[Dict]:
        """获取连接状态"""
        return self._manager.get_status()

    async def shutdown(self):
        """关闭所有连接"""
        await self._manager.shutdown()
        self._connected = False
        logger.info("[MCPDetector] 已关闭")

    @property
    def is_available(self) -> bool:
        return True
