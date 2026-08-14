"""modules/perception/__init__ — 模块级导入降级 + 向后兼容代理（perception_manager）"""
import importlib
import sys
from unittest.mock import MagicMock

import pytest


def test_integration_import_failure_falls_back_to_none(monkeypatch):
    """integration.py 导入失败 → PerceptionIntegrator=None，不阻塞模块导入（14-15）"""
    import modules.perception as p_mod

    monkeypatch.setitem(sys.modules, "modules.perception.integration", None)
    reloaded = importlib.reload(p_mod)
    assert reloaded.PerceptionIntegrator is None
    # 恢复：重新加载让 integration 正常导入
    importlib.reload(p_mod)


def _fake_system(started=False):
    fake = MagicMock()
    fake._started = started
    return fake


def test_compat_proxy_running_property(monkeypatch):
    import modules.perception as p_mod
    import modules.perception.setup as setup_mod

    fake = _fake_system(started=True)
    monkeypatch.setattr(setup_mod, "get_perception_system", lambda: fake)
    proxy = p_mod._get_compat_proxy()
    assert proxy._running is True


def test_compat_proxy_start_monitoring_when_stopped(monkeypatch):
    import modules.perception as p_mod
    import modules.perception.setup as setup_mod

    fake = _fake_system(started=False)
    monkeypatch.setattr(setup_mod, "get_perception_system", lambda: fake)
    proxy = p_mod._get_compat_proxy()
    proxy.start_monitoring()
    fake.setup.assert_called_once()
    fake.start.assert_called_once()


def test_compat_proxy_start_monitoring_when_already_started(monkeypatch):
    import modules.perception as p_mod
    import modules.perception.setup as setup_mod

    fake = _fake_system(started=True)
    monkeypatch.setattr(setup_mod, "get_perception_system", lambda: fake)
    proxy = p_mod._get_compat_proxy()
    proxy.start_monitoring()
    fake.setup.assert_not_called()
    fake.start.assert_not_called()


def test_compat_proxy_stop_monitoring(monkeypatch):
    import modules.perception as p_mod
    import modules.perception.setup as setup_mod

    fake = _fake_system(started=True)
    monkeypatch.setattr(setup_mod, "get_perception_system", lambda: fake)
    proxy = p_mod._get_compat_proxy()
    proxy.stop_monitoring()
    fake.stop.assert_called_once()
