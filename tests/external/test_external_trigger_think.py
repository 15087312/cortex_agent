"""trigger_think 感知触发思考真实端到端（external：真实 LLM + 前端连接）

绝不硬编码 API key，无 key 或无前端连接时跳过。需 `pytest -m external`。
"""
import asyncio
import os
import threading

import pytest

import modules.database.connection as conn
import modules.perception.trigger_think as tt

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


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_think_real_llm(tmp_path, monkeypatch):
    """真实感知触发思考：无前端连接时握手失败跳过（不调 LLM 不崩）"""
    import modules.database.session_repo as sr_mod
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "tt_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    conn.get_db_manager().initialize()
    if not _frontend_connected():
        await tt._think("screen:测试变化")
    else:
        await tt._think("screen:测试变化")
