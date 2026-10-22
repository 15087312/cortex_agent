"""window_detector + screen/context.py 分支覆盖扩展

window_detector: 平台初始化(win32/quartz/linux, import 成功/失败)、
                 is_available、detect 各分支、win32/quartz 后端实现、异常兜底。
context.py: get_summary 全字段、to_dict 截断限制。
"""
import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np

from modules.perception.detectors.window_detector import WindowDetector
from modules.perception.events.types import PerceptionEventType
from modules.perception.screen.context import ScreenContext, UIElement


# ====================================================================
# WindowDetector — 平台后端初始化
# ====================================================================

class TestWindowDetectorPlatform:
    def test_init_platform_windows(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Windows")
        assert WindowDetector()._platform == "Windows"

    def test_init_backend_win32_available(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Windows")
        det = WindowDetector()
        fake = types.ModuleType("win32gui")
        with patch.dict(sys.modules, {"win32gui": fake}):
            det._init_backend()
        assert det._backend == "win32"

    def test_init_backend_win32_missing(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Windows")
        det = WindowDetector()
        with patch.dict(sys.modules, {"win32gui": None}):
            det._init_backend()
        assert det._backend is None

    def test_init_backend_quartz_available(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        det = WindowDetector()
        fake = types.ModuleType("Quartz")
        fake.CGWindowListCopyWindowInfo = MagicMock()
        with patch.dict(sys.modules, {"Quartz": fake}):
            det._init_backend()
        assert det._backend == "quartz"

    def test_init_backend_quartz_missing(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        det = WindowDetector()
        with patch.dict(sys.modules, {"Quartz": None}):
            det._init_backend()
        assert det._backend is None

    def test_init_backend_linux(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        det = WindowDetector()
        det._init_backend()
        assert det._backend is None

    def test_is_available_initializes_backend(self):
        det = WindowDetector()
        det._backend = None
        with patch.object(det, "_init_backend") as m:
            assert det.is_available() is False
        m.assert_called_once()

    def test_is_available_true(self):
        det = WindowDetector()
        det._backend = "win32"
        assert det.is_available() is True


# ====================================================================
# WindowDetector — detect 分支
# ====================================================================

class TestWindowDetectorDetectExt:
    def test_detect_init_backend_when_none(self):
        det = WindowDetector()
        det._backend = None
        with patch.object(det, "_init_backend"), \
             patch.object(det, "is_available", return_value=True), \
             patch.object(det, "_get_active_window", return_value=("W", "A")):
            events = det.detect(np.empty(0), "_system")
        assert len(events) == 1

    def test_detect_backend_already_set(self):
        det = WindowDetector()
        det._backend = "win32"
        with patch.object(det, "_init_backend") as m, \
             patch.object(det, "is_available", return_value=True), \
             patch.object(det, "_get_active_window", return_value=("W", "A")):
            events = det.detect(np.empty(0), "_system")
        m.assert_not_called()
        assert len(events) == 1

    def test_detect_not_available_returns_empty(self):
        det = WindowDetector()
        with patch.object(det, "is_available", return_value=False):
            assert det.detect(np.empty(0), "_system") == []

    def test_detect_none_window_returns_empty(self):
        det = WindowDetector()
        with patch.object(det, "is_available", return_value=True), \
             patch.object(det, "_get_active_window", return_value=(None, None)):
            assert det.detect(np.empty(0), "_system") == []

    def test_get_active_window_exception(self):
        det = WindowDetector()
        det._backend = "win32"
        with patch.object(det, "_get_window_win32", side_effect=RuntimeError("boom")):
            assert det._get_active_window() == (None, None)

    def test_get_active_window_quartz(self):
        det = WindowDetector()
        det._backend = "quartz"
        with patch.object(det, "_get_window_quartz", return_value=("T", "A")):
            assert det._get_active_window() == ("T", "A")

    def test_get_active_window_unknown_backend(self):
        det = WindowDetector()
        det._backend = "linux_native"
        assert det._get_active_window() == (None, None)


# ====================================================================
# WindowDetector — win32 后端实现
# ====================================================================

class TestWindowDetectorWin32:
    def test_get_window_win32_success(self):
        win32gui = types.ModuleType("win32gui")
        win32gui.GetForegroundWindow = lambda: 123
        win32gui.GetWindowText = lambda hwnd: "My Window"
        win32process = types.ModuleType("win32process")
        win32process.GetWindowThreadProcessId = lambda hwnd: (0, 456)
        psutil = types.ModuleType("psutil")
        proc = MagicMock()
        proc.name.return_value = "myapp"
        psutil.Process = lambda pid: proc
        with patch.dict(sys.modules, {
            "win32gui": win32gui, "win32process": win32process, "psutil": psutil,
        }):
            assert WindowDetector._get_window_win32() == ("My Window", "myapp")

    def test_get_window_win32_psutil_error(self):
        win32gui = types.ModuleType("win32gui")
        win32gui.GetForegroundWindow = lambda: 123
        win32gui.GetWindowText = lambda hwnd: "My Window"
        win32process = types.ModuleType("win32process")
        win32process.GetWindowThreadProcessId = lambda hwnd: (0, 456)
        psutil = types.ModuleType("psutil")
        psutil.Process = lambda pid: (_ for _ in ()).throw(RuntimeError("proc gone"))
        with patch.dict(sys.modules, {
            "win32gui": win32gui, "win32process": win32process, "psutil": psutil,
        }):
            assert WindowDetector._get_window_win32() == ("My Window", "")


# ====================================================================
# WindowDetector — quartz 后端实现（纯 Quartz，无 AppKit 依赖）
# ====================================================================

def _quartz_env(all_windows=None, quartz_raises=False):
    quartz = types.ModuleType("Quartz")
    quartz.kCGNullWindowID = 0
    quartz.kCGWindowListOptionOnScreenOnly = 1
    if quartz_raises:
        def _copy(*a):
            raise RuntimeError("quartz down")
        quartz.CGWindowListCopyWindowInfo = _copy
    else:
        quartz.CGWindowListCopyWindowInfo = lambda *a: all_windows
    return {"Quartz": quartz}


class TestWindowDetectorQuartz:
    def test_empty_list_returns_none(self):
        with patch.dict(sys.modules, _quartz_env([])):
            assert WindowDetector._get_window_quartz() == (None, None)

    def test_no_layer_zero_returns_none(self):
        wins = [
            {"kCGWindowOwnerPID": 999, "kCGWindowLayer": 1, "kCGWindowName": "Dock"},
        ]
        with patch.dict(sys.modules, _quartz_env(wins)):
            assert WindowDetector._get_window_quartz() == (None, None)

    def test_front_window_with_name(self):
        wins = [
            {"kCGWindowOwnerPID": 999, "kCGWindowLayer": 1,
             "kCGWindowOwnerName": "Other", "kCGWindowName": "Dock"},
            {"kCGWindowOwnerPID": 123, "kCGWindowLayer": 0,
             "kCGWindowOwnerName": "Safari", "kCGWindowName": "Front Window"},
            {"kCGWindowOwnerPID": 123, "kCGWindowLayer": 1,
             "kCGWindowOwnerName": "Safari", "kCGWindowName": "Dock Layer"},
        ]
        with patch.dict(sys.modules, _quartz_env(wins)):
            title, app = WindowDetector._get_window_quartz()
            assert title == "Front Window"
            assert app == "Safari"

    def test_front_window_without_name(self):
        wins = [
            {"kCGWindowOwnerPID": 123, "kCGWindowLayer": 0,
             "kCGWindowOwnerName": "Safari", "kCGWindowName": None},
        ]
        with patch.dict(sys.modules, _quartz_env(wins)):
            title, app = WindowDetector._get_window_quartz()
            assert title == "Safari"
            assert app == "Safari"

    def test_quartz_raises_propagates(self):
        """底层异常直接抛出，由 _get_active_window 统一兜底为 (None, None)"""
        import pytest as _pytest

        with patch.dict(sys.modules, _quartz_env([], quartz_raises=True)):
            with _pytest.raises(RuntimeError):
                WindowDetector._get_window_quartz()

    def test_quartz_raises_via_active_window(self):
        det = WindowDetector()
        det._backend = "quartz"
        with patch.object(det, "_get_window_quartz",
                          side_effect=RuntimeError("quartz down")):
            assert det._get_active_window() == (None, None)


# ====================================================================
# ScreenContext — get_summary / to_dict 限制
# ====================================================================

class TestScreenContextExt:
    def test_get_summary_full_fields(self):
        ctx = ScreenContext(app_name="Safari", window_title="GitHub - Home")
        ctx.elements = [
            UIElement(type="button", label="提交"),
            UIElement(type="button", label="取消"),
            UIElement(type="text_field", label="搜索"),
            UIElement(type="image", label=""),
        ]
        ctx.element_count = len(ctx.elements)
        ctx.visual_description = "一个网页界面"
        s = ctx.get_summary()
        assert "当前应用: Safari" in s
        assert "窗口: GitHub - Home" in s
        assert "UI元素(4个)" in s
        assert "按钮: 提交, 取消" in s
        assert "输入框: 1个" in s
        assert "视觉描述: 一个网页界面" in s

    def test_get_summary_elements_without_buttons_inputs(self):
        ctx = ScreenContext(app_name="x", window_title="w")
        ctx.elements = [UIElement(type="image", label="")]
        ctx.element_count = 1
        s = ctx.get_summary()
        assert "UI元素(1个)" in s
        assert "按钮" not in s
        assert "输入框" not in s

    def test_to_dict_truncates_elements_and_visual(self):
        ctx = ScreenContext(app_name="x", visual_description="长" * 1000)
        ctx.elements = [UIElement(type="button", label=str(i)) for i in range(60)]
        d = ctx.to_dict()
        assert len(d["elements"]) == 50
        assert len(d["visual_description"]) == 500

    def test_to_dict_no_visual_description(self):
        ctx = ScreenContext(app_name="x")
        d = ctx.to_dict()
        assert d["visual_description"] == ""

    def test_event_payload_integration(self):
        det = WindowDetector()
        with patch.object(det, "is_available", return_value=True), \
             patch.object(det, "_get_active_window", return_value=("Terminal", "iTerm2")):
            events = det.detect(np.empty(0), "_system")
        assert events[0].event_type == PerceptionEventType.SCREEN_WINDOW
        assert events[0].payload["app_name"] == "iTerm2"
