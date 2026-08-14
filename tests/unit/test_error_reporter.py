"""utils/error_reporter.py — 结构化错误报告"""
import pytest

from utils.error_reporter import (
    ErrorReport,
    ErrorReporter,
    report_error,
    report_exception,
    report_api_error,
    _build_report,
)


def test_error_report_defaults():
    r = ErrorReport(timestamp="t", source="s", module="m", function="f", error_type="e", message="msg")
    assert r.context == {}
    assert r.stack == ""
    assert r.severity == "ERROR"
    assert r.code == ""


def test_build_report():
    try:
        raise ValueError("bad value")
    except ValueError as e:
        r = _build_report(e, source="test", module="mod", function="fn", context={"k": 1})
    assert r.error_type == "ValueError"
    assert r.message == "bad value"
    assert r.context == {"k": 1}
    assert "ValueError" in r.stack


def test_report_error_logs(monkeypatch):
    captured = {}

    class FakeLogger:
        def error(self, *a, **k):
            captured["msg"] = a[0]
            captured["payload"] = a[1]

    monkeypatch.setattr("utils.error_reporter._reporter.logger", FakeLogger())
    report_error(ValueError("x"), source="s", module="m", function="f")
    assert "ERROR_REPORT" in captured["msg"]
    assert captured["payload"]["error_type"] == "ValueError"
    assert captured["payload"]["source"] == "s"


def test_report_exception(monkeypatch):
    captured = {}

    class FakeLogger:
        def error(self, *a, **k):
            captured["payload"] = a[1]

    monkeypatch.setattr("utils.error_reporter._reporter.logger", FakeLogger())
    report_exception(RuntimeError("boom"), module="m", function="f", context={"x": 1})
    assert captured["payload"]["message"] == "boom"
    assert captured["payload"]["context"] == {"x": 1}
    assert captured["payload"]["source"] == "exception"


def test_report_api_error_context(monkeypatch):
    captured = {}

    class FakeLogger:
        def error(self, *a, **k):
            captured["payload"] = a[1]

    monkeypatch.setattr("utils.error_reporter._reporter.logger", FakeLogger())
    report_api_error(OSError("conn"), module="m", function="f", status_code=503, request={"q": 1})
    p = captured["payload"]
    assert p["code"] == "503"
    assert p["context"]["status_code"] == 503
    assert p["context"]["request"] == {"q": 1}
    assert p["severity"] == "ERROR"


def test_report_logger_failure_falls_back(monkeypatch):
    """logger.error 抛异常时走字符串序列化降级"""
    calls = []

    class ExplodingLogger:
        def error(self, msg, payload=None):
            calls.append((msg, payload))
            if isinstance(payload, dict):
                raise RuntimeError("serialize fail")

    monkeypatch.setattr("utils.error_reporter._reporter.logger", ExplodingLogger())
    report_error(ValueError("x"), source="s", module="m", function="f")
    # 第一次 dict 序列化失败 → 第二次 str 序列化
    assert len(calls) == 2
    assert calls[1][0] == "[ERROR_REPORT] %s"
    assert isinstance(calls[1][1], str)


def test_report_logger_both_fail_debug(monkeypatch):
    """logger.error 对 dict 和 str 都失败时走 logger.debug 兜底"""
    from unittest.mock import MagicMock
    err = MagicMock(side_effect=RuntimeError("always fail"))
    fake = MagicMock()
    fake.error = err
    monkeypatch.setattr("utils.error_reporter._reporter.logger", fake)
    mod_logger = MagicMock()
    monkeypatch.setattr("utils.error_reporter.logger", mod_logger)
    report_error(ValueError("x"), source="s", module="m", function="f")
    assert mod_logger.debug.call_count == 1


def test_report_api_error_response_only(monkeypatch):
    captured = {}

    class FakeLogger:
        def error(self, *a, **k):
            captured["payload"] = a[1]

    monkeypatch.setattr("utils.error_reporter._reporter.logger", FakeLogger())
    report_api_error(OSError("conn"), module="m", function="f", response={"body": 1})
    p = captured["payload"]
    assert p["context"] == {"response": {"body": 1}}
    assert p["code"] == ""


def test_report_api_error_request_only(monkeypatch):
    captured = {}

    class FakeLogger:
        def error(self, *a, **k):
            captured["payload"] = a[1]

    monkeypatch.setattr("utils.error_reporter._reporter.logger", FakeLogger())
    report_api_error(OSError("conn"), module="m", function="f", request={"q": 1})
    p = captured["payload"]
    assert p["context"] == {"request": {"q": 1}}
