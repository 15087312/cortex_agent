"""api_stream think 真实端到端（external：真实多模型编排 + 真实 LLM）

绝不硬编码 API key——从配置/环境读取，无 key 或无前端连接时跳过。
需显式 `pytest -m external`。
"""
import asyncio
import os
import threading

import pytest

import modules.database.connection as conn
import modules.thinking.api_stream as stream_mod

pytestmark = pytest.mark.external


def _has_api_key() -> bool:
    from config.settings import settings
    return bool(
        getattr(settings, "LARGE_MODEL_API_KEY", None)
        or os.environ.get("LARGE_MODEL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


@pytest.fixture
def system(tmp_path, monkeypatch):
    import modules.database.session_repo as sr_mod
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "think_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    conn.get_db_manager().initialize()
    sys_ = stream_mod.StreamThinkingSystem()
    sys_._session_repo = SessionRepository()
    return sys_


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_think_real_orchestrator(system):
    """真实多模型编排 think：需前端 WS 连接（消息推送可达）"""
    system.sessions["s_ext"] = {"messages": [], "processing": False, "running": True, "model_id": "large_primary"}
    events = []

    async def cb(ev):
        events.append(ev)

    result = await system.think("s_ext", "你好", callback=cb)
    assert isinstance(result, str)
