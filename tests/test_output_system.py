"""OutputSystem 测试（此前 27% 覆盖）：响应清洗、校验、开关"""
import pytest

from modules.output_system.core import OutputSystem


def test_clean_response_removes_notebook():
    raw = "结果文本\n- 任务状态: 进行中\n- 当前进度: 50%\n最终回复"
    out = OutputSystem.clean_response(raw)
    assert "最终回复" in out
    assert "任务状态" not in out
    assert "结果文本" in out


def test_clean_response_empty():
    assert OutputSystem.clean_response("") == ""
    assert OutputSystem.clean_response(None) is None


def test_clean_response_collapses_blank_lines():
    out = OutputSystem.clean_response("第一行\n\n\n\n第二行")
    assert "\n\n\n" not in out
    assert "第一行" in out and "第二行" in out


def test_validate_disabled_checks():
    os = OutputSystem()
    os._content_enabled = False
    os._output_fmt_enabled = False
    passed, result = os.validate("正常内容", "text")
    assert passed is True


def test_enable_toggles():
    os = OutputSystem()
    os.enable_content_check(False)
    os.enable_format_check(False)
    assert os._content_enabled is False
    assert os._output_fmt_enabled is False
    os.enable_content_check(True)
    assert os._content_enabled is True
