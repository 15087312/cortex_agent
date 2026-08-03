"""
file_history 工具集 — 文件修改历史记录与回滚

参考 opencode 设计：每次文件修改前记录 initial，修改后记录新版本。
支持按版本回滚。
"""
import os
from typing import Dict, Any

from infra.tool_manager.tool_registry import ToolRegistry


def _get_session_id(**kwargs) -> str:
    """从工具参数中获取 session_id"""
    return kwargs.get("_session_id", "default")


@ToolRegistry.register(
    "record_file_change",
    description="记录文件修改。在编辑/写入文件前后调用：先 record_file_change(action='before') 记录原始内容，"
                "修改文件后调用 record_file_change(action='after') 记录新内容。",
    params={
        "action": "操作类型: before（记录修改前）/ after（记录修改后）",
        "file_path": "文件绝对路径",
        "content": "文件内容（after 时必填）",
    },
    risk_level="LOW",
    category="admin",
)
def record_file_change(action: str, file_path: str, content: str = "", **kwargs) -> Dict[str, Any]:
    """记录文件修改历史"""
    from modules.cortex.file_history import get_file_history

    session_id = _get_session_id(**kwargs)
    history = get_file_history()
    abs_path = os.path.abspath(file_path)

    if action == "before":
        # 读取当前文件内容并记录为 initial
        try:
            current_content = ""
            if os.path.exists(abs_path):
                with open(abs_path, "r", encoding="utf-8") as f:
                    current_content = f.read()
            vid = history.record_initial(session_id, abs_path, current_content)
            return {"success": True, "version_id": vid, "action": "recorded_initial",
                    "path": abs_path, "content_length": len(current_content)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "after":
        if not content:
            return {"success": False, "error": "after 操作需要提供 content 参数"}
        vid = history.record_version(session_id, abs_path, content)
        return {"success": True, "version_id": vid, "action": "recorded_version",
                "path": abs_path, "content_length": len(content)}

    return {"success": False, "error": f"未知操作: {action}，支持 before/after"}


@ToolRegistry.register(
    "rollback_file",
    description="将文件回滚到 AI 修改前的状态（initial 版本）",
    params={
        "file_path": "要回滚的文件路径",
    },
    risk_level="HIGH",
    category="admin",
    tags=["mutation"],
)
def rollback_file(file_path: str, **kwargs) -> Dict[str, Any]:
    """回滚单个文件"""
    from modules.cortex.file_history import get_file_history

    session_id = _get_session_id(**kwargs)
    history = get_file_history()
    abs_path = os.path.abspath(file_path)

    initial = history.get_initial(session_id, abs_path)
    if not initial:
        return {"success": False, "error": f"文件 {file_path} 没有初始版本记录"}

    content = initial["content"]
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "action": "restored", "path": abs_path,
                "content_length": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@ToolRegistry.register(
    "rollback_session_files",
    description="回滚当前会话中所有被 AI 修改过的文件到初始状态",
    params={},
    risk_level="HIGH",
    category="admin",
    tags=["mutation"],
)
def rollback_session_files(**kwargs) -> Dict[str, Any]:
    """回滚会话所有文件"""
    from modules.cortex.file_history import get_file_history

    session_id = _get_session_id(**kwargs)
    history = get_file_history()
    results = history.rollback_session(session_id)
    restored = sum(1 for v in results.values() if v == "restored")
    return {"success": True, "restored": restored, "total": len(results), "details": results}


@ToolRegistry.register(
    "list_file_versions",
    description="列出文件的历史版本",
    params={
        "file_path": "文件路径（可选，不填则列出会话所有文件）",
    },
    risk_level="LOW",
    category="query",
)
def list_file_versions(file_path: str = "", **kwargs) -> Dict[str, Any]:
    """列出文件版本"""
    from modules.cortex.file_history import get_file_history

    session_id = _get_session_id(**kwargs)
    history = get_file_history()

    if file_path:
        abs_path = os.path.abspath(file_path)
        versions = history.list_versions(session_id, abs_path)
        return {
            "success": True,
            "path": abs_path,
            "versions": [{"id": v["id"], "version": v["version"],
                          "content_length": len(v["content"]),
                          "created_at": v["created_at"]} for v in versions],
        }
    else:
        files = history.list_session_files(session_id)
        return {
            "success": True,
            "files": [{"path": f["path"], "version": f["version"],
                       "content_length": len(f["content"]),
                       "created_at": f["created_at"]} for f in files],
        }
