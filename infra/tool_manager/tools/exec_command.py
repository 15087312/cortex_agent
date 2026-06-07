"""
命令执行工具 — shell 命令执行、Python 沙箱执行、进程管理

- exec_command: 通用 shell 执行（高权限）— 带危险命令检测
- run_command: 白名单管控的命令执行（仅允许安全命令）
- run_python: Python 代码沙箱执行（AST 验证 + 资源限制）
- kill_process: 杀死进程
"""
import subprocess
import os
import signal
import time
import sys
import tempfile
import ast
import shlex
from typing import Dict, Any, Optional, List

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("exec")

MAX_OUTPUT_LENGTH = 50000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600

# 命令白名单 — run_command 只允许这些命令（安全命令，不含破坏性操作）
COMMAND_WHITELIST = {
    "python", "python3", "pip", "pip3",
    "git", "pytest", "ruff", "black", "mypy", "flake8",
    "node", "npm", "npx", "yarn",
    "cat", "head", "tail", "wc", "sort", "uniq",
    "ls", "find", "grep", "rg", "ag", "ack",
    "echo", "printf",
    "which", "where",
    "mkdir", "cp", "mv",
    "tar", "gzip", "gunzip", "zip", "unzip",
    "make", "cmake",
    "env", "pwd", "date", "whoami",
}

# exec_command 危险命令模式 — 检测到时在响应中添加警告
_DANGEROUS_PATTERNS = [
    "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf .",
    "mkfs.", "dd if=", "> /dev/sd",
    ":(){ :|:& };:",  # fork bomb
    "chmod 777 /", "chmod -R 777 /",
    "curl.*|.*sh", "wget.*|.*sh",  # pipe to shell
    "nc -l", "ncat -l",  # reverse shell
    "/etc/shadow", "/etc/passwd",
]


def _check_command_whitelist(command: str) -> bool:
    """检查命令是否在白名单中"""
    cmd = command.strip().split()[0] if command.strip() else ""
    base = os.path.basename(cmd)
    return base in COMMAND_WHITELIST


def _detect_dangerous_command(command: str) -> List[str]:
    """检测命令中的危险模式，返回匹配的警告列表"""
    warnings = []
    cmd_lower = command.lower().strip()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.lower() in cmd_lower:
            warnings.append(f"检测到危险模式: '{pattern}'")
    # 检测链式命令（; && || |）中的危险操作
    if any(op in command for op in [";", "&&", "||", "|"]):
        parts = []
        for sep in [";", "&&", "||", "|"]:
            parts.extend(command.split(sep))
        for part in parts:
            part = part.strip()
            if part:
                base = os.path.basename(part.split()[0]) if part.split() else ""
                if base in ("rm", "mkfs", "dd", "nc", "ncat"):
                    warnings.append(f"链式命令中包含高危命令: '{base}'")
    return warnings


