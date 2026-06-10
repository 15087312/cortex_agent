"""
文件管理工具 - 安全管控示例

所有操作经过：
1. SecurityPolicy 集中安全检查（白名单 + 敏感文件 + 禁止目录）
2. 输出模块统一转发
"""
import os
import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from infra.tool_manager.tool_registry import ToolRegistry
from infra.security.centralized_policy import get_security_policy
from utils.logger import setup_logger

logger = setup_logger("file_manager")


def _is_path_allowed(target_path: Path) -> bool:
    """统一路径安全检查 — 委托 SecurityPolicy（白名单 + 禁止目录 + 敏感文件）"""
    policy = get_security_policy()
    path_str = str(target_path)
    if not policy.is_path_allowed(path_str):
        return False
    if policy.is_forbidden_write_path(path_str):
        return False
    if policy.is_sensitive_file(path_str):
        return False
    return True


def _check_symlink_safe(path: str, target_path: Path) -> Optional[str]:
    """检测符号链接 TOCTOU — 原始路径和解析后路径都必须通过安全检查"""
    original = Path(path).expanduser()
    # 检查原始路径（解析前）是否在允许范围内
    if original.exists() and original.is_symlink():
        if not _is_path_allowed(original):
            return f"符号链接目标不在允许范围内: {path} → {target_path}"
    return None


@ToolRegistry.register(
    name="list_files",
    description="列出目录下的文件和子目录",
    params={"path": "目录路径", "recursive": "是否递归（可选）"}
)
def list_files(path: str, recursive: bool = False) -> Dict[str, Any]:
    """
    列出目录内容

    Args:
        path: 目录路径
        recursive: 是否递归列出子目录

    Returns:
        文件列表信息
    """
    try:
        target_path = Path(path).expanduser().resolve()

        # 符号链接 TOCTOU 检测
        sym_check = _check_symlink_safe(path, target_path)
        if sym_check:
            return {"error": sym_check}

        # SEC-9: Whitelist-based check
        if not _is_path_allowed(target_path):
            return {"error": "禁止访问该目录路径"}

        if not target_path.exists():
            return {"error": f"路径不存在: {path}"}

        if not target_path.is_dir():
            return {"error": f"路径不是目录: {path}"}

        files = []
        if recursive:
            for item in target_path.rglob("*"):
                files.append({
                    "name": item.name,
                    "path": str(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0
                })
        else:
            for item in target_path.iterdir():
                files.append({
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0
                })
        
        return {
            "path": str(target_path),
            "total": len(files),
            "files": files
        }
    
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    name="read_file",
    description="读取文件内容",
    params={"path": "文件路径", "encoding": "编码格式（默认utf-8）"},
    core=True,
)
def read_file(path: str, encoding: str = "utf-8") -> Dict[str, Any]:
    """
    读取文件内容

    Args:
        path: 文件路径
        encoding: 文件编码

    Returns:
        文件内容
    """
    try:
        target_path = Path(path).expanduser().resolve()

        # 符号链接 TOCTOU 检测
        sym_check = _check_symlink_safe(path, target_path)
        if sym_check:
            return {"error": sym_check}

        # SEC-9: Whitelist-based check (must be in allowed directories)
        if not _is_path_allowed(target_path):
            return {"error": "禁止访问该文件路径"}

        if not target_path.exists():
            return {"error": f"文件不存在: {path}"}

        if not target_path.is_file():
            return {"error": f"路径不是文件: {path}"}

        with open(target_path, 'r', encoding=encoding) as f:
            content = f.read()

        return {
            "path": str(target_path),
            "size": len(content),
            "content": content[:10000],  # 限制返回长度
            "truncated": len(content) > 10000
        }

    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    name="write_file",
    description="写入文件内容",
    params={"path": "文件路径", "content": "文件内容", "mode": "写入模式（w/a）"},
    risk_level="MEDIUM",
    category="mutation",
    core=True,
)
def write_file(path: str, content: str, mode: str = "w") -> Dict[str, Any]:
    """
    写入文件内容

    Args:
        path: 文件路径
        content: 文件内容
        mode: 写入模式（w=覆盖，a=追加）

    Returns:
        写入结果
    """
    try:
        target_path = Path(path).expanduser().resolve()

        # 符号链接 TOCTOU 检测
        sym_check = _check_symlink_safe(path, target_path)
        if sym_check:
            return {"error": sym_check}

        # SEC-9: Whitelist-based check (must be in allowed directories)
        if not _is_path_allowed(target_path):
            return {"error": "禁止写入该文件路径"}

        # 确保父目录存在
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with open(target_path, mode, encoding='utf-8') as f:
            f.write(content)

        return {
            "success": True,
            "path": str(target_path),
            "size": len(content),
            "mode": mode
        }

    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    name="delete_file",
    description="删除文件",
    params={"path": "文件路径"},
    risk_level="HIGH",
    category="mutation",
)
def delete_file(path: str) -> Dict[str, Any]:
    """
    删除文件

    Args:
        path: 文件路径

    Returns:
        删除结果
    """
    try:
        target_path = Path(path).expanduser().resolve()

        # 符号链接 TOCTOU 检测
        sym_check = _check_symlink_safe(path, target_path)
        if sym_check:
            return {"error": sym_check}

        # SEC-9: Whitelist-based check (must be in allowed directories)
        if not _is_path_allowed(target_path):
            return {"error": "禁止删除该文件路径"}

        if not target_path.exists():
            return {"error": f"文件不存在: {path}"}

        if not target_path.is_file():
            return {"error": f"路径不是文件: {path}"}

        target_path.unlink()

        return {
            "success": True,
            "path": str(target_path),
            "message": "文件已删除"
        }

    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    name="get_file_info",
    description="获取文件详细信息",
    params={"path": "文件路径"}
)
def get_file_info(path: str) -> Dict[str, Any]:
    """
    获取文件元数据

    Args:
        path: 文件路径

    Returns:
        文件信息
    """
    try:
        target_path = Path(path).expanduser().resolve()

        # 符号链接 TOCTOU 检测
        sym_check = _check_symlink_safe(path, target_path)
        if sym_check:
            return {"error": sym_check}

        if not _is_path_allowed(target_path):
            return {"error": "禁止访问该文件路径"}

        if not target_path.exists():
            return {"error": f"路径不存在: {path}"}
        
        stat = target_path.stat()
        
        return {
            "path": str(target_path),
            "name": target_path.name,
            "type": "directory" if target_path.is_dir() else "file",
            "size": stat.st_size,
            "created": stat.st_ctime,
            "modified": stat.st_mtime,
            "permissions": oct(stat.st_mode)[-3:]
        }
    
    except Exception as e:
        return {"error": str(e)}


