"""主动搭话 try_outreach 真实端到端测试（external：真实 LLM + 前端连接）

绝不硬编码 API key——从项目配置/环境读取，无 key 或无前端连接时跳过。
需显式 `pytest -m external`。
"""
import asyncio
import os
import threading

import pytest

import modules.database.connection as conn
from modules.perception.trigger import ProactiveTrigger

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
def session_repo(tmp_path, monkeypatch):
    import modules.database.session_repo as sr_mod
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "trg_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    conn.get_db_manager().initialize()
    repo = SessionRepository()
    repo.create_session("s_ext")
    repo.set_outreach_config("s_ext", {"enabled": True, "cooldown_minutes": 0})
    import modules.database.session_repo as sr
    monkeypatch.setattr(sr, "get_session_repo", lambda: repo)
    return repo


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_try_outreach_real_llm(session_repo):
    """真实触发：无前端连接时握手失败跳过（不调 LLM 不崩）"""
    tr = ProactiveTrigger()
    tr._session_last_trigger["s_ext"] = 0.0
    if not _frontend_connected():
        await tr._try_outreach("s_ext", "schedule")
        assert tr._trigger_count == 0  # 握手失败未触发
        return
    # 有前端连接 → 真实 LLM 生成 + 落库
    await tr._try_outreach("s_ext", "schedule")
    assert tr._trigger_count >= 1
    with conn.get_db_manager().get_session() as s:
        from modules.database.chat_models import ChatMessage
        row = s.query(ChatMessage).filter_by(session_id="s_ext").first()
        assert row is not None  # 消息已持久化
