"""
Tests for config/settings.py — field validators and production checks.
"""
import pytest
from unittest.mock import patch
from config.settings import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides):
    """Build a Settings instance with no .env file and optional overrides."""
    with patch.object(Settings, "_load_user_config", return_value=None):
        return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# SERVER_PORT validation
# ---------------------------------------------------------------------------

class TestServerPortValidation:
    def test_accepts_valid_port(self):
        s = _make_settings(SERVER_PORT=8080)
        assert s.SERVER_PORT == 8080

    def test_accepts_lower_bound(self):
        s = _make_settings(SERVER_PORT=1024)
        assert s.SERVER_PORT == 1024

    def test_accepts_upper_bound(self):
        s = _make_settings(SERVER_PORT=65535)
        assert s.SERVER_PORT == 65535

    def test_rejects_below_1024(self):
        with pytest.raises(Exception):  # ValidationError from pydantic
            _make_settings(SERVER_PORT=80)

    def test_rejects_above_65535(self):
        with pytest.raises(Exception):
            _make_settings(SERVER_PORT=70000)

    def test_rejects_zero(self):
        with pytest.raises(Exception):
            _make_settings(SERVER_PORT=0)


# ---------------------------------------------------------------------------
# CONTEXT_COMPRESS_RATIO validation
# ---------------------------------------------------------------------------

class TestCompressRatioValidation:
    def test_accepts_valid_ratio(self):
        s = _make_settings(CONTEXT_COMPRESS_RATIO=0.2)
        assert s.CONTEXT_COMPRESS_RATIO == 0.2

    def test_accepts_lower_bound(self):
        s = _make_settings(CONTEXT_COMPRESS_RATIO=0.05)
        assert s.CONTEXT_COMPRESS_RATIO == 0.05

    def test_accepts_upper_bound(self):
        s = _make_settings(CONTEXT_COMPRESS_RATIO=0.95)
        assert s.CONTEXT_COMPRESS_RATIO == 0.95

    def test_rejects_below_lower_bound(self):
        with pytest.raises(Exception):
            _make_settings(CONTEXT_COMPRESS_RATIO=0.01)

    def test_rejects_above_upper_bound(self):
        with pytest.raises(Exception):
            _make_settings(CONTEXT_COMPRESS_RATIO=1.0)


# ---------------------------------------------------------------------------
# 上下文管理：CONTEXT_LIMIT_MODE / 条数 / Token 上限
# ---------------------------------------------------------------------------

class TestContextLimitValidation:
    def test_defaults_match_settings_page(self):
        """设置页默认展示的口径与默认值"""
        fields = Settings.model_fields
        assert fields["CONTEXT_LIMIT_MODE"].default == "ratio"
        assert fields["CONTEXT_COMPRESS_RATIO"].default == 0.8
        assert fields["CHAT_CONTEXT_MAX_MESSAGES"].default == 20
        assert fields["CHAT_CONTEXT_MAX_TOKENS"].default == 32000

    @pytest.mark.parametrize("mode", ["ratio", "messages", "tokens"])
    def test_accepts_supported_modes(self, mode):
        s = _make_settings(CONTEXT_LIMIT_MODE=mode)
        assert s.CONTEXT_LIMIT_MODE == mode

    def test_mode_is_case_insensitive(self):
        s = _make_settings(CONTEXT_LIMIT_MODE="MESSAGES")
        assert s.CONTEXT_LIMIT_MODE == "messages"

    def test_rejects_unknown_mode(self):
        with pytest.raises(Exception):
            _make_settings(CONTEXT_LIMIT_MODE="window")

    def test_zero_means_unlimited(self):
        s = _make_settings(CHAT_CONTEXT_MAX_MESSAGES=0, CHAT_CONTEXT_MAX_TOKENS=0)
        assert s.CHAT_CONTEXT_MAX_MESSAGES == 0
        assert s.CHAT_CONTEXT_MAX_TOKENS == 0

    @pytest.mark.parametrize("field", ["CHAT_CONTEXT_MAX_MESSAGES", "CHAT_CONTEXT_MAX_TOKENS"])
    def test_rejects_negative_limits(self, field):
        with pytest.raises(Exception):
            _make_settings(**{field: -1})


# ---------------------------------------------------------------------------
# validate_production
# ---------------------------------------------------------------------------

class TestValidateProduction:
    def test_raises_when_simple_api_key_empty_in_production(self):
        s = _make_settings(
            APP_ENV="production",
            LARGE_MODEL_API_KEY="sk-test",
            SIMPLE_API_KEY="",
        )
        with pytest.raises(ValueError, match="SIMPLE_API_KEY"):
            s.validate_production()

    def test_raises_when_large_model_key_empty_in_production(self):
        s = _make_settings(
            APP_ENV="production",
            LARGE_MODEL_API_KEY="",
            SIMPLE_API_KEY="sk-test",
        )
        with pytest.raises(ValueError, match="LARGE_MODEL_API_KEY"):
            s.validate_production()

    def test_raises_when_both_keys_empty_in_production(self):
        s = _make_settings(
            APP_ENV="production",
            LARGE_MODEL_API_KEY="",
            SIMPLE_API_KEY="",
        )
        with pytest.raises(ValueError):
            s.validate_production()

    def test_passes_when_both_keys_set_in_production(self):
        s = _make_settings(
            APP_ENV="production",
            LARGE_MODEL_API_KEY="sk-large",
            SIMPLE_API_KEY="sk-simple",
        )
        # Should not raise
        s.validate_production()

    def test_skips_validation_in_development(self):
        s = _make_settings(
            APP_ENV="development",
            LARGE_MODEL_API_KEY="",
            SIMPLE_API_KEY="",
        )
        # Should not raise — only production is checked
        s.validate_production()


