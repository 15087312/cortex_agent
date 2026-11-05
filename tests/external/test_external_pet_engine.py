"""PetEngine chat/stream 真实端到端（external：真实 LLM）

绝不硬编码 API key，无 key 时跳过。需 `pytest -m external`。
"""
import os

import pytest

pytestmark = pytest.mark.external


def _has_api_key() -> bool:
    from config.settings import settings
    return bool(
        getattr(settings, "LARGE_MODEL_API_KEY", None)
        or os.environ.get("LARGE_MODEL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


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
    """真实桌宠对话：桌宠已改为独立 SSE 形态，不再依赖前端 WS 握手。"""
    reply = await pe.chat("你好")
    # 桌宠已改为独立 SSE 形态，不再依赖前端 WS 握手；无前端连接也应能完成一轮。
    assert isinstance(reply, str)
