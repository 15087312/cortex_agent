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
        if result and result.get("success"):
            return result["data"].get("sessions", [])
        return None

    async def get_context(self, limit: int = 20) -> Optional[Dict[str, Any]]:
        """获取短期记忆上下文"""
        result = await self._get(f"/memory/short-term/context?limit={limit}", timeout=5)
        return result.get("data", {}) if result else None

    async def get_personality(self) -> Optional[Dict[str, Any]]:
        """获取用户个性配置"""
        result = await self._get("/memory/personality", timeout=5)
        return result.get("data", {}) if result else None

    async def get_user_emotion(self) -> Optional[Dict[str, Any]]:
        """获取当前用户情绪状态"""
        result = await self._get("/memory/short-term/emotion", timeout=5)
        return result.get("data", {}) if result else None

    async def search_memory(self, query: str, memory_type: str = "thought", limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        """搜索长期记忆"""
        try:
            s = await self._get_session()
            params = {"query": query, "memory_type": memory_type, "limit": limit}
            async with s.get(f"{self.api_url}/memory/long-term/{memory_type}/search", params=params, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get("results", []) if data else None
        except Exception as e:
            logger.warning("search_memory request failed: %s", e)
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

    async def toggle_companion_mode(self) -> None:
        """切换陪伴模式 - no-op since companion mode is now a skill"""
        return None
