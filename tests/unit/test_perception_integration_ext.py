"""perception/integration 补测：生命周期 / 事件订阅 / 描述格式化 / 单例"""
from unittest.mock import MagicMock

from modules.perception.integration import PerceptionIntegrator, get_perception_integrator
from modules.perception.events.types import PerceptionEvent, PerceptionEventType


def _integrator():
    return PerceptionIntegrator()


# ── start / stop ───────────────────────────────────────────────────────

def test_start_auto_monitoring(monkeypatch):
    ps = MagicMock()
    ps._started = False
    monkeypatch.setattr("modules.perception.get_perception_system", lambda: ps)
    bus = MagicMock()
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: bus)
    intg = _integrator()
    intg.start()
    ps.setup.assert_called_once()
    ps.start.assert_called_once()
    assert bus.subscribe.call_count >= 6


def test_start_when_already_started(monkeypatch):
    ps = MagicMock()
    ps._started = True
    monkeypatch.setattr("modules.perception.get_perception_system", lambda: ps)
    bus = MagicMock()
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: bus)
    intg = _integrator()
    intg.start()
    ps.setup.assert_not_called()
    ps.start.assert_not_called()
    assert bus.subscribe.call_count >= 6


def test_start_auto_monitoring_disabled(monkeypatch):
    monkeypatch.setattr("modules.perception.get_perception_system",
                        MagicMock(side_effect=AssertionError("不应调用")))
    intg = _integrator()
    intg._auto_monitoring = False
    intg.start()  # 不订阅
    assert intg._sub_id == ""


def test_subscribe_events_error(monkeypatch):
    bus = MagicMock()
    bus.subscribe.side_effect = RuntimeError("bus down")
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: bus)
    intg = _integrator()
    intg._subscribe_events()  # 不抛异常


def test_subscribe_events_import_error(monkeypatch):
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus",
                        MagicMock(side_effect=RuntimeError("no bus")))
    intg = _integrator()
    intg._subscribe_events()  # 静默降级


def test_stop(monkeypatch):
    ps = MagicMock()
    monkeypatch.setattr("modules.perception.get_perception_system", lambda: ps)
    bus = MagicMock()
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: bus)
    intg = _integrator()
    intg._sub_id = "sub_1"
    intg.stop()
    ps.stop.assert_called_once()
    bus.unsubscribe.assert_called_once_with("sub_1")
    assert intg._sub_id == ""


def test_stop_without_sub_id(monkeypatch):
    ps = MagicMock()
    monkeypatch.setattr("modules.perception.get_perception_system", lambda: ps)
    intg = _integrator()
    intg.stop()
    ps.stop.assert_called_once()


def test_stop_unsubscribe_error(monkeypatch):
    ps = MagicMock()
    monkeypatch.setattr("modules.perception.get_perception_system", lambda: ps)
    bus = MagicMock()
    bus.unsubscribe.side_effect = RuntimeError("boom")
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: bus)
    intg = _integrator()
    intg._sub_id = "sub_1"
    intg.stop()  # 不抛异常


# ── _on_perception_event ───────────────────────────────────────────────

def test_on_event_payload_fallback():
    intg = _integrator()
    intg.pool = MagicMock()
    ev = PerceptionEvent(event_type=PerceptionEventType.SCREEN_OCR, source="x",
                         payload={"new_lines": ["hi"]})
    intg._on_perception_event(ev)
    intg.pool.add.assert_called_once()


def test_on_event_exception():
    intg = _integrator()
    intg.pool = MagicMock()
    intg.pool.add.side_effect = RuntimeError("pool full")
    intg._on_perception_event(PerceptionEvent(event_type=PerceptionEventType.SCREEN_OCR,
                                              payload={"new_lines": ["hi"]}))  # 不抛异常


# ── _format_description 分支 ──────────────────────────────────────────

def test_format_screen_diff_ratios():
    f = PerceptionIntegrator._format_description
    assert "大幅变化" in f("screen.diff", {"change_ratio": 0.5})
    assert "中等变化" in f("screen.diff", {"change_ratio": 0.2})
    assert "小幅变化" in f("screen.diff", {"change_ratio": 0.05})


def test_format_window():
    f = PerceptionIntegrator._format_description
    assert "窗口切换" in f("screen.window", {"app_name": "A", "window_title": "T", "prev_app": "B"})
    assert "当前窗口" in f("screen.window", {"app_name": "A", "window_title": "T"})
    assert f("screen.window", {}) == ""


def test_format_ocr():
    f = PerceptionIntegrator._format_description
    assert "屏幕新文本" in f("screen.ocr", {"top_elements": ["a", "b"]})
    assert "屏幕新文本" in f("screen.ocr", {"new_lines": ["x", "y"]})
    assert f("screen.ocr", {}) == ""


def test_format_ui():
    f = PerceptionIntegrator._format_description
    out = f("screen.ui", {"element_count": 3, "description": "两个按钮"})
    assert "3个元素" in out and "两个按钮" in out
    assert f("screen.ui", {"element_count": 0}) == "屏幕UI: 0个元素"


def test_format_file_change():
    f = PerceptionIntegrator._format_description
    assert f("file.change", {"change": "修改", "path": "/a/b"}) == "文件修改: /a/b"
    assert f("file.change", {"path": "/a/b"}) == "文件变化: /a/b"
    assert f("file.change", {}) == "文件变化: 未知"


def test_format_speech_and_difference():
    f = PerceptionIntegrator._format_description
    assert f("speech.detected", {"text": "你好"}) == "语音: 你好"
    assert f("speech.detected", {}) == ""
    assert f("difference.detected", {"description": "变化了"}) == "变化了"
    assert f("difference.detected", {}) == ""


def test_format_default():
    f = PerceptionIntegrator._format_description
    assert f("custom", {"description": "desc"}) == "desc"
    assert f("custom", {"text": "txt"}) == "txt"
    assert f("custom", {}) == ""


def test_format_exception():
    f = PerceptionIntegrator._format_description
    assert f("screen.diff", {"change_ratio": object()}) == ""


# ── 单例 ──────────────────────────────────────────────────────────────

def test_get_perception_integrator_singleton(monkeypatch):
    import modules.perception.integration as integ_mod
    monkeypatch.setattr(integ_mod, "_perception_integrator_instance", None)
    a = get_perception_integrator()
    b = get_perception_integrator()
    assert a is b
