"""api/auth.py — API Key 认证防御分支补测"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import api.auth as auth_mod


def test_require_api_key_header_valid():
    with patch.object(auth_mod.settings, "SIMPLE_API_KEY", "secret-key"):
        assert auth_mod.require_api_key(x_api_key="secret-key") == "secret-key"


def test_require_api_key_query_valid():
    with patch.object(auth_mod.settings, "SIMPLE_API_KEY", "secret-key"):
        assert auth_mod.require_api_key(x_api_key=None, api_key="secret-key") == "secret-key"


def test_require_api_key_missing_key_rejected():
    with patch.object(auth_mod.settings, "SIMPLE_API_KEY", "secret-key"):
        with pytest.raises(HTTPException) as ei:
            auth_mod.require_api_key(x_api_key=None, api_key=None)
        assert ei.value.status_code == 401


def test_require_api_key_wrong_key_rejected():
    with patch.object(auth_mod.settings, "SIMPLE_API_KEY", "secret-key"):
        with pytest.raises(HTTPException) as ei:
            auth_mod.require_api_key(x_api_key="wrong")
        assert ei.value.status_code == 401


def test_require_api_key_not_configured_500():
    """SIMPLE_API_KEY 为空 → 服务器认证未配置（23-24）"""
    with patch.object(auth_mod.settings, "SIMPLE_API_KEY", ""):
        with pytest.raises(HTTPException) as ei:
            auth_mod.require_api_key()
        assert ei.value.status_code == 500
        assert "认证未配置" in ei.value.detail
