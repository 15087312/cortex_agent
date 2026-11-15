"""tools/__init__.py — 导入期扫描的范围（下划线模块跳过 / 恢复自创工具已不在这里）

通过 reload 触发模块级循环的未覆盖路径；所有导入动作均被 mock，
不产生真实副作用（ToolRegistry 已在会话开始时完成注册）。
"""
import importlib
import pkgutil
from unittest.mock import MagicMock

import infra.tool_manager.tools as tm


class _FakeInfo:
    name = "_private"
    ispkg = False


def _stub_scan(monkeypatch, modules=()):
    monkeypatch.setattr(pkgutil, "iter_modules",
                        lambda *a, **k: [type("_I", (), {"name": m})() for m in modules])
    monkeypatch.setattr(importlib, "import_module", MagicMock())


def test_skips_underscore_modules(monkeypatch):
    monkeypatch.setattr(pkgutil, "iter_modules", lambda *a, **k: [_FakeInfo()])
    monkeypatch.setattr(importlib, "import_module", MagicMock())
    importlib.reload(tm)
    assert "_private" not in tm._imported


def test_import_does_not_restore_ai_tools(monkeypatch):
    """恢复自创工具（读 data/ai_tools.json + exec 模型写的源码）不再是 import 副作用。

    import 期注册不可撤销，而且谁覆盖谁取决于哪个模块先被 import。现在这一步
    由 bootstrap.register_business_capabilities() 在内置工具就位后显式做。
    """
    restore = MagicMock(return_value=0)
    monkeypatch.setattr(tm.ai_tools, "restore_ai_tools", restore)
    _stub_scan(monkeypatch)

    importlib.reload(tm)

    assert restore.call_count == 0
    assert tm.__all__ == []
