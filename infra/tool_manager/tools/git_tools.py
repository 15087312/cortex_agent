"""
Git 工具包 — git_status, git_add, git_commit, git_push, git_pull, git_diff
"""
import subprocess
import os
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("git_tools")

GIT_TIMEOUT = 30


def _run_git(args: list, workdir: Optional[str] = None) -> Dict[str, Any]:
    """运行 git 命令"""
    try:
        cmd = ["git"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=workdir)
        return {
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"git 命令超时（{GIT_TIMEOUT}秒）", "success": False}
    except FileNotFoundError:
        return {"error": "git 未安装或不在 PATH 中", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


@ToolRegistry.register("git_status", description="查看当前 Git 仓库的状态。包括修改的文件、未跟踪文件、暂存区变更等。", params={"workdir": "可选，Git 仓库目录路径"}, risk_level="LOW", category="query")
def git_status(workdir: Optional[str] = None) -> Dict[str, Any]:
    r = _run_git(["status", "--porcelain"], workdir)
    if not r["success"]: return r
    lines = [l for l in r.get("stdout", "").split("\n") if l.strip()]
    staged = [l[3:] for l in lines if l[0] != " " and l[1] != " "]
    unstaged = [l[3:] for l in lines if l[0] == " " and l[1] != " "]
    untracked = [l[3:] for l in lines if l.startswith("??")]
    modified = [l[3:] for l in lines if l[1] == "M" or (l[0] == " " and l[1] == "M")]
    return {"success": True, "staged": staged, "unstaged": unstaged, "untracked": untracked, "modified": modified, "total": len(lines)}

@ToolRegistry.register("git_add", description="将文件添加到 Git 暂存区。仅开发 Agent 可用。", params={"path": "要添加的文件路径（或 '.' 添加所有）"}, risk_level="MEDIUM", category="admin", tags=["mutation"])
def git_add(path: str, workdir: Optional[str] = None) -> Dict[str, Any]:
    if not path: return {"error": "路径不能为空", "success": False}
    return _run_git(["add", path], workdir)

@ToolRegistry.register("git_commit", description="提交暂存区的变更到本地仓库。强制要求提交信息。", params={"message": "提交信息"}, risk_level="MEDIUM", category="admin", tags=["mutation"])
def git_commit(message: str, workdir: Optional[str] = None) -> Dict[str, Any]:
    if not message or not message.strip(): return {"error": "提交信息不能为空", "success": False}
    return _run_git(["commit", "-m", message.strip()], workdir)

@ToolRegistry.register("git_push", description="推送本地提交到远程仓库。禁止 --force 强制推送。需主管审批。", params={"remote": "可选，远程仓库名（默认 origin）", "branch": "可选，分支名"}, risk_level="HIGH", category="admin", tags=["mutation"])
def git_push(remote: str = "origin", branch: Optional[str] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    # 参数验证：防止注入 --force 等危险标志
    if remote.startswith("-"):
        return {"error": f"remote 参数不能以 '-' 开头: {remote}", "success": False}
    if branch and (branch.startswith("-") or "--force" in branch):
        return {"error": f"branch 参数包含非法内容: {branch}", "success": False}
    cmd = ["push", remote]
    if branch: cmd.append(branch)
    return _run_git(cmd, workdir)

@ToolRegistry.register("git_pull", description="从远程仓库拉取最新代码。", params={"remote": "可选，远程仓库名（默认 origin）", "branch": "可选，分支名"}, risk_level="LOW", category="query")
def git_pull(remote: str = "origin", branch: Optional[str] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    cmd = ["pull", remote]
    if branch: cmd.append(branch)
    return _run_git(cmd, workdir)

@ToolRegistry.register("git_diff", description="查看 Git 工作区/暂存区的代码差异。", params={"staged": "可选，是否查看暂存区差异（默认 False）", "path": "可选，限定文件路径"}, risk_level="LOW", category="query")
def git_diff(staged: bool = False, path: Optional[str] = None, workdir: Optional[str] = None) -> Dict[str, Any]:
    cmd = ["diff"]
    if staged: cmd.append("--cached")
    if path: cmd.extend(["--", path])
    r = _run_git(cmd, workdir)
    if r["success"]:
        diff = r.get("stdout", "")
        lines = diff.split("\n")
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        r["added_lines"] = added
        r["removed_lines"] = removed
        r["diff"] = diff
    return r
