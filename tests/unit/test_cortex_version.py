#!/usr/bin/env python3
"""cortex/version.py 单元测试 — 补齐版本读取/推导/更新分支覆盖。"""
from types import SimpleNamespace

import pytest


def test_read_version_file_missing(monkeypatch, tmp_path):
    import cortex.version as cv
    # 指向不存在 VERSION 文件的目录 → 返回 unknown 版本
    monkeypatch.setattr(cv, "Path", lambda *a, **k: tmp_path / "nope")
    assert cv._read_version_file() == "0.0.0-unknown"


def test_get_version_name_variants():
    from cortex.version import _get_version_name
    assert _get_version_name("1.2.3-unknown") == "Unknown"
    assert _get_version_name("1.2.3-control") == "Control Mode"
    assert _get_version_name("1.2.3-beta2") == "Beta"
    assert _get_version_name("1.2.3-alpha1") == "Alpha"
    assert _get_version_name("1.2.3") == "Release"


def test_get_version_info_dict():
    from cortex.version import get_version_info
    info = get_version_info()
    assert set(info) >= {"version", "core", "suffix", "name", "build_date", "full"}
    assert "v" in info["full"]
    assert info["full"].startswith("v")


def test_get_version_string_and_core():
    from cortex.version import get_version_string, __version_core__, __version__
    assert get_version_string().startswith("v")
    assert "." in __version_core__


def test_update_version_invalid_format():
    from cortex.version import update_version
    assert update_version("not-a-version") is False


def test_update_version_success(monkeypatch, tmp_path):
    import cortex.version as cv
    target = tmp_path / "VERSION"

    class FakeParent:
        def __init__(self, val):
            self.val = val

        def __truediv__(self, other):
            return target

    # Path(__file__) -> .parent -> .parent -> / "VERSION"
    def _fake_path(*a, **k):
        inner = FakeParent("inner")
        par = FakeParent("par")
        par.parent = inner
        outer = FakeParent("outer")
        outer.parent = par
        return outer

    monkeypatch.setattr(cv, "Path", _fake_path)
    assert cv.update_version("2.1.0-beta1") is True
    assert target.read_text() == "2.1.0-beta1"


def test_update_version_write_error(monkeypatch, tmp_path):
    import cortex.version as cv
    target = tmp_path / "VERSION"
    target.mkdir()  # 目标为目录 → 写入抛异常

    class FakeParent:
        def __truediv__(self, other):
            return target

    def _fake_path(*a, **k):
        inner = FakeParent()
        par = FakeParent()
        par.parent = inner
        outer = FakeParent()
        outer.parent = par
        return outer

    monkeypatch.setattr(cv, "Path", _fake_path)
    assert cv.update_version("2.1.0") is False


def test_cortex_init_import_error_fallback(monkeypatch):
    """cortex/__init__ 的 except ImportError 兜底分支 — 版本导入失败时降级为 unknown"""
    import importlib

    import cortex
    import cortex.version as cv

    saved = {
        k: cortex.__dict__.get(k)
        for k in ("__version__", "__version_name__", "get_version_string", "__all__")
    }
    try:
        for n in ("__version__", "__version_name__", "get_version_string"):
            monkeypatch.delattr(cv, n)
        importlib.reload(cortex)
        assert cortex.__version__ == "unknown"
        assert cortex.__version_name__ == "unknown"
        assert cortex.get_version_string() == "vunknown"
    finally:
        for k, v in saved.items():
            if v is None:
                cortex.__dict__.pop(k, None)
            else:
                cortex.__dict__[k] = v