@ToolRegistry.register(
    name="search_files",
    description=(
        "搜索文件。支持两种模式：\n"
        "- target='files'（默认）：按文件名模式匹配（支持通配符 *）\n"
        "- target='content'：在文件内容中搜索文本\n"
        "示例：search_files(path='/project', pattern='*.py') 找所有Python文件\n"
        "示例：search_files(path='/project', pattern='write_thought', target='content') 在文件内容中搜索"
    ),
    params={
        "path": "搜索根目录",
        "pattern": "文件名模式（target=files时支持通配符）或要搜索的文本（target=content时）",
        "target": "搜索目标：'files'=按文件名（默认），'content'=搜索文件内容",
        "file_type": "文件类型过滤（file/directory），仅 target=files 时有效",
        "file_glob": "文件类型过滤（如 '*.py'），仅 target=content 时有效",
    },
    core=True,
)
def search_files(
    path: str,
    pattern: str,
    target: str = "files",
    file_type: str = None,
    file_glob: str = None,
) -> Dict[str, Any]:
    """
    搜索匹配的文件或文件内容

    Args:
        path: 搜索根目录
        pattern: 文件名模式（target=files时支持通配符）或要搜索的文本
        target: "files"=按文件名搜索，"content"=搜索文件内容
        file_type: target=files时有效，file/directory
        file_glob: target=content时有效，如 "*.py" 只搜索Python文件

    Returns:
        匹配结果列表
    """
    try:
        import fnmatch

        target_path = Path(path).expanduser().resolve()

        # 符号链接 TOCTOU 检测
        sym_check = _check_symlink_safe(path, target_path)
        if sym_check:
            return {"error": sym_check}

        # SEC-9: Whitelist-based check
        if not _is_path_allowed(target_path):
            return {"error": "禁止搜索该目录路径"}

        if not target_path.exists():
            return {"error": f"路径不存在: {path}"}

        # ── 内容搜索模式 ──
        if target == "content":
            results = []
            file_glob_pattern = file_glob or "*"
            for item in target_path.rglob(file_glob_pattern):
                if not item.is_file():
                    continue
                try:
                    content = item.read_text(encoding="utf-8", errors="ignore")
                    if pattern in content:
                        # 找匹配的行
                        matched_lines = []
                        for i, line in enumerate(content.split("\n"), 1):
                            if pattern in line:
                                matched_lines.append(f"  L{i}: {line.strip()[:200]}")
                                if len(matched_lines) >= 5:  # 最多显示5行
                                    matched_lines.append("  ... (更多匹配行)")
                                    break
                        results.append({
                            "name": item.name,
                            "path": str(item),
                            "type": "file",
                            "size": item.stat().st_size,
                            "matches": len(matched_lines),
                            "lines": matched_lines,
                        })
                except Exception as e:
                    logger.warning(f"读取文件失败，已跳过: {item}: {e}")
                    continue

            return {
                "path": str(target_path),
                "pattern": pattern,
                "target": "content",
                "total": len(results),
                "results": results[:50],  # 限制返回数量
            }

        # ── 文件名搜索模式（原有逻辑）──
        results = []
        for item in target_path.rglob("*"):
            # 类型过滤
            if file_type == "file" and not item.is_file():
                continue
            if file_type == "directory" and not item.is_dir():
                continue

            # 名称匹配
            if fnmatch.fnmatch(item.name, pattern):
                results.append({
                    "name": item.name,
                    "path": str(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0,
                })

        return {
            "path": str(target_path),
            "pattern": pattern,
            "target": "files",
            "total": len(results),
            "results": results[:100],  # 限制返回数量
        }
    
    except Exception as e:
        return {"error": str(e)}
