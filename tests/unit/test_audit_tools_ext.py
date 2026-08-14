"""audit_tools 测试 — 审计日志/变更追踪/完整性验证 全路径覆盖"""
import json
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.tools import audit_tools


@pytest.fixture
def log_paths(tmp_path, monkeypatch):
    audit_file = tmp_path / "audit_log.jsonl"
    change_file = tmp_path / "changes.jsonl"
    monkeypatch.setattr(audit_tools, "AUDIT_LOG", str(audit_file))
    monkeypatch.setattr(audit_tools, "CHANGE_LOG", str(change_file))
    return audit_file, change_file


class TestLogEntry:
    def test_writes_entry_with_timestamp(self, log_paths, tmp_path):
        audit_file, _ = log_paths
        audit_tools._log_entry(str(audit_file), {"tool": "f"})
        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["tool"] == "f"
        assert "_timestamp" in data

    def test_exception_warned(self, monkeypatch, tmp_path):
        called = []
        monkeypatch.setattr(audit_tools, "logger", MagicMock())
        # 目录部分为空 → os.makedirs("") 抛 FileNotFoundError
        audit_tools._log_entry("audit.jsonl", {"tool": "f"})
        assert audit_tools.logger.warning.call_count == 1


class TestLogToolCall:
    def test_basic(self, log_paths, tmp_path):
        audit_file, _ = log_paths
        r = audit_tools.log_tool_call("exec_command", "user", params='{"a":1}', result="ok", success=False)
        assert r == {"success": True}
        data = json.loads(audit_file.read_text(encoding="utf-8").strip().split("\n")[0])
        assert data["tool"] == "exec_command"
        assert data["role"] == "user"
        assert data["success"] is False
        assert data["params"] == '{"a":1}'

    def test_truncates_long_params(self, log_paths, tmp_path):
        audit_file, _ = log_paths
        audit_tools.log_tool_call("f", params="x" * 1000, result="y" * 1000)
        data = json.loads(audit_file.read_text(encoding="utf-8").strip().split("\n")[0])
        assert len(data["params"]) == 500
        assert len(data["result"]) == 500


class TestGenerateAuditReport:
    def test_reports_summary(self, log_paths, tmp_path):
        audit_file, _ = log_paths
        for i in range(3):
            audit_tools.log_tool_call(f"tool{i}", "user", params="p")
        r = audit_tools.generate_audit_report(limit=2)
        assert r["success"] is True
        assert r["total_logs"] == 3
        # by_role 仅统计 recent（最近 limit 条）
        assert r["by_role"] == {"user": 2}
        assert r["by_tool"]["tool1"] == 1
        assert len(r["recent_entries"]) == 2

    def test_limit_clamped(self, log_paths, tmp_path):
        audit_file, _ = log_paths
        audit_tools.log_tool_call("f")
        r = audit_tools.generate_audit_report(limit=0)
        assert "最近 1 条" in r["report_period"]
        r2 = audit_tools.generate_audit_report(limit=9999)
        assert "最近 500 条" in r2["report_period"]

    def test_bad_line_skipped(self, log_paths, tmp_path):
        audit_file, _ = log_paths
        audit_file.write_text("not-json\n" + json.dumps({"tool": "f", "role": "user"}) + "\n", encoding="utf-8")
        r = audit_tools.generate_audit_report()
        assert r["total_logs"] == 1

    def test_missing_file(self, log_paths, tmp_path):
        audit_file, _ = log_paths
        r = audit_tools.generate_audit_report()
        assert r["success"] is True
        assert r["total_logs"] == 0

    def test_exception_returns_error(self, log_paths, tmp_path, monkeypatch):
        audit_file, _ = log_paths
        monkeypatch.setattr(audit_tools.os.path, "exists", lambda p: True)
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("disk")))
        r = audit_tools.generate_audit_report()
        assert "error" in r


class TestTrackChanges:
    def test_log_requires_file(self, log_paths, tmp_path):
        r = audit_tools.track_changes("log")
        assert "需要 file_path" in r["error"]

    def test_log_writes_entry(self, log_paths, tmp_path):
        _, change_file = log_paths
        r = audit_tools.track_changes("log", file_path="/a/b.py", content_before="old")
        assert r["success"] is True
        data = json.loads(change_file.read_text(encoding="utf-8").strip().split("\n")[0])
        assert data["file"] == "/a/b.py"
        assert data["content_before"] == "old"

    def test_log_truncates_content(self, log_paths, tmp_path):
        _, change_file = log_paths
        audit_tools.track_changes("log", file_path="/a.py", content_before="x" * 2000)
        data = json.loads(change_file.read_text(encoding="utf-8").strip().split("\n")[0])
        assert len(data["content_before"]) == 1000

    def test_history_all_and_filtered(self, log_paths, tmp_path):
        _, change_file = log_paths
        audit_tools.track_changes("log", file_path="/a.py")
        audit_tools.track_changes("log", file_path="/b.py")
        r = audit_tools.track_changes("history")
        assert r["total_changes"] == 2
        r2 = audit_tools.track_changes("history", file_path="/a.py")
        assert r2["total_changes"] == 1

    def test_history_bad_line_skipped(self, log_paths, tmp_path):
        _, change_file = log_paths
        change_file.write_text("not-json\n" + json.dumps({"file": "/a.py"}) + "\n", encoding="utf-8")
        r = audit_tools.track_changes("history")
        assert r["total_changes"] == 1

    def test_history_missing_file(self, log_paths, tmp_path):
        _, change_file = log_paths
        r = audit_tools.track_changes("history")
        assert r["success"] is True
        assert r["total_changes"] == 0

    def test_history_read_error(self, log_paths, tmp_path, monkeypatch):
        _, change_file = log_paths
        change_file.write_text("x", encoding="utf-8")
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("disk")))
        r = audit_tools.track_changes("history")
        assert r["success"] is True

    def test_rollback_not_supported(self, log_paths, tmp_path):
        r = audit_tools.track_changes("rollback")
        assert "git" in r["error"]

    def test_unknown_action(self, log_paths, tmp_path):
        r = audit_tools.track_changes("bogus")
        assert "不支持" in r["error"]


class TestVerifyIntegrity:
    def test_clean(self, monkeypatch):
        sub = MagicMock()
        sub.run.return_value = MagicMock(returncode=0, stdout="")
        monkeypatch.setattr(audit_tools, "subprocess", sub)
        r = audit_tools.verify_integrity()
        assert r["success"] is True
        assert r["git_changes"] == 0
        assert r["status"] == "clean"

    def test_modified(self, monkeypatch):
        sub = MagicMock()
        sub.run.return_value = MagicMock(returncode=0, stdout="M file1.py\n?? file2.py\n")
        monkeypatch.setattr(audit_tools, "subprocess", sub)
        r = audit_tools.verify_integrity()
        assert r["git_changes"] == 2
        assert r["status"] == "modified"
        assert r["modified_files"] == ["ile1.py", "file2.py"]

    def test_not_git_repo(self, monkeypatch):
        sub = MagicMock()
        sub.run.return_value = MagicMock(returncode=128, stdout="")
        monkeypatch.setattr(audit_tools, "subprocess", sub)
        r = audit_tools.verify_integrity()
        assert "未找到 Git 仓库" in r["warning"]

    def test_exception(self, monkeypatch):
        sub = MagicMock()
        sub.run.side_effect = Exception("boom")
        monkeypatch.setattr(audit_tools, "subprocess", sub)
        r = audit_tools.verify_integrity()
        assert "验证失败" in r["error"]
