"""定时任务 handle_chat 真实端到端测试（external：需真实 LLM API + 前端连接）

绝不硬编码 API key——从项目配置读取，无 key 或无法连接时跳过。
需要显式 `pytest -m external` 且本地具备：
- 大模型 API key（config.settings.LARGE_MODEL_API_KEY 或环境变量）
- 前端 WebSocket 连接（主动消息推送可达）
"""
import asyncio
import os

import pytest

from modules.thinking.scheduled_tasks import ScheduledTaskManager

pytestmark = pytest.mark.external


def _has_api_key() -> bool:
    from config.settings import settings
    return bool(
        getattr(settings, "LARGE_MODEL_API_KEY", None)
        or os.environ.get("LARGE_MODEL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def _frontend_connected() -> bool:
    """有活跃 WS 连接（推送可达）"""
    try:
        from modules.thinking.api_stream import connection_manager
        return bool(connection_manager.active_connections)
    except Exception:
        return False


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_handle_chat_real_llm(tmp_path, monkeypatch):
    """真实 LLM：定时任务生成并推送（需前端在线；无连接时握手失败则跳过 LLM 不崩）"""
    import modules.database.connection as conn
    import modules.database.session_repo as sr_mod
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "sched_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    conn.get_db_manager().initialize()
    repo = SessionRepository()
    repo.create_session("s_ext")
    monkeypatch.setattr(sr_mod, "get_session_repo", lambda: repo)

    m = ScheduledTaskManager()
    # 无前端连接 → 握手失败 → 跳过 LLM（不崩、不推送）
    if not _frontend_connected():
        await m._handle_chat("s_ext", {"prompt": "你好"})
        return
    # 有前端连接 → 真实生成 + 持久化
    await m._handle_chat("s_ext", {"prompt": "请回复：测试定时消息"})
    with conn.get_db_manager().get_session() as s:
        from modules.database.chat_models import ChatMessage
        row = s.query(ChatMessage).filter_by(session_id="s_ext").first()
        assert row is not None and row.content  # 已落库


async def test_handle_chat_agent_type_tier_resolution(tmp_path, monkeypatch):
    """agent_type 真实解析（roles.yaml）——不依赖 LLM 输出，只验证流程不崩"""
    import modules.database.connection as conn
    import modules.database.session_repo as sr_mod
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "sched_ext2.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    conn.get_db_manager().initialize()
    repo = SessionRepository()
    repo.create_session("s_ext2")
    monkeypatch.setattr(sr_mod, "get_session_repo", lambda: repo)

    m = ScheduledTaskManager()
    # 无前端连接时握手失败会跳过 LLM；此测试只验证 agent_type 解析路径不抛异常
    if not _frontend_connected():
        await m._handle_chat("s_ext2", {"prompt": "测试", "agent_type": "code_writer"})
    else:
        await m._handle_chat("s_ext2", {"prompt": "测试", "agent_type": "code_writer"})
