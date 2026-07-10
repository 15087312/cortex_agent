"""HTTP API 客户端 — 复用 aiohttp 连接池"""

from typing import Any, Dict, Optional, List

import aiohttp
from utils.logger import setup_logger

logger = setup_logger("tui_api_client")


class APIClient:
    """后端 REST API 客户端"""

    def __init__(self, api_url: str = "http://localhost:8080"):
        self.api_url = api_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        try:
            s = await self._get_session()
            async with s.get(f"{self.api_url}{path}", timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.warning("API GET %s failed: %s", path, e)
        return None

    async def _post(self, path: str, timeout: int = 3, **kwargs) -> Optional[aiohttp.ClientResponse]:
        try:
            s = await self._get_session()
            return await s.post(f"{self.api_url}{path}", timeout=timeout, **kwargs)
        except Exception as e:
            logger.warning("API POST %s failed: %s", path, e)
            return None

    async def _put(self, path: str, timeout: int = 3, **kwargs) -> Optional[aiohttp.ClientResponse]:
        try:
            s = await self._get_session()
            return await s.put(f"{self.api_url}{path}", timeout=timeout, **kwargs)
        except Exception as e:
            logger.warning("API PUT %s failed: %s", path, e)
            return None

    async def health(self) -> bool:
        try:
            s = await self._get_session()
            async with s.get(f"{self.api_url}/health", timeout=3) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug("Health check failed (degradation): %s", e)
            return False

    async def get_status(self) -> Optional[Dict[str, Any]]:
        result = await self._get("/stream/status")
        return result.get("data", {}) if result else None

    async def get_memory(self) -> Optional[Dict[str, Any]]:
        result = await self._get("/management/memory", timeout=5)
        return result.get("data", {}) if result else None

    async def get_sessions(self) -> Optional[List[Dict[str, Any]]]:
        result = await self._get("/stream/sessions", timeout=5)
        if result and result.get("success") and isinstance(result.get("data"), list):
            return result["data"]
        return None

    async def get_session_messages(self, session_id: str, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        """获取指定会话的历史消息"""
        result = await self._get(f"/stream/sessions/{session_id}/messages?limit={limit}", timeout=5)
        if result and result.get("success"):
            return result.get("data", [])
        return None

    # ── 思考控制 ──

    async def stop_thinking(self, session_id: str = "") -> bool:
        """停止当前思考处理"""
        try:
            path = "/stream/stop"
            if session_id:
                path += f"?session_id={session_id}"
            resp = await self._post(path, timeout=3)
            return resp is not None and resp.status in [200, 204]
        except Exception as e:
            logger.warning("stop_thinking request failed: %s", e)
        return False

    # ── 配置管理 ──

    async def get_config(self) -> Optional[Dict[str, Any]]:
        """获取当前应用配置"""
        result = await self._get("/config", timeout=3)
        return result.get("data", {}) if result else None

    async def update_config(self, key: str, value: Any) -> bool:
        """更新配置项"""
        try:
            resp = await self._put(f"/config/{key}", json={"value": value}, timeout=3)
            return resp is not None and resp.status in [200, 204]
        except Exception as e:
            logger.warning("update_config(%s) failed: %s", key, e)
        return False
