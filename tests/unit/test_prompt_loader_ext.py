"""
扩展测试：config/prompts/loader.py — PromptLoader 缓存/热重载/异常处理与
get_loader 单例。
"""
import threading

import pytest

from config.prompts.loader import PromptLoader, get_loader


@pytest.fixture
def loader(tmp_path, monkeypatch):
    l = PromptLoader()
    monkeypatch.setattr(l, "_base_dir", tmp_path)
    return l


class TestPromptLoader:
    def test_load_caches(self, loader, tmp_path, monkeypatch):
        (tmp_path / "x.yaml").write_text("prompts:\n  p: 1\n", encoding="utf-8")
        calls = []

        def spy(name):
            calls.append(name)
            return PromptLoader._read_yaml(loader, name)

        monkeypatch.setattr(loader, "_read_yaml", spy)
        assert loader.load("x") == {"prompts": {"p": 1}}
        assert loader.load("x") == {"prompts": {"p": 1}}
        assert calls == ["x"]

    def test_load_none_not_cached(self, loader, monkeypatch):
        monkeypatch.setattr(loader, "_read_yaml", lambda name: None)
        assert loader.load("missing") is None
        assert loader.load("missing") is None
        assert "missing" not in loader._cache

    def test_load_missing_file(self, loader):
        assert loader.load("does_not_exist") is None

    def test_read_yaml_valid(self, loader, tmp_path):
        (tmp_path / "good.yaml").write_text("a: 1\nb: 2\n", encoding="utf-8")
        assert loader._read_yaml("good") == {"a": 1, "b": 2}

    def test_read_yaml_empty_file(self, loader, tmp_path):
        (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
        assert loader._read_yaml("empty") == {}

    def test_read_yaml_invalid_warns(self, loader, tmp_path, capsys):
        (tmp_path / "bad.yaml").write_text("a: [unclosed\n", encoding="utf-8")
        assert loader._read_yaml("bad") is None
        assert "加载 prompt 配置失败" in capsys.readouterr().err

    def test_read_yaml_unreadable_file(self, loader, tmp_path, capsys):
        (tmp_path / "dir.yaml").mkdir()
        assert loader._read_yaml("dir") is None

    def test_reload_single(self, loader, tmp_path, monkeypatch):
        (tmp_path / "a.yaml").write_text("v: 1\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("v: 1\n", encoding="utf-8")
        monkeypatch.setattr(loader, "_read_yaml", lambda name: {"v": 1})
        assert loader.load("a") == {"v": 1}
        assert loader.load("b") == {"v": 1}
        loader.reload("a")
        assert "a" not in loader._cache
        assert "b" in loader._cache

    def test_reload_all(self, loader, tmp_path, monkeypatch):
        monkeypatch.setattr(loader, "_read_yaml", lambda name: {"v": 1})
        loader.load("a")
        loader.load("b")
        loader.reload()
        assert loader._cache == {}

    def test_double_check_returns_cached(self, loader, tmp_path, monkeypatch):
        (tmp_path / "x.yaml").write_text("v: 1\n", encoding="utf-8")
        entered = threading.Event()
        release = threading.Event()
        real_read = PromptLoader._read_yaml

        def slow_read(name):
            entered.set()
            release.wait(timeout=10)
            return real_read(loader, name)

        monkeypatch.setattr(loader, "_read_yaml", slow_read)
        results = {}

        def t1():
            results["t1"] = loader.load("x")

        def t2():
            results["t2"] = loader.load("x")

        th1 = threading.Thread(target=t1)
        th1.start()
        assert entered.wait(timeout=10)
        th2 = threading.Thread(target=t2)
        th2.start()
        release.set()
        th1.join(timeout=10)
        th2.join(timeout=10)
        assert results["t1"] == {"v": 1}
        assert results["t2"] == {"v": 1}
        assert loader._cache["x"] == {"v": 1}


class TestGetLoader:
    def test_singleton(self, monkeypatch):
        import config.prompts.loader as mod
        monkeypatch.setattr(mod, "_loader", None)
        first = get_loader()
        second = get_loader()
        assert first is second
        assert isinstance(first, PromptLoader)

    def test_double_check_returns_existing(self, monkeypatch):
        import config.prompts.loader as mod
        entered = threading.Event()
        release = threading.Event()

        class SlowLoader(mod.PromptLoader):
            def __init__(self):
                entered.set()
                release.wait(timeout=10)
                super().__init__()

        monkeypatch.setattr(mod, "PromptLoader", SlowLoader)
        monkeypatch.setattr(mod, "_loader", None)
        results = {}

        def t1():
            results["t1"] = mod.get_loader()

        def t2():
            results["t2"] = mod.get_loader()

        th1 = threading.Thread(target=t1)
        th1.start()
        assert entered.wait(timeout=10)
        th2 = threading.Thread(target=t2)
        th2.start()
        release.set()
        th1.join(timeout=10)
        th2.join(timeout=10)
        assert results["t1"] is not None
        assert results["t2"] is results["t1"]
