"""InputController 测试（此前 41% 覆盖）：鼠标操作与暂停分支"""
from unittest.mock import MagicMock, patch

from modules.output_system.input_controller import InputController, Point


def test_point_offset():
    p = Point(10, 20)
    q = p.offset(5, -3)
    assert (q.x, q.y) == (15, 17)


def _make_ctrl():
    fake_controller = MagicMock()
    fake_controller.move_to.return_value = True
    fake_controller.click.return_value = True
    with patch("modules.output_system.input_controller.PyAutoGUIController", return_value=fake_controller):
        ctrl = InputController()
    ctrl._controller = fake_controller
    return ctrl, fake_controller


def test_move_to():
    ctrl, fc = _make_ctrl()
    assert ctrl.move_to(1, 2) is True
    fc.move_to.assert_called_once_with(1, 2, 0.3)


def test_click():
    ctrl, fc = _make_ctrl()
    assert ctrl.click(1, 2, "left") is True
    fc.click.assert_called_once()


def test_paused_ignores_operations():
    ctrl, fc = _make_ctrl()
    ctrl._paused = True
    assert ctrl.move_to(1, 2) is False
    assert ctrl.click(1, 2) is False
    fc.move_to.assert_not_called()
    fc.click.assert_not_called()