def _validate_python_ast(code: str) -> Optional[str]:
    """AST 验证 Python 代码安全性，返回错误信息或 None"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"语法错误: {e}"

    # 禁止的 AST 节点类型
    FORBIDDEN_IMPORTS = {
        "subprocess", "shutil", "socket", "http", "urllib",
        "ftplib", "smtplib", "ctypes", "importlib",
    }

    for node in ast.walk(tree):
        # import subprocess/shutil/socket 等
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in FORBIDDEN_IMPORTS:
                    return f"禁止导入模块: '{alias.name}'"
        # from subprocess import ...
        if isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if root_module in FORBIDDEN_IMPORTS:
                    return f"禁止从 '{node.module}' 导入"
        # exec()/eval() 调用
        if isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name in ("exec", "eval", "compile", "__import__", "getattr"):
                return f"禁止调用: '{func_name}()'"
        # os.system / os.popen 等属性调用
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id in ("os", "sys") and node.func.attr in ("system", "popen", "exec"):
                    return f"禁止调用: '{node.func.value.id}.{node.func.attr}()'"
    return None


@ToolRegistry.register(
    "exec_command",
    description=(
        "【高权限】在系统终端中执行任意 shell 命令。支持超时和工作目录。"
        "可用于运行脚本、编译代码、执行测试等。注意：此工具无命令限制，但会检测危险模式。"
    ),
    params={
        "command": "要执行的 shell 命令",
        "timeout": "可选，超时秒数（默认60，最大600）",
        "workdir": "可选，工作目录路径",
    },
    risk_level="HIGH",
    category="admin",
    core=True,
)
def exec_command(command: str, timeout: Optional[int] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    """执行任意 shell 命令（高权限）— 带危险模式检测"""
    if not command or not command.strip():
        return {"error": "命令不能为空", "exit_code": -1}

    try:
        timeout = int(timeout) if timeout is not None else DEFAULT_TIMEOUT
    except (ValueError, TypeError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    # 危险命令检测（不阻止，但在响应中警告）
    danger_warnings = _detect_dangerous_command(command)

    try:
        start = time.time()
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=workdir,
        )
        elapsed = time.time() - start
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + f"\n... (截断, {len(stdout)} 字符)"
        if len(stderr) > MAX_OUTPUT_LENGTH:
            stderr = stderr[:MAX_OUTPUT_LENGTH] + f"\n... (截断, {len(stderr)} 字符)"
        resp = {"stdout": stdout, "stderr": stderr, "exit_code": result.returncode, "elapsed_seconds": round(elapsed, 2)}
        if danger_warnings:
            resp["security_warnings"] = danger_warnings
            logger.warning(f"exec_command 危险命令检测: {danger_warnings}, command={command[:100]}")
        return resp
    except subprocess.TimeoutExpired:
        return {"error": f"超时（{timeout}秒）", "exit_code": -1}
    except Exception as e:
        return {"error": f"执行失败: {e}", "exit_code": -1}


@ToolRegistry.register(
    "run_command",
    description=(
        "【白名单管控】执行受白名单限制的 shell 命令。"
        "只允许执行 python、pip、git、pytest、ruff、black 等安全命令。"
        "需要执行其他命令请使用 exec_command。"
    ),
    params={
        "command": "要执行的命令（必须是白名单内的命令）",
        "timeout": "可选，超时秒数（默认60）",
        "workdir": "可选，工作目录路径",
    },
    risk_level="MEDIUM",
    category="admin",
)
def run_command(command: str, timeout: Optional[int] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    """执行白名单管控的命令"""
    if not command or not command.strip():
        return {"error": "命令不能为空", "exit_code": -1}
    if not _check_command_whitelist(command):
        base = command.strip().split()[0]
        return {
            "error": f"命令 '{base}' 不在白名单中。允许的命令: {', '.join(sorted(COMMAND_WHITELIST)[:15])}...",
            "exit_code": -1,
        }
    return exec_command(command, timeout, workdir)


@ToolRegistry.register(
    "run_python",
    description=(
        "执行 Python 代码片段（沙箱模式）。"
        "在临时目录中执行，禁止导入危险模块（subprocess/shutil/socket 等）和调用 exec/eval。"
        "使用 AST 静态分析 + 资源限制。返回 stdout/stderr/exit_code。"
    ),
    params={
        "code": "要执行的 Python 代码",
        "timeout": "可选，超时秒数（默认30）",
    },
    risk_level="MEDIUM",
    category="admin",
    core=True,
)
def run_python(code: str, timeout: Optional[int] = 30) -> Dict[str, Any]:
    """在沙箱中执行 Python 代码 — AST 验证 + 资源限制"""
    if not code or not code.strip():
        return {"error": "Python 代码不能为空"}

    try:
        timeout = int(timeout) if timeout is not None else 30
    except (ValueError, TypeError):
        timeout = 30
    timeout = max(5, min(timeout, 120))

    # AST 静态分析 — 比字符串匹配更可靠
    ast_error = _validate_python_ast(code)
    if ast_error:
        return {"error": f"安全检查失败: {ast_error}"}

    # 在临时目录中用隔离的子进程执行
    sandbox_dir = tempfile.mkdtemp(prefix="pysandbox_")
    script_path = os.path.join(sandbox_dir, "_script.py")

    # 生成沙箱包装脚本 — 添加资源限制
    wrapper_code = f"""\
import resource
import sys

# 资源限制：内存 256MB，CPU 时间 {timeout}s
try:
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, ({timeout}, {timeout}))
except (ValueError, OSError):
    pass  # 某些系统不支持

# 禁止的内建函数
_builtins = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
_builtins['__import__'] = lambda name, *a, **kw: (_ for _ in ()).throw(
    ImportError(f"沙箱禁止导入: {{name}}")
) if name.split('.')[0] in {{'subprocess','shutil','socket','http','urllib','ftplib','smtplib','ctypes','importlib'}} else __import__(name, *a, **kw)

# 执行用户代码
exec(open("{script_path}").read())
"""

    wrapper_path = os.path.join(sandbox_dir, "_wrapper.py")
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
        with open(wrapper_path, "w", encoding="utf-8") as f:
            f.write(wrapper_code)

        start = time.time()
        result = subprocess.run(
            [sys.executable, wrapper_path],
            capture_output=True, text=True, timeout=timeout + 2,
            cwd=sandbox_dir,
            env={"PATH": "/usr/bin:/bin", "HOME": sandbox_dir},
        )
        elapsed = time.time() - start

        return {
            "stdout": (result.stdout or "")[:MAX_OUTPUT_LENGTH],
            "stderr": (result.stderr or "")[:MAX_OUTPUT_LENGTH],
            "exit_code": result.returncode,
            "elapsed_seconds": round(elapsed, 2),
        }
    except subprocess.TimeoutExpired:
        return {"error": f"超时（{timeout}秒）", "exit_code": -1}
    except Exception as e:
        return {"error": f"执行失败: {e}", "exit_code": -1}
    finally:
        try:
            import shutil
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception as e:
            logger.debug(f"沙箱目录清理失败 (非致命): {e}")


@ToolRegistry.register(
    "kill_process",
    description=(
        "杀死指定 PID 的进程。只能杀死当前用户启动的进程。"
        "需要主管审批。返回杀死结果。"
    ),
    params={
        "pid": "要杀死的进程 ID",
        "force": "可选，是否强制杀死（SIGKILL），默认 SIGTERM",
    },
    risk_level="HIGH",
    category="admin",
)
def kill_process(pid: int, force: bool = False) -> Dict[str, Any]:
    """杀死进程"""
    try:
        pid = int(pid)
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        return {
            "success": True,
            "pid": pid,
            "signal": "SIGKILL" if force else "SIGTERM",
        }
    except ProcessLookupError:
        return {"error": f"进程 {pid} 不存在"}
    except PermissionError:
        return {"error": f"无权限杀死进程 {pid}（只能杀死当前用户进程）"}
    except Exception as e:
        return {"error": f"杀死进程失败: {e}"}
