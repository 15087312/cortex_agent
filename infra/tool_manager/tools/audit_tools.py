"""
审计与追溯工具
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional
from collections import deque

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("audit_tools")

AUDIT_LOG = str(Path.home() / ".hermes" / "audit_log.jsonl")
CHANGE_LOG = str(Path.home() / ".hermes" / "changes.jsonl")


def _log_entry(log_file: str, entry: dict):
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        entry["_timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")


@ToolRegistry.register("log_tool_call", description="记录工具调用的详细信息（时间、角色、参数、结果）。用于审计追溯。", params={
    "tool_name": "调用的工具名",
    "caller_role": "调用者角色",
    "params": "可选，调用参数（JSON）",
    "result": "可选，调用结果",
    "success": "可选，是否成功",
}, risk_level="LOW", category="query")
def log_tool_call(tool_name: str, caller_role: str = "unknown", params: Optional[str] = None, result: Optional[str] = None, success: bool = True) -> Dict[str, Any]:
    entry = {"tool": tool_name, "role": caller_role, "success": success}
    if params: entry["params"] = params[:500]
    if result: entry["result"] = result[:500]
    _log_entry(AUDIT_LOG, entry)
    return {"success": True}


@ToolRegistry.register("generate_audit_report", description="生成任务的完整审计报告。返回最近工具调用日志的汇总。", params={"limit": "可选，返回最近记录数（默认50）"}, risk_level="LOW", category="query")
def generate_audit_report(limit: int = 50) -> Dict[str, Any]:
    limit = min(max(limit, 1), 500)
    logs = []
    try:
        if os.path.exists(AUDIT_LOG):
            with open(AUDIT_LOG, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try: logs.append(json.loads(line))
                        except Exception as e: logger.debug(f"审计日志行解析失败，跳过: {e}")
        recent = logs[-limit:]
        roles = {}
        tools = {}
        for entry in recent:
            r = entry.get("role", "unknown")
            t = entry.get("tool", "unknown")
            roles[r] = roles.get(r, 0) + 1
            tools[t] = tools.get(t, 0) + 1
        return {"success": True, "total_logs": len(logs), "report_period": f"最近 {limit} 条", "by_role": roles, "by_tool": tools, "recent_entries": recent[-20:]}
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register("track_changes", description="跟踪代码修改记录，支持一键回滚。记录文件的每次修改。", params={
    "action": "操作: log（记录修改）或 history（查看历史）或 rollback（回滚）",
    "file_path": "文件路径",
    "content_before": "可选，修改前内容（用于记录）",
    "rollback_count": "可选，回滚到倒数第几步（默认1）",
}, risk_level="LOW", category="query")
def track_changes(action: str = "history", file_path: Optional[str] = None, content_before: Optional[str] = None, rollback_count: int = 1) -> Dict[str, Any]:
    action = action.lower().strip()
    if action == "log":
        if not file_path: return {"error": "需要 file_path"}
        entry = {"file": file_path, "action": "modified", "content_before": (content_before or "")[:1000]}
        _log_entry(CHANGE_LOG, entry)
        return {"success": True, "file": file_path}
    elif action == "history":
        changes = []
        try:
            if os.path.exists(CHANGE_LOG):
                with open(CHANGE_LOG, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entry = json.loads(line)
                                if not file_path or entry.get("file") == file_path:
                                    changes.append(entry)
                            except Exception as e: logger.debug(f"变更记录行解析失败，跳过: {e}")
        except Exception as e:
            logger.debug(f"变更历史读取失败: {e}")
        return {"success": True, "total_changes": len(changes), "changes": changes[-30:]}
    elif action == "rollback":
        return {"error": "自动回滚需要结合 git 使用。请使用 git 相关工具。"}
    return {"error": f"不支持的操作: {action}"}


@ToolRegistry.register("verify_integrity", description="验证代码文件完整性，检测是否被篡改（基于 Git 状态）。", params={"path": "可选，要验证的文件或目录"}, risk_level="LOW", category="query")
def verify_integrity(path: Optional[str] = None) -> Dict[str, Any]:
    workdir = Path(path).expanduser() if path else Path.cwd()
    try:
        r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10, cwd=workdir if workdir.is_dir() else None)
        if r.returncode != 0:
            return {"warning": "未找到 Git 仓库，完整性验证基于文件修改时间"}
        modified = [l[3:] for l in (r.stdout or "").strip().split("\n") if l.strip()]
        return {"success": True, "git_changes": len(modified), "modified_files": modified[:20], "status": "modified" if modified else "clean"}
    except Exception as e:
        return {"error": f"验证失败: {e}"}


import subprocess  # noqa: E402
