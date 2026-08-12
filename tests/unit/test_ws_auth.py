"""WS 握手鉴权 _ws_auth_ok 单元测试（HIGH-1 安全修复回归）。

HTTP 中间件不覆盖 WebSocket，必须在握手处显式校验。规则与 HTTP 中间件一致：
- SIMPLE_API_KEY 未配置（开发模式）→ 放行
- 已配置 → 校验 X-API-Key header 或 ?api_key= 查询参数（hmac 常量时间比较）
"""
import pytest

from modules.thinking.api_stream import _ws_auth_ok
from config.settings import settings


class _FakeWS:
    """实现 _ws_auth_ok 用到的 WebSocket 接口子集"""

    def __init__(self, headers=None, query_params=None):
        self.headers = headers or {}
        self.query_params = query_params or {}


def _set_key(value: str) -> str:
    old = settings.SIMPLE_API_KEY
    object.__setattr__(settings, "SIMPLE_API_KEY", value)
    return old


def test_auth_disabled_when_no_key():
    """开发模式（未配置 SIMPLE_API_KEY）→ 放行"""
    old = _set_key("")
    try:
        assert _ws_auth_ok(_FakeWS()) is True
    finally:
        _set_key(old)


def test_reject_when_key_configured_but_missing():
    old = _set_key("secret")
    try:
        assert _ws_auth_ok(_FakeWS()) is False
    finally:
        _set_key(old)


def test_reject_wrong_header_key():
    old = _set_key("secret")
    try:
        assert _ws_auth_ok(_FakeWS(headers={"x-api-key": "wrong"})) is False
    finally:
        _set_key(old)


def test_accept_header_key():
    old = _set_key("secret")
    try:
        assert _ws_auth_ok(_FakeWS(headers={"x-api-key": "secret"})) is True
    finally:
        _set_key(old)


def test_accept_query_key():
    """浏览器 WebSocket 无法设 header，走 ?api_key= 查询参数"""
    old = _set_key("secret")
    try:
        assert _ws_auth_ok(_FakeWS(query_params={"api_key": "secret"})) is True
    finally:
        _set_key(old)


def test_query_key_works_with_bad_header():
    """header 与 query 任一匹配即放行（兼容 CLI header + 前端 query 混用）"""
    old = _set_key("secret")
    try:
        ws = _FakeWS(headers={"x-api-key": "wrong"}, query_params={"api_key": "secret"})
        assert _ws_auth_ok(ws) is True
    finally:
        _set_key(old)
