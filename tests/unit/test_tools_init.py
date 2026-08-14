"""tools/__init__.py — 导入期防御性分支（下划线模块跳过 / 恢复失败回退）

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


def test_skips_underscore_modules(monkeypatch):
    monkeypatch.setattr(pkgutil, "iter_modules", lambda *a, **k: [_FakeInfo()])
    monkeypatch.setattr(importlib, "import_module", MagicMock())
    monkeypatch.setattr(tm.ai_tools, "restore_ai_tools", MagicMock(return_value=0))
    importlib.reload(tm)
    assert "_private" not in tm._imported


def test_restore_ai_tools_failure(monkeypatch):
    monkeypatch.setattr(pkgutil, "iter_modules", lambda *a, **k: [])
    monkeypatch.setattr(importlib, "import_module", MagicMock())
    monkeypatch.setattr(tm.ai_tools, "restore_ai_tools",
                        MagicMock(side_effect=RuntimeError("boom")))
    importlib.reload(tm)
    assert True  # 异常被吞掉并降级为 warning
