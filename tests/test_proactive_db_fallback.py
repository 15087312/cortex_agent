"""主动搭话 _get_session_info 的 DB 兜底测试

场景：内存 sessions 为空（重启/长空闲）时，应从数据库选最近有真实消息的会话，
而不是直接跳过。
"""
import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.perception.trigger import ProactiveTrigger


class FakeRepo:
    """模拟 DB session repo"""

    def get_all_sessions(self, limit=50):
        return [
            {"session_id": "empty-session", "title": "空会话"},
            {"session_id": "recent-session", "title": "上次对话"},
            {"session_id": "thought-only", "title": "只有思考"},
        ]

    def get_recent_messages(self, session_id, limit=20):
        if session_id == "empty-session":
            return []
        if session_id == "thought-only":
            return [{"role": "thought", "content": "内部思考"}]
        return [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "有什么可以帮你？"},
        ]


class TestProactiveDbFallback(unittest.TestCase):

    def setUp(self):
        self.trigger = ProactiveTrigger.__new__(ProactiveTrigger)
        # 固定走 agent 路径的 DB repo，避免受环境 CORTEX_MODE=chatonly 影响
        self.patch_mode = mock.patch(
            "modules.thinking.chat_gateway._resolve_mode", return_value="agent"
        )
        self.patch_mode.start()
        self.addCleanup(self.patch_mode.stop)

    def test_memory_empty_falls_back_to_db(self):
        """内存无会话时，从 DB 选最近有真实消息的会话"""
        with mock.patch(
            "modules.thinking.api_stream.get_thinking_system", return_value=None
        ):
            with mock.patch(
                "modules.database.session_repo.get_session_repo",
                return_value=FakeRepo(),
            ) as repo_mock:
                sid, conversation = self.trigger._get_session_info()

        self.assertEqual(sid, "recent-session")
        self.assertIn("你好", conversation)
        self.assertIn("有什么可以帮你", conversation)
        repo_mock.assert_called_once()

    def test_memory_nonempty_prefers_latest_message(self):
        """内存有会话时优先内存（时间戳最新），不查 DB"""
        sys_with_sessions = mock.Mock()
        sys_with_sessions.sessions = {
            "a": {"messages": [{"role": "user", "content": "A", "timestamp": 100}]},
            "b": {"messages": [{"role": "user", "content": "B", "timestamp": 200}]},
        }
        with mock.patch(
            "modules.thinking.api_stream.get_thinking_system",
            return_value=sys_with_sessions,
        ) as sys_mock:
            with mock.patch(
                "modules.database.session_repo.get_session_repo",
                return_value=FakeRepo(),
            ) as repo_mock:
                sid, conversation = self.trigger._get_session_info()

        self.assertEqual(sid, "b")
        self.assertIn("B", conversation)
        sys_mock.assert_called_once()
        repo_mock.assert_not_called()

    def test_no_conversation_at_all_returns_empty(self):
        """内存和 DB 都没有真实消息 → 返回空（上层跳过）"""
        empty_repo = mock.Mock()
        empty_repo.get_all_sessions.return_value = [
            {"session_id": "x", "title": "x"},
        ]
        empty_repo.get_recent_messages.return_value = []
        with mock.patch(
            "modules.thinking.api_stream.get_thinking_system",
            return_value=None,
        ):
            with mock.patch(
                "modules.database.session_repo.get_session_repo",
                return_value=empty_repo,
            ):
                sid, conversation = self.trigger._get_session_info()

        self.assertEqual(sid, "")
        self.assertEqual(conversation, "")


class TestSpokenThisBoot(unittest.TestCase):
    """本次启动是否说过话（主动搭话前提）"""

    def setUp(self):
        self.trigger = ProactiveTrigger.__new__(ProactiveTrigger)
        self.patch_mode = mock.patch(
            "modules.thinking.chat_gateway._resolve_mode", return_value="agent"
        )
        self.patch_mode.start()
        self.addCleanup(self.patch_mode.stop)

    def test_not_spoken_blocks_outreach(self):
        """本次启动没说过话 → 不允许主动搭话（防止只打开没说话就打扰）"""
        with mock.patch(
            "modules.database.session_repo.get_boot_has_spoken", return_value=False
        ):
            self.assertFalse(self.trigger._has_spoken_this_boot())

    def test_spoken_allows_outreach(self):
        """本次启动发过消息 → 允许主动搭话"""
        with mock.patch(
            "modules.database.session_repo.get_boot_has_spoken", return_value=True
        ):
            self.assertTrue(self.trigger._has_spoken_this_boot())

    def test_execute_outreach_skips_when_not_spoken(self):
        """未说过话时 _execute_outreach 直接跳过，不选会话也不调 LLM"""
        event = mock.Mock()
        event.payload = {}
        with mock.patch.object(
            self.trigger, "_has_spoken_this_boot", return_value=False
        ) as spoken_mock:
            with mock.patch.object(self.trigger, "_get_session_info") as info_mock:
                self.trigger._execute_outreach(5, event, 1)
        spoken_mock.assert_called_once()
        info_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
