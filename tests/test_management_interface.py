"""management.interface 测试（此前 0% 覆盖）：错误上报端口单例"""
from modules.management.interface import get_error_reporter


def test_get_error_reporter_singleton():
    r1 = get_error_reporter()
    r2 = get_error_reporter()
    assert r1 is r2
    assert r1 is not None


def test_error_reporter_has_report():
    r = get_error_reporter()
    assert hasattr(r, "report")
