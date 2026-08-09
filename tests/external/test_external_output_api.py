"""output_api 硬件/音频端点真实测试（external：真实 TTS + 鼠标/键盘硬件）

需真实音频/鼠标环境。需显式 `pytest -m external`。
"""
import asyncio
import os

import pytest

import modules.output_system.api as api_mod

pytestmark = pytest.mark.external


def _has_tts() -> bool:
    try:
        import gtts  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_tts(), reason="gTTS 未安装")
async def test_speech_output_real_tts():
    """真实 TTS 合成（无音频文件生成时返回 None）"""
    out = await api_mod.speech_output(text="你好")
    assert out["success"] is True


def test_mouse_move_real(monkeypatch):
    """真实 InputController：移动到当前坐标（不产生副作用）"""
    ctrl = api_mod.input_controller
    if hasattr(ctrl, "get_current_position"):
        x, y = ctrl.get_current_position()
        assert ctrl.move_to(x, y) is True
