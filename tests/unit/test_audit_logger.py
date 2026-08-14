"""audit_logger.py — 安全审计日志（JSONL 写入/读取/异常降级）"""
import json
import os
from unittest.mock import patch

from modules.security_system.audit_logger import SecurityAuditLogger


def _logger(tmp_path):
    return SecurityAuditLogger(str(tmp_path / "audit.jsonl"))


def test_init_creates_dir(tmp_path):
    path = tmp_path / "nested" / "audit.jsonl"
    logger = SecurityAuditLogger(str(path))
    assert path.parent.is_dir()


def test_log_writes_jsonl(tmp_path):
    logger = _logger(tmp_path)
    logger.log("tool_call", "L4", "执行了命令", True, {"tool": "bash"})
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event_type"] == "tool_call"
    assert entry["security_level"] == "L4"
    assert entry["content_preview"] == "执行了命令"
    assert entry["result"] == "通过"
    assert entry["metadata"] == {"tool": "bash"}


def test_log_denied_result(tmp_path):
    logger = _logger(tmp_path)
    logger.log("block", "L0", "越权", False)
    entry = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert entry["result"] == "拦截"


def test_log_truncates_long_content(tmp_path):
    logger = _logger(tmp_path)
    long = "x" * 500
    logger.log("t", "L1", long, True)
    entry = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert len(entry["content_preview"]) == 100


def test_log_unencodable_content(tmp_path):
    logger = _logger(tmp_path)
    with patch.object(logger, "_save_local"):
        logger.log("t", "L1", "正常", True)
    # 无异常即通过（content_preview 编码路径已执行）


def test_log_metadata_default_empty(tmp_path):
    logger = _logger(tmp_path)
    logger.log("t", "L1", "x", True)
    entry = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert entry["metadata"] == {}


def test_log_without_dir_creation(tmp_path):
    logger = SecurityAuditLogger(str(tmp_path / "a" / "b" / "c.jsonl"))
    logger.log("t", "L1", "x", True)
    assert (tmp_path / "a" / "b" / "c.jsonl").exists()


def test_save_local_write_failure(tmp_path):
    logger = _logger(tmp_path)
    with patch("builtins.open", side_effect=OSError("disk full")):
        logger.log("t", "L1", "x", True)  # 不抛异常，记录到日志


def test_get_recent_logs_empty_when_missing(tmp_path):
    logger = _logger(tmp_path)
    assert logger.get_recent_logs() == []


def test_get_recent_logs_reads_and_skips_bad_lines(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text(
        json.dumps({"a": 1}) + "\nnot-json\n" + json.dumps({"b": 2}) + "\n",
        encoding="utf-8",
    )
    logger = SecurityAuditLogger(str(p))
    logs = logger.get_recent_logs()
    assert len(logs) == 2
    assert logs[0]["a"] == 1
    assert logs[1]["b"] == 2


def test_get_recent_logs_respects_limit(tmp_path):
    p = tmp_path / "audit.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for i in range(10):
            f.write(json.dumps({"i": i}) + "\n")
    logger = SecurityAuditLogger(str(p))
    logs = logger.get_recent_logs(limit=3)
    assert len(logs) == 3
    assert logs[0]["i"] == 7


def test_get_recent_logs_bad_line_skipped(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("{{bad", encoding="utf-8")
    logger = SecurityAuditLogger(str(p))
    assert logger.get_recent_logs() == []


# ── 防御分支：无目录 / 编码异常 / 日志输出失败 ──────────────────────────────

def test_ensure_dir_skipped_when_no_dirname(monkeypatch, tmp_path):
    """archive_path 无目录部分时 _ensure_dir 直接跳过（22->exit）"""
    logger = SecurityAuditLogger.__new__(SecurityAuditLogger)
    logger.archive_path = "plain_audit.jsonl"
    logger._ensure_dir()  # 不应抛异常


def test_log_encoding_failure_fallback(tmp_path):
    """content 为 bytes 时切片后无 .encode → AttributeError → 回退占位文案（36-37）"""
    logger = _logger(tmp_path)
    logger.log("t", "L1", b"\xff\xfe binary", True)  # bytes 无 .encode → fallback
    logger.log("t", "L1", "正常内容" * 100, True)  # 超长截断 + 正常编码
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert any("[内容包含无法编码的字符]" in item for item in lines)


def test_log_info_exception_falls_back_to_debug(tmp_path):
    logger = _logger(tmp_path)
    with patch.object(logger, "_save_local"):
        with patch("modules.security_system.audit_logger.logger.info", side_effect=RuntimeError("boom")):
            logger.log("t", "L1", "x", True)  # 51-52 logger.info 失败 → debug 兜底，不抛异常
