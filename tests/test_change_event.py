"""ChangeEvent 单元测试"""
import time
from modules.perception.change_event import ChangeEvent


class TestChangeEvent:
    def test_create_event(self):
        event = ChangeEvent(
            change_type="created",
            target_type="file",
            target="/tmp/test.txt",
            details={"size": 1024},
        )
        assert event.change_type == "created"
        assert event.target_type == "file"
        assert event.target == "/tmp/test.txt"
        assert event.details == {"size": 1024}
        assert event.timestamp > 0

    def test_default_details(self):
        event = ChangeEvent(change_type="modified", target_type="screen", target="main")
        assert event.details == {}

    def test_auto_timestamp(self):
        before = time.time()
        event = ChangeEvent(change_type="deleted", target_type="file", target="x")
        after = time.time()
        assert before <= event.timestamp <= after

    def test_to_prompt_file_created(self):
        event = ChangeEvent(change_type="created", target_type="file", target="readme.md")
        assert "Created" in event.to_prompt()
        assert "readme.md" in event.to_prompt()

    def test_to_prompt_file_modified(self):
        event = ChangeEvent(change_type="modified", target_type="file", target="main.py")
        assert "Modified" in event.to_prompt()
        assert "main.py" in event.to_prompt()

    def test_to_prompt_file_deleted(self):
        event = ChangeEvent(change_type="deleted", target_type="file", target="old.log")
        assert "Deleted" in event.to_prompt()
        assert "old.log" in event.to_prompt()

    def test_to_prompt_file_moved(self):
        event = ChangeEvent(
            change_type="moved",
            target_type="file",
            target="/new/dst.txt",
            details={"from": "/old/src.txt"},
        )
        text = event.to_prompt()
        assert "移动" in text or "Moved" in text or "moved" in text
        assert "/new/dst.txt" in text
        assert "/old/src.txt" in text

    def test_to_prompt_dialog(self):
        event = ChangeEvent(change_type="created", target_type="dialog", target="你好")
        text = event.to_prompt()
        assert "你好" in text

    def test_to_prompt_dialog_modified(self):
        event = ChangeEvent(change_type="modified", target_type="dialog", target="已编辑")
        text = event.to_prompt()
        assert "已编辑" in text

    def test_to_prompt_screen(self):
        event = ChangeEvent(
            change_type="changed",
            target_type="screen",
            target="main",
            details={"change_desc": "窗口切换"},
        )
        text = event.to_prompt()
        assert "main" in text
        assert "窗口切换" in text

    def test_to_prompt_screen_default_desc(self):
        event = ChangeEvent(change_type="changed", target_type="screen", target="main")
        text = event.to_prompt()
        assert "main" in text
        assert "画面变化" in text

    def test_to_prompt_speech(self):
        event = ChangeEvent(change_type="speech", target_type="speech", target="打开浏览器")
        text = event.to_prompt()
        assert "打开浏览器" in text

    def test_to_prompt_unknown_type(self):
        event = ChangeEvent(change_type="custom", target_type="unknown", target="something")
        text = event.to_prompt()
        assert "unknown" in text
        assert "custom" in text
        assert "something" in text
