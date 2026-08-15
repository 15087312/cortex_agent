"""心理活动会话对话缓存内存安全测试

类型: 缓存型泄漏防护——每会话有界(20条) + 会话可清理，防无界增长
（§49 改为按 session 隔离后，补充清理能力与泄漏验证）
"""
import pytest

from modules.thinking.conscience import Conscience

pytestmark = pytest.mark.leak


def test_dialog_buffer_per_session_capped():
    """每会话对话缓存有上限（20 条），不随消息无限增长"""
    c = Conscience(model_client=None)
    for i in range(50):
        c.add_to_dialog("user", f"消息{i}", session_id="sess_a")
    assert len(c._dialog_buffers["sess_a"]) == 20


def test_dialog_buffers_isolated_by_session():
    """不同会话互不累计（§49 回归）"""
    c = Conscience(model_client=None)
    c.add_to_dialog("user", "A内容", session_id="sess_a")
    c.add_to_dialog("user", "B内容", session_id="sess_b")
    assert len(c._dialog_buffers["sess_a"]) == 1
    assert len(c._dialog_buffers["sess_b"]) == 1


def test_clear_session_frees_buffer():
    """会话删除时 clear_session 释放对应缓存"""
    c = Conscience(model_client=None)
    for i in range(5):
        c.add_to_dialog("user", f"消息{i}", session_id="sess_a")
    assert "sess_a" in c._dialog_buffers
    c.clear_session("sess_a")
    assert "sess_a" not in c._dialog_buffers


def test_clear_session_unknown_safe():
    """clear_session 未知会话不崩（防御）"""
    c = Conscience(model_client=None)
    c.clear_session("ghost")
    assert c._dialog_buffers == {}


def test_clear_all_dialogs_frees_everything():
    """清空所有会话缓存，防长期运行无界增长"""
    c = Conscience(model_client=None)
    for sid in ("s1", "s2", "s3"):
        c.add_to_dialog("user", "x", session_id=sid)
    assert len(c._dialog_buffers) == 3
    c.clear_all_dialogs()
    assert c._dialog_buffers == {}


def test_many_sessions_fully_clearable():
    """大量会话创建后可整体清理，不残留（防泄漏）"""
    c = Conscience(model_client=None)
    for i in range(100):
        c.add_to_dialog("user", "x", session_id=f"session_{i}")
    assert len(c._dialog_buffers) == 100
    c.clear_all_dialogs()
    assert c._dialog_buffers == {}
