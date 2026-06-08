"""
Tests for CompressionEngine — context window compression.
"""
import pytest
from modules.thinking.context.compression import CompressionEngine, CompressionLevel, get_compression_engine


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


class TestTruncateToTokens:
    def test_short_text_unchanged(self, engine):
        text = "short"
        result = engine._truncate_to_tokens(text, max_tokens=1000)
        assert result == text

    def test_long_text_truncated(self, engine):
        text = "word " * 1000
        result = engine._truncate_to_tokens(text, max_tokens=10)
        assert len(result) < len(text)
        assert len(result) > 0

    def test_empty_text(self, engine):
        assert engine._truncate_to_tokens("", max_tokens=100) == ""

    def test_zero_max_tokens(self, engine):
        result = engine._truncate_to_tokens("hello", max_tokens=0)
        assert result == "hello"


class TestRuleBasedCompression:
    def test_light_compress(self, engine):
        text = "Hello world. " * 100
        result = engine._light_compress(text)
        assert isinstance(result, str)
        assert len(result) <= len(text)

    def test_truncate_to_tokens(self, engine):
        text = "word " * 500
        result = engine._truncate_to_tokens(text, max_tokens=10)
        assert len(result) < len(text)


@pytest.mark.asyncio
async def test_compress_none_level(engine):
    text = "short text"
    result = await engine.compress(text, max_tokens=10000, level=CompressionLevel.NONE)
    assert result == text
