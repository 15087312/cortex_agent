"""TTS 引擎单元测试（mock gTTS，不访问真实网络）"""
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from config.settings import settings
from modules.output_system.tts import TTSEngine


class FakeGtts:
    """伪造 gTTS 类：save() 直接写文件，不发网络请求"""

    def __init__(self, text, lang="zh", slow=False):
        self.text = text
        self.lang = lang

    def save(self, path):
        Path(path).write_bytes(b"FAKE_MP3_DATA")


def install_fake_gtts(monkeypatch):
    fake_mod = types.ModuleType("gtts")
    fake_mod.gTTS = FakeGtts
    monkeypatch.setitem(sys.modules, "gtts", fake_mod)


@pytest.fixture
def tts_enabled(monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_TTS_ENABLED", True)


class TestTTSEngine:
    def test_available_false_when_dep_missing(self, monkeypatch):
        """gtts 未安装时应探测为不可用"""
        monkeypatch.setitem(sys.modules, "gtts", None)
        engine = TTSEngine()
        engine._available = None  # 强制重新探测
        assert engine.available is False

    def test_disabled_returns_none(self, tmp_path):
        """总开关关闭时返回 None（默认配置即为关闭）"""
        with patch.object(settings, "OUTPUT_TTS_ENABLED", False):
            engine = TTSEngine(output_dir=str(tmp_path))
            assert engine.synthesize_sync("你好") is None

    def test_empty_text_returns_none(self, tts_enabled, tmp_path):
        engine = TTSEngine(output_dir=str(tmp_path))
        assert engine.synthesize_sync("") is None
        assert engine.synthesize_sync("   ") is None

    def test_synthesize_success_writes_mp3(self, tts_enabled, monkeypatch, tmp_path):
        install_fake_gtts(monkeypatch)
        engine = TTSEngine(output_dir=str(tmp_path))
        path = engine.synthesize_sync("你好，科特")
        assert path is not None
        assert Path(path).suffix == ".mp3"
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"FAKE_MP3_DATA"

    @pytest.mark.asyncio
    async def test_synthesize_async(self, tts_enabled, monkeypatch, tmp_path):
        install_fake_gtts(monkeypatch)
        engine = TTSEngine(output_dir=str(tmp_path))
        path = await engine.synthesize("异步合成测试")
        assert path is not None
        assert Path(path).exists()
