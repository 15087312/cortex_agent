"""PreGenExpertGuidanceAdapter 真实测试（external：真实 SmallModelClient LLM）

绝不硬编码 API key，无 key 时跳过。需 `pytest -m external`。
"""
import asyncio
import os

import pytest

from modules.thinking.adapters import PreGenExpertGuidanceAdapter

pytestmark = pytest.mark.external


def _has_api_key() -> bool:
    from config.settings import settings
    return bool(
        getattr(settings, "SMALL_MODEL_API_KEY", None)
        or getattr(settings, "LARGE_MODEL_API_KEY", None)
        or os.environ.get("LARGE_MODEL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


@pytest.mark.skipif(not _has_api_key(), reason="无大模型 API key")
async def test_pregen_expert_guidance_real():
    """真实良知系统：生成内心独白（无输出时返回 {}）"""
    adapter = PreGenExpertGuidanceAdapter()
    out = await adapter.run("用户问题")
    assert isinstance(out, dict)
