#!/usr/bin/env python3
"""infra/model/config_fingerprint.py — 配置指纹 + 旧 client session 关闭分支覆盖。"""
from unittest.mock import AsyncMock, MagicMock


def test_close_client_session_none():
    from infra.model.config_fingerprint import close_client_session
    close_client_session(None)  # 空 client 安全返回


def test_close_client_session_no_session():
    from infra.model.config_fingerprint import close_client_session
    client = MagicMock()
    client._session = None
    close_client_session(client)  # 无 session 安全返回


def test_close_client_session_closed():
    from infra.model.config_fingerprint import close_client_session
    sess = MagicMock()
    sess.closed = True
    client = MagicMock()
    client._session = sess
    close_client_session(client)
    sess.close.assert_not_called()  # 已关闭则不重复关闭


async def test_close_client_session_running_loop(monkeypatch):
    import asyncio

    from infra.model.config_fingerprint import close_client_session
    scheduled = []
    monkeypatch.setattr(asyncio, "ensure_future", lambda coro: scheduled.append(coro))
    sess = MagicMock()
    sess.closed = False
    sess.close = AsyncMock()
    client = MagicMock()
    client._session = sess
    close_client_session(client)
    assert scheduled  # 有运行中 loop → 异步 ensure_future 关闭


def test_close_client_session_no_loop_fallback(monkeypatch):
    import asyncio

    from infra.model.config_fingerprint import close_client_session
    sess = MagicMock()
    sess.closed = False
    # 无运行中 loop → ensure_future 抛 RuntimeError → 降级同步 close
    monkeypatch.setattr(
        asyncio, "ensure_future",
        lambda coro: (_ for _ in ()).throw(RuntimeError("no running event loop")),
    )
    client = MagicMock()
    client._session = sess
    close_client_session(client)
    sess.close.assert_called()


def test_close_client_session_generic_exception(monkeypatch):
    import asyncio

    from infra.model.config_fingerprint import close_client_session
    sess = MagicMock()
    sess.closed = False
    monkeypatch.setattr(
        asyncio, "ensure_future",
        lambda coro: (_ for _ in ()).throw(ValueError("boom")),
    )
    # 非 RuntimeError 也应在 try/except 内被吞掉，不向上抛
    close_client_session(MagicMock(_session=sess)) or None  # noqa
