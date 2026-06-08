"""
文件系统增强工具 — append_file, file_exists
"""
import os
from pathlib import Path
from typing import Dict, Any

from infra.tool_manager.tool_registry import ToolRegistry
from infra.security.centralized_policy import get_security_policy
from utils.logger import setup_logger

logger = setup_logger("file_extra")


def _is_path_allowed(path: str) -> bool:
    """统一路径安全检查 — 委托 SecurityPolicy"""
    return get_security_policy().is_path_allowed(path)


def _is_sensitive_path(path: str) -> bool:
    """敏感文件检查 — 委托 SecurityPolicy"""
    return get_security_policy().is_sensitive_file(path)


def _is_forbidden_write_path(path: str) -> bool:
    """禁止写入检查 — 委托 SecurityPolicy"""
    return get_security_policy().is_forbidden_write_path(path)


@ToolRegistry.register(
    "append_file",
    description="追加内容到文件末尾。不会覆盖已有内容。如果文件不存在则创建。",
    params={
        "path": "文件路径（绝对路径或相对于项目根目录）",
        "content": "要追加的内容",
    },
    risk_level="MEDIUM",
    category="admin",
)
def append_file(path: str, content: str) -> Dict[str, Any]:
    """追加内容到文件"""
    if not path:
        return {"error": "文件路径不能为空"}

    p = Path(path).expanduser()
    if not p.is_absolute():
        try:
            project_root = Path(__file__).resolve().parents[3]
            p = project_root / path
        except Exception as e:
            logger.warning(f"路径解析失败: {e}")
            return {"error": f"路径不是绝对路径且无法解析: {path}"}

    if _is_forbidden_write_path(str(p)):
        return {"error": f"禁止写入系统目录或 .git 目录: {path}"}

    if not _is_path_allowed(str(p)):
        return {"error": f"路径不在允许范围内: {path}"}

    if _is_sensitive_path(str(p)):
        return {"error": f"拒绝写入敏感文件: {path}"}

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return {
            "success": True,
            "path": str(p),
            "size": len(content),
            "mode": "append",
        }
    except Exception as e:
        return {"error": f"追加失败: {e}"}


@ToolRegistry.register(
    "file_exists",
    description="检查文件或目录是否存在。返回存在状态、类型（文件/目录）和基本信息。",
    params={
        "path": "要检查的路径",
    },
    risk_level="LOW",
    category="query",
)
def file_exists(path: str) -> Dict[str, Any]:
    """检查文件是否存在"""
    if not path:
        return {"error": "路径不能为空"}

    p = Path(path).expanduser()

    if not _is_path_allowed(str(p)):
        return {"error": f"路径不在允许范围内: {path}"}

    try:
        exists = p.exists()
        result = {
            "exists": exists,
            "path": str(p),
        }
        if exists:
            result["type"] = "directory" if p.is_dir() else "file"
            result["size"] = p.stat().st_size if p.is_file() else 0
            result["is_file"] = p.is_file()
            result["is_dir"] = p.is_dir()
        return result
    except Exception as e:
        return {"error": f"检查失败: {e}"}
