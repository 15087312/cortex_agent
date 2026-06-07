"""
安全级别与核心规则定义
"""
from enum import Enum
from typing import List, Set, Tuple
import ast


class SecurityLevel(Enum):
    CORE = "L0"
    CONTENT = "L1"
    MODULE = "L2"
    EVOLVE = "L3"
    OUTPUT = "L4"


FORBIDDEN_SYSTEM_COMMANDS = [
    "rm -rf /", "rm -rf ~", "rm -rf .", "format", "mkfs", "dd if=",
    "chmod 777 /", "chown -R /", "系统还原", "重装系统",
    "删除系统文件", "修改启动项", "关闭防火墙", "卸载杀毒软件",
    "破解", "木马", "病毒", "挖矿", "端口扫描", "越权访问",
    "shutdown -h", "halt", "reboot", "init 0",
]


PROTECTED_CORE_MODULES = [
    "security_system", "security_api", "security_level",
    "global_monitor"
]


FORBIDDEN_CODE_PATTERNS = [
    "os.system('rm -rf", "subprocess.call(['rm",
    "eval(\"__import__", "exec(open('/etc/",
    "import os; os.remove", "__import__('os').system",
    "os.popen('rm", "shutil.rmtree"
]

# SEC-10: Dangerous modules and functions that should be restricted
DANGEROUS_MODULES: Set[str] = {
    "os", "subprocess", "sys", "socket", "importlib", "ctypes",
    "pickle", "marshal", "shelve", "dbm", "code", "codeop"
}

DANGEROUS_FUNCTIONS: Set[str] = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "globals", "locals", "vars", "dir", "delattr", "setattr",
    "getattr", "hasattr"
}


def _check_code_with_ast(code: str) -> Tuple[bool, str]:
    """SEC-10: Use AST parsing to detect dangerous patterns statically"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # 不是合法 Python 代码（可能是普通文本），跳过 AST 检查
        return True, ""

    for node in ast.walk(tree):
        # Check for dangerous imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in DANGEROUS_MODULES:
                    return False, f"禁止导入模块: {alias.name}"

        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module in DANGEROUS_MODULES:
                return False, f"禁止导入模块: {node.module}"

        # Check for dangerous function calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in DANGEROUS_FUNCTIONS:
                    return False, f"禁止调用函数: {node.func.id}"
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in {"system", "call", "popen", "run"}:
                    return False, f"禁止调用函数: {node.func.attr}"

    return True, ""
