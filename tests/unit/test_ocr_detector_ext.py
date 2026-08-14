"""ocr_detector 补测：生命周期 / 触发 / OCR 识别 / 事件发布 / 单例"""
import threading
import time
from unittest.mock import MagicMock, patch

import modules.perception.detectors.ocr_detector as ocr_mod
from modules.perception.detectors.ocr_detector import OCRDetector, get_ocr_detector
from modules.perception.events.types import PerceptionEventType


class FakeRapidOCR:
    """fake rapidocr_onnxruntime.RapidOCR"""

    def __init__(self, results=None):
        self._results = results

    def __call__(self, path):
        return self._results, None


def _ocr():
    d = OCRDetector()
    return d


def test_init_and_properties():
    d = OCRDetector(threshold=0.5, cooldown=2.0)
    assert d._threshold == 0.5
    assert d._cooldown == 2.0
    assert d.detector_type == "ocr"
    assert d._running is False
    assert d._event_bus is None
    assert d._sub_id == ""


def test_init_defaults():
    d = OCRDetector()
    assert d._threshold == ocr_mod.OCR_TRIGGER_THRESHOLD
    assert d._cooldown == 5.0


def test_is_available_import_fails(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", None)
    d = _ocr()
    assert d.is_available() is False


def test_is_available_import_ok(monkeypatch):
    fake = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", fake)
    d = _ocr()
    assert d.is_available() is True


def test_start_subscribes(monkeypatch):
    bus = MagicMock()
    bus.subscribe.return_value = "sub_id"
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", MagicMock())
    d = _ocr()
    d.start(bus)
    assert d._running is True
    assert d._sub_id == "sub_id"
    bus.subscribe.assert_called_once()


def test_start_not_available(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", None)
    d = _ocr()
    d.start(MagicMock())
    assert d._running is False


def test_start_no_event_bus(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", MagicMock())
    d = _ocr()
    d.start(None)
    assert d._running is True
    assert d._sub_id == ""


def test_start_already_running(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", MagicMock())
    bus = MagicMock()
    d = _ocr()
    d._running = True
    d.start(bus)
    bus.subscribe.assert_not_called()


def test_stop(monkeypatch):
    bus = MagicMock()
    d = _ocr()
    d._running = True
    d._event_bus = bus
    d._sub_id = "s"
    d.stop()
    assert d._running is False
    bus.unsubscribe.assert_called_once_with("s")
    assert d._sub_id == ""


def test_stop_no_subscription():
    d = _ocr()
    d.stop()  # 不抛异常
    assert d._running is False


# ── 事件触发 ───────────────────────────────────────────────────────────

def _event(ratio):
    return type("E", (), {"payload": {"change_ratio": ratio}})()


def test_on_screen_diff_below_threshold():
    d = _ocr()
    d._on_screen_diff(_event(0.01))
    assert d._last_trigger_time == 0.0


def test_on_screen_diff_above_threshold(monkeypatch):
    d = _ocr()
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    d._on_screen_diff(_event(0.9))
    assert d._last_trigger_time > 0


def test_on_screen_diff_cooldown(monkeypatch):
    d = _ocr()
    d._last_trigger_time = time.time()
    monkeypatch.setattr(threading.Thread, "start",
                        MagicMock(side_effect=AssertionError("不应启动线程")))
    d._on_screen_diff(_event(0.9))  # 冷却期内不触发


# ── _run_ocr ──────────────────────────────────────────────────────────

def test_run_ocr_no_screenshot(monkeypatch):
    d = _ocr()
    monkeypatch.setattr("utils.screen_capture.capture_screen", lambda: None)
    d._run_ocr()


def test_run_ocr_no_texts(monkeypatch):
    d = _ocr()
    monkeypatch.setattr("utils.screen_capture.capture_screen", lambda: "shot")
    monkeypatch.setattr(d, "_ocr识别", lambda b64: [])
    d._run_ocr()


def test_run_ocr_no_change(monkeypatch):
    d = _ocr()
    d._last_texts = ["相同"]
    monkeypatch.setattr("utils.screen_capture.capture_screen", lambda: "shot")
    monkeypatch.setattr(d, "_ocr识别", lambda b64: ["相同"])
    published = []
    monkeypatch.setattr(d, "_publish_event", lambda a, b, c: published.append(1))
    d._run_ocr()
    assert published == []


def test_run_ocr_publishes_new_and_removed(monkeypatch):
    d = _ocr()
    d._last_texts = ["旧"]
    monkeypatch.setattr("utils.screen_capture.capture_screen", lambda: "shot")
    monkeypatch.setattr(d, "_ocr识别", lambda b64: ["新"])
    published = []
    monkeypatch.setattr(d, "_publish_event",
                        lambda new, removed, all: published.append((new, removed, all)))
    d._run_ocr()
    assert published == [(["新"], ["旧"], ["新"])]
    assert d._last_texts == ["新"]


# ── _ocr识别 ──────────────────────────────────────────────────────────

def test_ocr_recognize_flow(monkeypatch):
    """真实 b64 + tempfile 流程，RapidOCR 返回多段结果"""
    import base64
    fake = FakeRapidOCR(results=[
        ["box", " 你好  ", 0.95],
        ["box", "低置信", 0.3],
        ["box", "", 0.9],
    ])
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime",
                        type("M", (), {"RapidOCR": lambda *a, **k: fake})())
    d = _ocr()
    texts = d._ocr识别(base64.b64encode(b"pngdata").decode())
    assert texts == ["你好"]  # 过滤低置信度和空白


def test_ocr_recognize_no_result(monkeypatch):
    import base64
    fake = FakeRapidOCR(results=[None, None])
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime",
                        type("M", (), {"RapidOCR": lambda *a, **k: fake})())
    d = _ocr()
    assert d._ocr识别(base64.b64encode(b"pngdata").decode()) == []


def test_ocr_recognize_import_fails(monkeypatch):
    import base64
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", None)
    d = _ocr()
    assert d._ocr识别(base64.b64encode(b"x").decode()) == []


def test_ocr_recognize_engine_error(monkeypatch):
    import base64

    class BoomEngine:
        def __call__(self, path):
            raise RuntimeError("infer failed")

    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime",
                        type("M", (), {"RapidOCR": lambda *a, **k: BoomEngine()})())
    d = _ocr()
    assert d._ocr识别(base64.b64encode(b"x").decode()) == []


# ── _publish_event ────────────────────────────────────────────────────

def test_publish_event(monkeypatch):
    published = []
    fake_bus = MagicMock()
    fake_bus.publish = lambda e: published.append(e)
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: fake_bus)
    d = _ocr()
    d._publish_event(["新增文字1", "新增文字2"], ["消失文字"], ["新增文字1"])
    assert len(published) == 1
    ev = published[0]
    assert ev.event_type == PerceptionEventType.SCREEN_OCR
    assert ev.source == "ocr_detector"
    assert "新增文字" in ev.description
    assert ev.payload["text_count"] == 1


