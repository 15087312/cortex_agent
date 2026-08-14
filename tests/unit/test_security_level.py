"""security_level.py — 安全级别 + AST 静态危险代码检测"""
from modules.security_system.security_level import (
    DANGEROUS_FUNCTIONS,
    DANGEROUS_MODULES,
    SecurityLevel,
    _check_code_with_ast,
)


def test_security_level_enum_values():
    assert SecurityLevel.CORE.value == "L0"
    assert SecurityLevel.CONTENT.value == "L1"
    assert SecurityLevel.OUTPUT.value == "L4"


def test_forbidden_lists_nonempty():
    from modules.security_system.security_level import (
        FORBIDDEN_SYSTEM_COMMANDS,
        PROTECTED_CORE_MODULES,
        FORBIDDEN_CODE_PATTERNS,
    )
    assert FORBIDDEN_SYSTEM_COMMANDS
    assert PROTECTED_CORE_MODULES
    assert FORBIDDEN_CODE_PATTERNS


def test_ast_clean_code_passes():
    ok, msg = _check_code_with_ast("x = 1 + 2\ndef f():\n    return x")
    assert ok is True
    assert msg == ""


def test_ast_syntax_error_skips():
    ok, msg = _check_code_with_ast("这不是 Python 代码 ###")
    assert ok is True
    assert msg == ""


def test_ast_blocks_import_dangerous_module():
    ok, msg = _check_code_with_ast("import os")
    assert ok is False
    assert "禁止导入模块: os" in msg


def test_ast_blocks_import_as_dangerous_module():
    ok, msg = _check_code_with_ast("import socket as s")
    assert ok is False
    assert "socket" in msg


def test_ast_blocks_importfrom_dangerous_module():
    ok, msg = _check_code_with_ast("from subprocess import run")
    assert ok is False
    assert "禁止导入模块: subprocess" in msg


def test_ast_blocks_dangerous_function_call():
    ok, msg = _check_code_with_ast("eval('1+1')")
    assert ok is False
    assert "禁止调用函数: eval" in msg


def test_ast_blocks_attribute_call():
    ok, msg = _check_code_with_ast("os.system('ls')")
    assert ok is False
    assert "禁止调用函数: system" in msg


def test_ast_blocks_popen_attribute():
    ok, msg = _check_code_with_ast("os.popen('ls')")
    assert ok is False
    assert "popen" in msg


def test_ast_safe_imports_pass():
    ok, msg = _check_code_with_ast("import json\nimport math")
    assert ok is True


def test_all_dangerous_modules_detected():
    for mod in sorted(DANGEROUS_MODULES):
        ok, msg = _check_code_with_ast(f"import {mod}")
        assert ok is False, f"{mod} 应被拦截"
        assert mod in msg


def test_dangerous_function_names_present():
    assert {"eval", "exec", "compile", "__import__", "open"} <= DANGEROUS_FUNCTIONS


# ── AST 遍历 fall-through 分支（node 不命中危险名单 → 继续循环）──────────────

def test_ast_importfrom_safe_module_continues():
    ok, msg = _check_code_with_ast("from json import dumps\ndumps({'a': 1})")
    assert ok is True
    assert msg == ""


def test_ast_importfrom_no_module_continues():
    ok, msg = _check_code_with_ast("from . import sibling")
    assert ok is True
    assert msg == ""


def test_ast_call_safe_name_continues():
    ok, msg = _check_code_with_ast("print('hello')")
    assert ok is True
    assert msg == ""


def test_ast_call_non_name_non_attribute_func_continues():
    # func 是 Lambda/其它表达式（既非 Name 也非 Attribute）→ 74->58 继续
    ok, msg = _check_code_with_ast("(lambda: 1)()")
    assert ok is True
    assert msg == ""


def test_ast_call_safe_attribute_continues():
    # Attribute 但 attr 不在 {system, call, popen, run} → 75->58 继续
    ok, msg = _check_code_with_ast("obj.upper('x')")
    assert ok is True
    assert msg == ""


def test_ast_call_dangerous_attribute_blocks():
    ok, msg = _check_code_with_ast("proc.call('ls')")
    assert ok is False
    assert "call" in msg
