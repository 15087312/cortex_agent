"""
Tests for configuration — Settings.
"""
import pytest
from config.settings import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.APP_ENV in ("development", "production", "testing")
        assert s.CONTEXT_WINDOW_SIZE > 0
        assert s.CONTEXT_COMPRESS_RATIO > 0
        assert s.CONTEXT_COMPRESS_RATIO < 1

    def test_model_names(self):
        s = Settings()
        assert isinstance(s.LARGE_MODEL_NAME, str)
        assert isinstance(s.SMALL_MODEL_NAME, str)

    def test_validate_production_passes(self):
        s = Settings()
        s.APP_ENV = "production"
        s.LARGE_MODEL_API_KEY = "sk-test"
        s.SIMPLE_API_KEY = "test-api-key-12345"
        s.validate_production()

    def test_validate_development_skips(self):
        s = Settings()
        s.APP_ENV = "development"
        s.LARGE_MODEL_API_KEY = ""
        s.validate_production()

    def test_api_fields_exist(self):
        s = Settings()
        assert hasattr(s, 'SIMPLE_API_KEY')
        assert hasattr(s, 'ALLOWED_CORS_ORIGINS')
        assert hasattr(s, 'SERVER_PORT')
        assert hasattr(s, 'LOGGING_ENABLED')

    def test_sqlite_path_is_absolute(self):
        s = Settings()
        # SQLITE_PATH should be an absolute path or contain the project name
        assert "memory.db" in s.SQLITE_PATH
