"""
Tests for CompressionEngine — token 估算（压缩已移交 LLM 总结机制）。
"""
import pytest
from modules.thinking.context.compression import CompressionEngine, get_compression_engine


@pytest.fixture
def engine():
    import modules.thinking.context.compression as mod
    mod._instance = None
    return get_compression_engine()


class TestEstimateTokens:
    def test_english_text(self, engine):
        tokens = engine.estimate_tokens("hello world this is a test")
        assert tokens > 0
        assert tokens < 20

    def test_chinese_text(self, engine):
        tokens = engine.estimate_tokens("你好世界这是一段中文测试文本")
        assert tokens > 0

    def test_empty_string(self, engine):
        assert engine.estimate_tokens("") == 0

    def test_mixed_text(self, engine):
        tokens = engine.estimate_tokens("Hello 你好 world 世界")
        assert tokens > 0
