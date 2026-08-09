"""EventReducer.reduce 真实端到端（external：真实 LLM + 真实 EventStore）

绝不硬编码 API key，无 key 时跳过。需 `pytest -m external`。
"""
import asyncio
import os
import threading

import pytest

import modules.database.connection as conn
from modules.memory.event_reducer import EventReducer

pytestmark = pytest.mark.external


def _has_api_key() -> bool:
    from config.settings import settings
    return bool(
        getattr(settings, "LARGE_MODEL_API_KEY", None)
        or os.environ.get("LARGE_MODEL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


@pytest.fixture
def reducer(tmp_path, monkeypatch):
    from modules.memory.event_store import EventStore
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "er_ext.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    store = EventStore(
        db_path=str(tmp_path / "er.db"),
        faiss_index_path=str(tmp_path / "er.faiss"),
        id_map_path=str(tmp_path / "er_id.json"),
    )
    r = EventReducer(store=store)
    r._model_client = None  # reduce 内部会实例化 LLM
    return r


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_reduce_real_llm(reducer):
    """真实 LLM：长对话提炼为记忆事件并落库"""
    long_text = ("用户讨论了项目延期的主要原因和应对方案。" * 6)
    events = await reducer.reduce("s_ext", long_text, owner_id="large::large_primary")
    assert isinstance(events, list)
