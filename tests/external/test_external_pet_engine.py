"""PetEngine chat/stream 真实端到端（external：真实 LLM + 前端连接）

绝不硬编码 API key，无 key 或无前端连接时跳过。需 `pytest -m external`。
"""
import asyncio
import os
import threading

import pytest

import modules.database.connection as conn

pytestmark = pytest.mark.external


def _has_api_key() -> bool:
    from config.settings import settings
    return bool(
        getattr(settings, "LARGE_MODEL_API_KEY", None)
        or os.environ.get("LARGE_MODEL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def _frontend_connected() -> bool:
    try:
        from modules.thinking.api_stream import connection_manager
        return bool(connection_manager.active_connections)
    except Exception:
        return False


@pytest.fixture
def pe(tmp_path, monkeypatch):
    import modules.desktop_pet.store as store_mod
    from modules.desktop_pet.pet_engine import PetEngine
    store = store_mod.PetStore(str(tmp_path / "pet_ext.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    yield PetEngine(event_bus=None)
    store_mod.PetStore._instance = None


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_pet_chat_real(pe):
    """真实桌宠对话：无前端连接时握手失败跳过（不调 LLM 返回空）"""
    if not _frontend_connected():
        reply = await pe.chat("你好")
        assert reply == ""
    else:
        reply = await pe.chat("你好")
        assert isinstance(reply, str)