def test_publish_event_removed_only(monkeypatch):
    published = []
    fake_bus = MagicMock()
    fake_bus.publish = lambda e: published.append(e)
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: fake_bus)
    d = _ocr()
    d._publish_event([], ["消失"], ["A"])
    ev = published[0]
    assert "消失文字" in ev.description


def test_publish_event_no_changes(monkeypatch):
    published = []
    fake_bus = MagicMock()
    fake_bus.publish = lambda e: published.append(e)
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", lambda: fake_bus)
    d = _ocr()
    d._publish_event([], [], ["A", "B"])
    assert "2 段" in published[0].description


def test_publish_event_bus_error(monkeypatch):
    def boom():
        raise RuntimeError("bus down")
    monkeypatch.setattr("modules.perception.events.bus.get_event_bus", boom)
    d = _ocr()
    d._publish_event(["x"], [], ["x"])  # 不抛异常


# ── detect / 单例 ─────────────────────────────────────────────────────

def test_detect_returns_empty():
    import numpy as np
    d = _ocr()
    assert d.detect(np.zeros((4, 4, 3), dtype=np.uint8), "roi") == []


def test_get_ocr_detector_singleton(monkeypatch):
    monkeypatch.setattr(ocr_mod, "_detector", None)
    a = get_ocr_detector()
    b = get_ocr_detector()
    assert a is b
