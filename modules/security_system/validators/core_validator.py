"""
核心校验器 - L0级，永远开启不可关闭
"""
from typing import Tuple
from ..security_level import FORBIDDEN_SYSTEM_COMMANDS, PROTECTED_CORE_MODULES, FORBIDDEN_CODE_PATTERNS, _check_code_with_ast
from utils.logger import setup_logger

logger = setup_logger("core_validator")


class CoreValidator:
    @staticmethod
    def validate_system_command(content: str) -> Tuple[bool, str]:
        content_lower = content.lower()
        for cmd in FORBIDDEN_SYSTEM_COMMANDS:
            if cmd.lower() in content_lower:
                logger.warning(f"[L0拦截] 检测到高危指令: {cmd}")
                return False, f"[L0核心拦截] 检测到高危系统指令「{cmd}」，禁止执行"
        return True, content

    @staticmethod
    def validate_module_protect(module_name: str) -> Tuple[bool, str]:
        if module_name in PROTECTED_CORE_MODULES:
            logger.warning(f"[L0拦截] 尝试修改受保护模块: {module_name}")
            return False, f"[L0核心拦截] 禁止修改受保护的核心模块「{module_name}」"
        return True, module_name

    @staticmethod
    def validate_code_safety(code: str) -> Tuple[bool, str]:
        # SEC-10: Use AST-based static analysis for robust code checking
        passed, ast_error = _check_code_with_ast(code)
        if not passed:
            logger.warning(f"[L0拦截] AST检测到危险代码: {ast_error}")
            return False, f"[L0核心拦截] {ast_error}"

        # Fall back to pattern matching for additional safety
        for pattern in FORBIDDEN_CODE_PATTERNS:
            if pattern in code:
                logger.warning(f"[L0拦截] 检测到危险代码模式: {pattern}")
                return False, f"[L0核心拦截] 检测到危险代码模式「{pattern}」"

        return True, code

    @staticmethod
    def validate_all(content: str) -> Tuple[bool, str]:
        passed, result = CoreValidator.validate_system_command(content)
        if not passed:
            return False, result
        passed, result = CoreValidator.validate_code_safety(content)
        if not passed:
            return False, result
        return True, content
