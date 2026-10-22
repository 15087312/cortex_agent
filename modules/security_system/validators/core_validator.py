# @link:block CoreValidator_Guard = Guard
"""
核心校验器 - L0级，永远开启不可关闭

语义（2026-10 调整）：
- validate_system_command / validate_code_safety：单体严格校验，调用方显式绕不过。
- validate_all（聊天输入/输出文本入口）：
  1. 显式 nocheck 模式（settings.EXECUTION_MODE = "nocheck"）下整体放行，
     与 tool_security_gate 的「完全不审查」语义保持一致；
  2. 关键词扫描仅在文本「像系统命令」时生效，普通聊天不再做子串扫黑名单（防误杀）。
- 真正执行命令的安全兜底在 infra/tool_manager/tools/exec_command.py 的
  极端危险硬阻断 + tool_security_gate 审批流，与本文件相互独立。
"""
from typing import Tuple
from ..security_level import FORBIDDEN_SYSTEM_COMMANDS, FORBIDDEN_CODE_PATTERNS, _check_code_with_ast
from utils.logger import setup_logger

logger = setup_logger("core_validator")

# 自由文本被判定为"命令语境"的启发式前缀（行首、忽略大小写）。
# 只有命中才对整段文本运行高危关键词扫描，避免「破解」「木马」等词在聊天内容中被误杀。
_COMMAND_LIKE_PREFIXES = (
    "rm ", "rmdir ", "dd if", "mkfs", "sudo ", "su -", "chmod", "chown",
    "shutdown", "halt", "reboot", "init 0", "kill ", "killall",
    "curl ", "wget ", "nc -", "netcat ", "bash -c", "sh -c", "zsh -c",
    "powershell", "cmd /c", "start-process ", "format ", "mount ", "umount ",
    "apt-get remove", "yum remove", "brew uninstall", "pip uninstall",
)


class CoreValidator:
    @staticmethod
    def _mode_nocheck() -> bool:
        """读取执行模式是否为 nocheck（完全放行），读取失败按默认模式处理。"""
        try:
            from config.settings import settings
            return settings.effective_execution_mode == "nocheck"
        except Exception as _swallowed_err:
            logger.debug(f"[_mode_nocheck] ignored Exception: {_swallowed_err}")
            return False

    @staticmethod
    def _looks_like_system_command(text: str) -> bool:
        """判断自由文本中是否存在命令语境行（行首匹配常见 shell/系统操作前缀）。"""
        if not text:
            return False
        for line in text.splitlines():
            s = line.strip().lower().lstrip("$#>-!?:;").strip()
            if not s:
                continue
            for prefix in _COMMAND_LIKE_PREFIXES:
                if s.startswith(prefix):
                    return True
        return False

    @staticmethod
    def validate_system_command(content: str) -> Tuple[bool, str]:
        """命令字符串严格校验（含聊天文本绕不过的子串黑名单），供命令执行语境直接调用。"""
        content_lower = content.lower()
        for cmd in FORBIDDEN_SYSTEM_COMMANDS:
            if cmd.lower() in content_lower:
                logger.warning(f"[L0拦截] 检测到高危指令: {cmd}")
                return False, f"[L0核心拦截] 检测到高危系统指令「{cmd}」，禁止执行"
        return True, content

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
        # ── nocheck 模式：用户显式开启的完全放行模式，跳过全部 L0 校验（含 AST 兜底） ──
        if CoreValidator._mode_nocheck():
            logger.warning("[L0绕过] nocheck 模式：跳过关键词与代码安全校验")
            return True, content

        # ── 关键词扫描仅在命令语境生效，防止聊天文本被子串匹配误杀 ──
        if CoreValidator._looks_like_system_command(content):
            passed, result = CoreValidator.validate_system_command(content)
            if not passed:
                return False, result

        passed, result = CoreValidator.validate_code_safety(content)
        if not passed:
            return False, result
        return True, content
