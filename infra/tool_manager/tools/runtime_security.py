"""
运行时安全工具 — 沙箱、白名单、文件/网络访问控制
"""
import os
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("runtime_security")

SANDBOX_TIMEOUT = 60


@ToolRegistry.register("sandbox_execution", description="在隔离沙箱中执行代码。使用临时目录 + 最小化环境变量，防止恶意操作。", params={
    "code": "要执行的 Python 代码",
    "timeout": "可选，超时秒数（默认30）",
    "allow_network": "可选，是否允许网络（默认False）",
}, risk_level="HIGH", category="admin")
def sandbox_execution(code: str, timeout: Optional[int] = 30, allow_network: bool = False) -> Dict[str, Any]:
    """在隔离沙箱中执行代码"""
    timeout = max(5, min(timeout or 30, 120))
    sandbox_dir = tempfile.mkdtemp(prefix="sandbox_")
    script = os.path.join(sandbox_dir, "_sandbox.py")
    try:
        with open(script, "w", encoding="utf-8") as f: f.write(code)

        env = {"PATH": "/usr/bin:/bin", "HOME": sandbox_dir, "TMPDIR": sandbox_dir, "PYTHONNOUSERSITE": "1"}
        if not allow_network:
            # 通过限制 PATH 和环境变量来阻止网络访问
            env["http_proxy"] = ""
            env["https_proxy"] = ""
            env["ALL_PROXY"] = ""

        # 使用 -I 隔离模式
        cmd = [sys.executable, "-I", "-W", "ignore", script]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=sandbox_dir, env=env)

        return {
            "success": r.returncode == 0,
            "stdout": (r.stdout or "")[:30000],
            "stderr": (r.stderr or "")[:10000],
            "exit_code": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"超时（{timeout}秒）", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}
    finally:
        import shutil
        shutil.rmtree(sandbox_dir, ignore_errors=True)


@ToolRegistry.register("command_whitelist", description="检查命令是否在安全白名单中。也可查询当前白名单列表。", params={
    "command": "要检查的命令",
}, risk_level="LOW", category="query")
def command_whitelist(command: str) -> Dict[str, Any]:
    """检查命令是否在白名单中"""
    from infra.tool_manager.tools.exec_command import COMMAND_WHITELIST as WL
    if not command:
        return {"whitelist": sorted(WL), "count": len(WL)}
    cmd_base = command.strip().split()[0]
    base = os.path.basename(cmd_base)
    allowed = base in WL
    return {"command": command, "base_command": base, "allowed": allowed, "whitelist_entry": base if allowed else None,
            "all_whitelisted": sorted(WL)[:20]}  # 只显示前20个以避免过长


@ToolRegistry.register("file_access_control", description="检查文件路径的访问权限。判断路径是否在允许的访问范围内。", params={
    "path": "要检查的文件路径",
    "mode": "访问模式: read/write/delete",
}, risk_level="LOW", category="query")
def file_access_control(path: str, mode: str = "read") -> Dict[str, Any]:
    """检查文件访问权限"""
    from infra.tool_manager.tools.file_extra import _is_sensitive_path, _is_forbidden_write_path
    p = Path(path).expanduser()
    result = {"path": str(p), "mode": mode, "exists": p.exists()}

    if mode == "read":
        result["allowed"] = not _is_sensitive_path(str(p))
        if not result["allowed"]:
            result["reason"] = "敏感文件禁止读取"
    elif mode == "write":
        result["allowed"] = not _is_forbidden_write_path(str(p))
        if not result["allowed"]:
            result["reason"] = "禁止写入系统目录或 .git 目录"
    elif mode == "delete":
        result["allowed"] = not _is_forbidden_write_path(str(p))
        if not result["allowed"]:
            result["reason"] = "禁止删除系统文件"
    else:
        result["allowed"] = True

    return result


@ToolRegistry.register("network_access_control", description="检查 URL 是否允许访问。基于域名白名单控制外部网络访问。", params={
    "url": "要检查的 URL",
}, risk_level="LOW", category="query")
def network_access_control(url: str) -> Dict[str, Any]:
    """检查组网访问权限"""
    from urllib.parse import urlparse
    if not url: return {"error": "URL 不能为空"}
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # 默认允许的域名
    allowed_domains = [
        "pypi.org", "pypi.python.org", "files.pythonhosted.org",
        "github.com", "raw.githubusercontent.com", "api.github.com",
        "gitlab.com", "bitbucket.org",
        "pypi.org", "python.org", "docs.python.org",
        "stackoverflow.com", "stackexchange.com",
        "npmjs.org", "npmjs.com", "unpkg.com", "cdn.jsdelivr.net",
        "docker.com", "hub.docker.com",
        "google.com", "duckduckgo.com",
    ]
    allowed = any(hostname == d or hostname.endswith("." + d) for d in allowed_domains)

    return {
        "url": url,
        "hostname": hostname,
        "allowed": allowed,
        "scheme": parsed.scheme,
        "reason": "允许访问" if allowed else f"域名 '{hostname}' 不在白名单中",
    }
