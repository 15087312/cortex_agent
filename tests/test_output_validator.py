"""output_validator 测试（此前 42% 覆盖）：输出控制字符拦截"""
from modules.security_system.validators.output_validator import OutputValidator


def _v():
    return OutputValidator()


def test_validate_normal():
    ok, out = _v().validate("你好，世界\n第二行\t缩进")
    assert ok is True
    assert out == "你好，世界\n第二行\t缩进"


def test_validate_control_char():
    ok, msg = _v().validate("abc\x00def")
    assert ok is False
    assert "U+0000" in msg


def test_validate_ansi_escape():
    ok, msg = _v().validate("颜色\x1b[31m红\x1b[0m")
    assert ok is False
    assert "U+001B" in msg


def test_validate_del():
    ok, _ = _v().validate("abc\x7f")
    assert ok is False
