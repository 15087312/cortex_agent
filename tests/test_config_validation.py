"""
Tests for config/settings.py — field validators and production checks.
"""
import pytest
from config.settings import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides):
    """Build a Settings instance with no .env file and optional overrides."""
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


# ---------------------------------------------------------------------------
# Plugin security defaults
# ---------------------------------------------------------------------------

class TestPluginDefaults:
    def test_require_signatures_defaults_true(self):
        s = _make_settings()
        assert s.PLUGIN_REQUIRE_SIGNATURES is True

    def test_require_enforced_sandbox_defaults_true(self):
        s = _make_settings()
        assert s.PLUGIN_REQUIRE_ENFORCED_SANDBOX is True
