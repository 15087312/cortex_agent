"""bootstrap — 装配层的自创工具恢复：时机有保证，失败不传染。

恢复原本挂在 tools/__init__.py 的 import 上（见 test_tools_init.py）。移到这里
之后要保证两件事：它跑在内置工具集就位之后（否则「自创工具先占名、内置随后被
DuplicateToolError 拒掉」会成为顺序依赖），以及它坏了不拖垮整个装配。
"""
from unittest.mock import MagicMock

import bootstrap
from infra.tool_manager.tool_registry import ToolRegistry
from infra.tool_manager.tools import ai_tools


def test_restore_runs_after_builtin_tools(monkeypatch):
    seen: dict = {}

    def fake_restore() -> int:
        seen["builtin_ready"] = ToolRegistry.get_tool("exec_command") is not None
        return 0

    monkeypatch.setattr(ai_tools, "restore_ai_tools", fake_restore)

    bootstrap.register_business_capabilities()

    assert seen["builtin_ready"] is True


def test_restore_failure_does_not_break_assembly(monkeypatch):
    """恢复失败只告警：自创工具是可选增强，缺了它后端仍应能起来。"""
    restore = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(ai_tools, "restore_ai_tools", restore)
    warning = MagicMock()
    monkeypatch.setattr(bootstrap.logger, "warning", warning)

    bootstrap.register_business_capabilities()

    assert restore.call_count == 1
    msgs = [c[0][0] for c in warning.call_args_list]
    assert any("AI 自创工具恢复失败" in m and "boom" in m for m in msgs)
