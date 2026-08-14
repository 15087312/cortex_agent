"""output_system/core 补充测试：validate / process / 分发 / 硬件方法"""
from unittest.mock import MagicMock, patch

from modules.output_system.core import OutputSystem


def _sys():
    s = OutputSystem.__new__(OutputSystem)
    s.core_validator = MagicMock()
    s.content_validator = MagicMock()
    s.output_validator = MagicMock()
    s.distributor = MagicMock()
    s.input_controller = MagicMock()
    s._content_enabled = True
    s._output_fmt_enabled = True
    s.interrupt_flag = False
    return s


# ── 开关 / 中断 ───────────────────────────────────────────────────────────────

def test_enable_content_check_disabled():
    s = _sys()
    s._enable_content_check(False)
    assert s._content_enabled is False
    s._enable_content_check()
    assert s._content_enabled is True
    s.enable_content_check(False)
    assert s._content_enabled is False


def test_enable_format_check_disabled():
    s = _sys()
    s._enable_format_check(False)
    assert s._output_fmt_enabled is False
    s._enable_format_check()
    assert s._output_fmt_enabled is True
    s.enable_format_check(False)
    assert s._output_fmt_enabled is False


def test_interrupt():
    s = _sys()
    s.set_interrupt()
    assert s.interrupt_flag is True
    s.reset_interrupt()
    assert s.interrupt_flag is False


# ── clean_response ────────────────────────────────────────────────────────────

def test_clean_response_empty():
    assert OutputSystem.clean_response("") == ""
    assert OutputSystem.clean_response(None) is None


def test_clean_response_notebook_block():
    text = (
        "第一行\n"
        "【更新记事本】内容\n"
        "- 任务状态: 进行中\n"
        "- 当前进度: 50%\n"
        "- 附带行\n"
        "正文继续"
    )
    out = OutputSystem.clean_response(text)
    # 正则先剥离 【更新记事本】 及其后全部内容
    assert out == "第一行"


def test_clean_response_notebook_lines_only():
    text = "- 任务状态: 进行中\n- 当前进度: 50%\n- 跳过\n真内容"
    out = OutputSystem.clean_response(text)
    assert out == "真内容"


def test_clean_response_no_notebook_keeps_dash_lines():
    text = "- 普通列表项\n第二行"
    out = OutputSystem.clean_response(text)
    assert out == text.strip()


# ── validate ──────────────────────────────────────────────────────────────────

def test_validate_core_fail():
    s = _sys()
    s.core_validator.validate_all.return_value = (False, "高危指令")
    ok, result = s.validate("rm -rf /")
    assert ok is False
    assert result == "高危指令"


def test_validate_content_fail():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "x")
    s.content_validator.validate.return_value = (False, "敏感")
    ok, result = s.validate("内容")
    assert ok is False
    assert result == "敏感"


def test_validate_format_fail():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "x")
    s.content_validator.validate.return_value = (True, "x")
    s.output_validator.validate.return_value = (False, "格式错")
    ok, result = s.validate("x", "text")
    assert ok is False
    assert result == "格式错"


def test_validate_content_disabled_skips_content():
    s = _sys()
    s._content_enabled = False
    s.core_validator.validate_all.return_value = (True, "x")
    s.content_validator.validate.return_value = (False, "不应走到")
    s.output_validator.validate.return_value = (True, "x")
    ok, _ = s.validate("x")
    assert ok is True
    s.content_validator.validate.assert_not_called()


def test_validate_format_disabled_skips_format():
    s = _sys()
    s._output_fmt_enabled = False
    s.core_validator.validate_all.return_value = (True, "x")
    s.content_validator.validate.return_value = (True, "x")
    s.output_validator.validate.return_value = (False, "不应走到")
    ok, _ = s.validate("x")
    assert ok is True
    s.output_validator.validate.assert_not_called()


def test_validate_all_pass():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "原始")
    s.content_validator.validate.return_value = (True, "原始")
    s.output_validator.validate.return_value = (True, "原始")
    ok, result = s.validate("原始")
    assert ok is True
    assert result == "原始"


# ── process ───────────────────────────────────────────────────────────────────

def test_process_validation_fail():
    s = _sys()
    s.core_validator.validate_all.return_value = (False, "拦截")
    assert s.process("危险", {"user_input": ""}, "text", "console") is None


def test_process_interrupt():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "ok")
    s.content_validator.validate.return_value = (True, "ok")
    s.output_validator.validate.return_value = (True, "ok")
    s.interrupt_flag = True
    s.reset_interrupt = lambda: None  # 模拟校验期间被置位（绕过开头 reset）
    assert s.process("ok", {}, "text", "console") is None


def test_process_stream():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "ok")
    s.content_validator.validate.return_value = (True, "ok")
    s.output_validator.validate.return_value = (True, "ok")
    s.distributor.stream_output.return_value = iter(["a", "b"])
    gen = s.process("ok", {}, "text", "console", stream=True)
    assert gen is not None
    assert list(gen) == ["a", "b"]


def test_process_non_stream_distributes():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "ok")
    s.content_validator.validate.return_value = (True, "ok")
    s.output_validator.validate.return_value = (True, "ok")
    assert s.process("ok", {}, "text", "console") is None
    s.distributor.distribute.assert_called_with("ok", "console")


def test_output_text():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "hi")
    s.content_validator.validate.return_value = (True, "hi")
    s.output_validator.validate.return_value = (True, "hi")
    s.distributor.stream_output.return_value = iter(["h"])
    gen = s.output_text("hi", user_input="问题", stream=True)
    assert gen is not None


# ── output_code / system msg ──────────────────────────────────────────────────

def test_output_code_fail():
    s = _sys()
    s.core_validator.validate_all.return_value = (False, "拦截")
    assert s.output_code("rm -rf /") == "[代码未通过安全验证]"


def test_output_code_pass():
    s = _sys()
    s.core_validator.validate_all.return_value = (True, "print(1)")
    s.content_validator.validate.return_value = (True, "print(1)")
    s.output_validator.validate.return_value = (True, "print(1)")
    out = s.output_code("print(1)")
    assert out == "print(1)"
    s.distributor.distribute.assert_called()


def test_output_system_msg():
    s = _sys()
    s.output_system_msg("重启")
    s.distributor.distribute.assert_called_with("[系统] 重启", "console")


# ── 硬件方法委托 ──────────────────────────────────────────────────────────────

def test_hardware_delegation():
    s = _sys()
    s.input_controller.move_to.return_value = True
    s.input_controller.click.return_value = True
    s.input_controller.typewrite.return_value = True
    s.input_controller.press.return_value = True
    s.input_controller.hotkey.return_value = True
    s.input_controller.scroll.return_value = True
    s.input_controller.screenshot.return_value = b"png"
    s.input_controller.get_current_position.return_value = (1, 2)

    assert s.move_mouse(10, 20, 0.5) is True
    assert s.click_mouse(10, 20, "right", 2) is True
    assert s.type_text("hi", 0.1) is True
    assert s.press_key("enter") is True
    assert s.hotkey("cmd", "c") is True
    assert s.scroll(-3, 10, 20) is True
    assert s.screenshot() == b"png"
    assert s.get_mouse_position() == (1, 2)

    s.pause_input()
    s.input_controller.pause.assert_called_once()
    s.resume_input()
    s.input_controller.resume.assert_called_once()


def test_output_system_real_init():
    # 真实构造不应抛错（控制器在无 pyautogui 时降级）
    with patch("modules.output_system.input_controller.InputController") as ic:
        ic.return_value = MagicMock()
        s = OutputSystem()
        assert s._content_enabled is True
        assert s.interrupt_flag is False
