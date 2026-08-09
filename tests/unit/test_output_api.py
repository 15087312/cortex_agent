"""output_system/api 测试（此前 40% 覆盖）：输出端点"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import modules.output_system.api as api_mod


def test_text_output_success():
    """真实 OutputSystem：输出分发（无硬件副作用）"""
    out = asyncio.run(api_mod.text_output(text="你好"))
    assert out["success"] is True
    assert out["data"]["output"] == "你好"
