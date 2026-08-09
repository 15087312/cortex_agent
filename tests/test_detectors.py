"""perception detectors 补测：window / ocr / voice / touchpoint"""
import asyncio
import threading
from unittest.mock import MagicMock, patch

from modules.perception.detectors.window_detector import WindowDetector
from modules.perception.detectors.ocr_detector import OCRDetector
from modules.perception.detectors.voice_detector import VoiceDetector, extract_instruction
from modules.perception.detectors.touchpoint_detector import TouchpointDetector


# ── window_detector ──

def _window():
    w = WindowDetector.__new__(WindowDetector)
    w._last_window = None
    w._last_app = None
    w._backend = "appkit"
    return w


def test_window_detector_type():
    w = WindowDetector()
    assert w.detector_type == "window"
    assert w._last_window is None


def test_window_detect_new_window(monkeypatch):
    w = _window()
    monkeypatch.setattr(w, "_get_active_window", lambda: ("窗口A", "Chrome"))
    events = w.detect(roi_image=None, roi_name="x")
    assert len(events) == 1
    assert events[0].payload["window_title"] == "窗口A"


def test_window_detect_no_change():
    w = _window()
    w._last_window = "窗口A"
    w._last_app = "Chrome"
    w._get_active_window = lambda: ("窗口A", "Chrome")
    assert w.detect(roi_image=None, roi_name="x") == []


def test_window_detect_none():
    w = _window()
    w._get_active_window = lambda: (None, None)
    assert w.detect(roi_image=None, roi_name="x") == []


def test_window_detect_unavailable(monkeypatch):
    w = _window()
    w._get_active_window = lambda: ("t", "a")
    monkeypatch.setattr(w, "is_available", lambda: False)
    assert w.detect(roi_image=None, roi_name="x") == []


def test_window_get_active_window_error(monkeypatch):
    w = _window()
    monkeypatch.setattr(w, "_get_window_win32", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(w, "_get_window_appkit", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert w._get_active_window() == (None, None)


def test_window_reset():
    w = _window()
    w._last_window = "x"
    w.reset()
    assert w._last_window is None


# ── ocr_detector ──

def _ocr():
    d = OCRDetector.__new__(OCRDetector)
    d._threshold = 0.1
    d._cooldown = 5.0
    d._lock = threading.Lock()
    d._last_trigger_time = 0.0
    d._last_texts = []
    d._event_bus = None
    d._sub_id = ""
    d._running = False
    return d


def test_ocr_on_screen_diff_below_threshold():
    d = _ocr()
    ev = type("E", (), {"payload": {"change_ratio": 0.01}})()
    d._on_screen_diff(ev)
    assert d._last_trigger_time == 0.0


def test_ocr_on_screen_diff_above_threshold(monkeypatch):
    d = _ocr()
    ev = type("E", (), {"payload": {"change_ratio": 0.5}})()
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    d._on_screen_diff(ev)
    assert d._last_trigger_time > 0


def test_ocr_on_screen_diff_cooldown():
    d = _ocr()
    d._last_trigger_time = __import__("time").time()
    ev = type("E", (), {"payload": {"change_ratio": 0.9}})()
    d._on_screen_diff(ev)


def test_ocr_run_no_screenshot(monkeypatch):
    d = _ocr()
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "capture_screen", lambda: None)
    d._run_ocr()


def test_ocr_run_publishes(monkeypatch):
    d = _ocr()
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "capture_screen", lambda: "shot")
    monkeypatch.setattr(d, "_ocr识别", lambda b64: ["文本1"])
    published = []
    monkeypatch.setattr(d, "_publish_event", lambda a, b, c: published.append((a, b, c)))
    d._run_ocr()
    assert published and published[0][0] == ["文本1"]


def test_ocr_run_no_texts(monkeypatch):
    d = _ocr()
    import utils.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "capture_screen", lambda: "shot")
    monkeypatch.setattr(d, "_ocr识别", lambda b64: [])
    d._run_ocr()  # 不发布


# ── voice_detector ──

def test_extract_instruction():
    assert extract_instruction("你好完毕", end_word="完毕") == "你好"
    assert extract_instruction("科特帮我看一下", wake_word="科特") == "帮我看一下"
    assert extract_instruction("科特 你好  完毕", wake_word="科特", end_word="完毕") == "你好"


def test_voice_detector_init():
    v = VoiceDetector.__new__(VoiceDetector)
    v._available = False
    v._running = False
    v._thread = None
    v._recognizer = None
    v._microphone = None
    import collections, threading
    v._events = collections.deque(maxlen=100)
    v._events_lock = threading.Lock()
    assert v.detector_type == "voice"
    assert v.is_available() is False
    v.start()  # 不可用时不启动线程
    assert v._running is False
    v.stop()
    v.reset()


# ── touchpoint ──

def test_touchpoint_find_app_path_non_mac():
    with patch("modules.perception.detectors.touchpoint_detector._IS_MAC", False):
        assert TouchpointDetector._find_app_path("X") is None
