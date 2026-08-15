"""模型 client 配置变更重建时的 aiohttp session 关闭测试

类型: 资源回收——配置指纹重建 client 时旧 aiohttp session 被关闭，防泄漏
（§53 配置指纹重建 + config_fingerprint.close_client_session）
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from infra.model.config_fingerprint import close_client_session

pytestmark = pytest.mark.leak


def test_close_client_session_none():
    """client 为 None 不崩（防御）"""
    close_client_session(None)


def test_close_client_session_no_session():
    """client 无 _session 属性不崩（防御）"""
    close_client_session(MagicMock(spec=[]))


def test_close_client_session_already_closed():
    """session 已关闭则不再调用 close（防御）"""
    sess = MagicMock()
    sess.closed = True
    client = MagicMock()
    client._session = sess
    close_client_session(client)
    sess.close.assert_not_called()


def test_close_client_session_no_loop():
    """无运行中事件循环：直接调用 close（兼容同步测试/线程）"""
    sess = MagicMock()
    sess.closed = False
    client = MagicMock()
    client._session = sess
    close_client_session(client)
    assert sess.close.call_count >= 1  # ensure_future 求值一次 + 异常回退一次


def test_close_client_session_with_loop():
    """有运行中事件循环：ensure_future 调度 close 执行"""
    sess = MagicMock()
    sess.closed = False
    client = MagicMock()
    client._session = sess
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        close_client_session(client)
        loop.run_until_complete(asyncio.sleep(0))  # flush ensure_future 任务
        sess.close.assert_called_once()
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_close_client_session_close_raises():
    """close 抛异常被吞，不向外传播（防御）"""
    sess = MagicMock()
    sess.closed = False
    sess.close.side_effect = RuntimeError("close 失败")
    client = MagicMock()
    client._session = sess
    close_client_session(client)  # 不崩
