"""chat_light ContinuousThinker 真实端到端（external：真实 LLM + 真实记忆库）

绝不硬编码 API key，无 key 时跳过。需 `pytest -m external`。
"""
import asyncio
import os

import pytest

from modules.thinking.chat_light.continuous_thinker import ContinuousThinker

pytestmark = pytest.mark.external


def _has_api_key() -> bool:
    from config.settings import settings
    return bool(
        getattr(settings, "LARGE_MODEL_API_KEY", None)
        or os.environ.get("LARGE_MODEL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_think_real_llm():
    """真实单模型思考循环：对话产生回复 token"""
    t = ContinuousThinker()
    q = asyncio.Queue()
    await t.think("s_ext", "你好", q)
    kinds = set()
    while not q.empty():
        item = q.get_nowait()
        kinds.add(item.get("type"))
    assert "done" in kinds


async def test_extract_memory_real(tmp_path, monkeypatch):
    """真实记忆提炼（长对话 → EventReducer 落库）"""
    import modules.database.connection as conn
    from modules.database.session_repo import SessionRepository
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "clt_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", __import__("threading").RLock())
    conn.get_db_manager().initialize()
    t = ContinuousThinker()
    msgs = [{"role": "user", "content": "我们讨论了项目延期的主要原因和应对方案。" * 6}]
    await t._extract_memory("s_ext", msgs)
