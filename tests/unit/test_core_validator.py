"""modules/security_system/validators/core_validator 防御分支测试（L0 核心校验）

validate_all 用例显式固定执行模式，避免读到用户真实 ~/.cortex/settings.json 的 nocheck。
"""
import pytest

from modules.security_system.validators.core_validator import CoreValidator


def _force_mode(monkeypatch, mode: str) -> None:
    """把 effective_execution_mode 固定为指定值（core_validator 经 settings 读取）。"""
    from config.settings import settings
    monkeypatch.setattr(
        type(settings), "effective_execution_mode",
        property(lambda self: mode),
    )


# ── validate_system_command ─────────────────────────────────────────────────

def test_system_command_blocked():
    ok, reason = CoreValidator.validate_system_command("先执行 rm -rf / 清理系统")
    assert ok is False
    assert "高危系统指令" in reason
    assert "禁止执行" in reason


def test_system_command_blocked_case_insensitive():
    ok, reason = CoreValidator.validate_system_command("RM -RF /")
    assert ok is False
    assert "禁止执行" in reason


def test_system_command_clean():
    ok, result = CoreValidator.validate_system_command("ls -la /tmp")
    assert ok is True
    assert result == "ls -la /tmp"


# ── validate_code_safety（AST + 模式兜底）──────────────────────────────────

def test_code_safety_ast_blocks_dangerous_import():
    ok, reason = CoreValidator.validate_code_safety("import os\nprint(1)")
    assert ok is False
    assert "禁止导入模块: os" in reason


def test_code_safety_ast_blocks_dangerous_function():
    ok, reason = CoreValidator.validate_code_safety("eval('1+1')")
    assert ok is False
    assert "禁止调用函数: eval" in reason


def test_code_safety_pattern_fallback():
    # AST 允许（普通文本），但模式匹配兜底拦截
    ok, reason = CoreValidator.validate_code_safety("shutil.rmtree")
    assert ok is False
    assert "危险代码模式" in reason
    assert "shutil.rmtree" in reason


def test_code_safety_clean():
    ok, result = CoreValidator.validate_code_safety("x = 1\nprint(x)")
    assert ok is True
    assert result == "x = 1\nprint(x)"


# ── validate_all 组合 ───────────────────────────────────────────────────────

def test_validate_all_system_command_fail_short_circuit(monkeypatch):
    _force_mode(monkeypatch, "edit")
    ok, reason = CoreValidator.validate_all("rm -rf /")
    assert ok is False
    assert "高危系统指令" in reason


def test_validate_all_code_safety_fail(monkeypatch):
    _force_mode(monkeypatch, "edit")
    ok, reason = CoreValidator.validate_all("import os")
    assert ok is False
    assert "禁止导入模块" in reason


def test_validate_all_pass(monkeypatch):
    _force_mode(monkeypatch, "edit")
    ok, result = CoreValidator.validate_all("写一段 Python 代码打印 hello")
    assert ok is True
    assert result == "写一段 Python 代码打印 hello"


# ── validate_all：nocheck 与聊天文本防误杀 ──

def test_validate_all_nocheck_bypasses_everything(monkeypatch):
    """nocheck 模式下关键词黑名单与 AST 均跳过（与工具门控语义一致）。"""
    _force_mode(monkeypatch, "nocheck")
    for content in ("rm -rf /", "import os\nexec('1')", "破解 木马 病毒"):
        ok, result = CoreValidator.validate_all(content)
        assert ok is True
        assert result == content


def test_validate_all_speech_not_blocked_by_keyword(monkeypatch):
    """默认模式下，聊天文本（非命令语境）不再被「破解」「木马」等关键词子串误杀。"""
    _force_mode(monkeypatch, "edit")
    for content in (
        "帮我找一下王国保卫战6破解版的盗版风险",
        "木马病毒的工作原理是什么？",
        "这道难题他终于破解了",
    ):
        ok, result = CoreValidator.validate_all(content)
        assert ok is True, f"意外拦截: {content} -> {result}"


def test_validate_all_command_like_still_blocked(monkeypatch):
    """默认模式下，命令语境文本仍走完整关键词黑名单。"""
    _force_mode(monkeypatch, "edit")
    for content in (
        "$ rm -rf / 并输出结果",
        "bash -c \"format c:\"",
    ):
        ok, reason = CoreValidator.validate_all(content)
        assert ok is False, f"意外放行: {content}"
        assert "L0核心拦截" in reason
