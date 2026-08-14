"""modules/security_system/validators/core_validator 防御分支测试（L0 核心校验）"""
from modules.security_system.validators.core_validator import CoreValidator


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

def test_validate_all_system_command_fail_short_circuit():
    ok, reason = CoreValidator.validate_all("rm -rf /")
    assert ok is False
    assert "高危系统指令" in reason


def test_validate_all_code_safety_fail():
    ok, reason = CoreValidator.validate_all("import os")
    assert ok is False
    assert "禁止导入模块" in reason


def test_validate_all_pass():
    ok, result = CoreValidator.validate_all("写一段 Python 代码打印 hello")
    assert ok is True
    assert result == "写一段 Python 代码打印 hello"
