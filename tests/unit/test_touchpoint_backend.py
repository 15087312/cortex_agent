"""perception/screen/touchpoint_backend 测试（此前 24% 覆盖）"""
from unittest.mock import MagicMock, patch

import modules.perception.screen.touchpoint_backend as tb
from modules.perception.screen.touchpoint_backend import TouchpointBackend


def test_init_available():
    with patch.dict("sys.modules", {"touchpoint": MagicMock()}):
        b = TouchpointBackend()
        assert b.is_available() is True


def test_init_unavailable():
    import sys
    old = sys.modules.pop("touchpoint", None)
    try:
        with patch.dict("sys.modules", {"touchpoint": None}):
            import importlib
            importlib.reload(tb)
            b = TouchpointBackend.__new__(TouchpointBackend)
            b._available = False
            assert b.is_available() is False
    finally:
        if old is not None:
            sys.modules["touchpoint"] = old


def _detect_backend(monkeypatch):
    b = TouchpointBackend.__new__(TouchpointBackend)
    b._available = True
    return b


def test_detect_no_windows(monkeypatch):
    class Tp:
        @staticmethod
        def windows():
            return []
    import sys
    monkeypatch.setitem(sys.modules, "touchpoint", Tp())
    b = _detect_backend(monkeypatch)
    result = b.detect(app="X")
    assert result.backend == "touchpoint"
    assert result.element_count == 0


def test_detect_by_app(monkeypatch):
    class W:
        def __init__(self, app, title, is_active=False, id=1):
            self.app = app
            self.title = title
            self.is_active = is_active
            self.id = id

    class El:
        def __init__(self, role, name, x, y, w, h):
            self.role = type("R", (), {"name": role})()
            self.name = name
            self.position = (x, y)
            self.size = (w, h)
            self.actions = ["click"]

    class Tp:
        wins = [W("Safari", "我的网页", is_active=True, id=1), W("Finder", "桌面", id=2)]

        @staticmethod
        def windows():
            return Tp.wins

        @staticmethod
        def elements(**kw):
            return [El("BUTTON", "提交", 10, 20, 30, 10)]

    import sys
    monkeypatch.setitem(sys.modules, "touchpoint", Tp())
    b = _detect_backend(monkeypatch)
    result = b.detect(app="Safari")
    assert result.app_name == "Safari"
    assert result.element_count == 1
    assert result.elements[0].type == "button"
    assert result.elements[0].center_x == 25
    assert result.role_summary.get("button") == 1


def test_detect_active_window(monkeypatch):
    class W:
        def __init__(self, app, title, is_active):
            self.app = app
            self.title = title
            self.is_active = is_active
            self.id = 5

    class Tp:
        @staticmethod
        def windows():
            return [W("Finder", "桌面", False), W("Chrome", "网页", True)]

        @staticmethod
        def elements(**kw):
            return []

    import sys
    monkeypatch.setitem(sys.modules, "touchpoint", Tp())
    b = _detect_backend(monkeypatch)
    result = b.detect()
    assert result.app_name == "Chrome"
