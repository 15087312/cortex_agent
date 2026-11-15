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


# ── perception.system 端口：接管要连本包内部的用法一起换 ────────────────────


def test_accessor_follows_port_takeover(take_over_port):
    """访问器端口优先：接管 perception.system 后包内调用点也拿到插件那份。

    管理面本来就经端口取，而 integration.py 直接 import 这个访问器——之前接管只换掉
    前者，同一个感知系统跑出两套行为。
    """
    import modules.perception as p_mod

    replacement = _fake_system(started=True)
    take_over_port("perception.system", replacement)
    assert p_mod.get_perception_system() is replacement


def test_builtin_provider_is_not_the_accessor(monkeypatch):
    """端口 provider 必须指向内置实现，不能指回端口优先访问器本身。

    ``as_factory(get_perception_system)`` 会造出端口→访问器→端口的无限递归。断言走行为
    而不是比两个函数对象：把访问器换成「一调用就炸」，端口仍必须解析出内置那份。
    """
    import modules.perception as p_mod
    import modules.perception.setup as setup_mod

    sentinel = object()
    monkeypatch.setattr(setup_mod, "get_perception_system", lambda: sentinel)

    def _recursion():
        raise AssertionError("端口 provider 走到了访问器：这是递归接线")

    monkeypatch.setattr(p_mod, "get_perception_system", _recursion)
    from infra import ports

    assert ports.resolve("perception.system") is sentinel


def test_manager_port_follows_perception_system_takeover(take_over_port):
    """perception.manager 登记成工厂：换感知系统时兼容代理跟着换，不必再接管第二条。"""
    from infra import ports

    replacement = _fake_system(started=False)
    take_over_port("perception.system", replacement)
    manager = ports.resolve("perception.manager")
    manager.start_monitoring()
    replacement.setup.assert_called_once()
    replacement.start.assert_called_once()


def test_accessor_falls_back_to_builtin(drop_port, monkeypatch):
    """端口没接线时回退内置单例（脚本、没 import 过本包的入口）。"""
    import modules.perception as p_mod
    import modules.perception.setup as setup_mod

    fake = _fake_system(started=True)
    monkeypatch.setattr(setup_mod, "get_perception_system", lambda: fake)
    drop_port("perception.system")
    assert p_mod.get_perception_system() is fake

