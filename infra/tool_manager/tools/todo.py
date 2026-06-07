"""
任务管理工具 — 创建、查看、更新和完成任务列表

对应 Claude Code 的 TodoWriteTool / TaskCreateTool / TaskListTool / TaskUpdateTool。
基于 JSON 文件持久化任务。
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("todo")

TODOS_FILE = str(Path.home() / ".hermes" / "todos.json")


def _load_todos() -> list:
    """加载任务列表"""
    try:
        if os.path.exists(TODOS_FILE):
            with open(TODOS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"任务文件加载失败，返回空列表: {e}")
    return []


def _save_todos(todos: list):
    """保存任务列表"""
    os.makedirs(os.path.dirname(TODOS_FILE), exist_ok=True)
    with open(TODOS_FILE, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)


@ToolRegistry.register(
    "todo",
    description=(
        "任务管理工具。用于创建、查看、更新和标记完成任务。"
        "支持待办事项的生命周期管理。"
        "用法：action='list' 查看所有任务，action='create' 创建新任务，"
        "action='update' 更新任务状态/内容，action='delete' 删除任务。"
    ),
    params={
        "action": "操作类型: list（列出）、create（创建）、update（更新状态）、delete（删除）",
        "items": (
            "任务列表，JSON 字符串格式。每个任务包含 id（可选，自动生成）、"
            "content（任务描述）、status（pending/in_progress/completed）。"
            "创建时传 [{'content': '...'}]；更新时传 [{'id': '...', 'status': 'completed'}]"
        ),
    },
    risk_level="LOW",
    category="query",
    core=True,
)
def todo(action: str = "list", items: Optional[str] = None) -> Dict[str, Any]:
    """任务管理"""
    action = action.lower().strip()

    if action == "list":
        todos = _load_todos()
        return {
            "action": "list",
            "total": len(todos),
            "items": todos,
        }

    elif action == "create":
        if not items:
            return {"error": "创建任务需要提供 items 参数"}
        try:
            parsed = json.loads(items) if isinstance(items, str) else items
        except json.JSONDecodeError:
            return {"error": "items 必须是有效的 JSON 字符串"}

        todos = _load_todos()
        created = []
        for item in parsed:
            task = {
                "id": f"task_{int(time.time())}_{len(todos) + len(created)}",
                "content": item.get("content", ""),
                "status": item.get("status", "pending"),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            todos.append(task)
            created.append(task)
        _save_todos(todos)

        return {
            "action": "create",
            "created": len(created),
            "items": created,
        }

    elif action == "update":
        if not items:
            return {"error": "更新任务需要提供 items 参数"}
        try:
            parsed = json.loads(items) if isinstance(items, str) else items
        except json.JSONDecodeError:
            return {"error": "items 必须是有效的 JSON 字符串"}

        todos = _load_todos()
        updated = []
        not_found = []
        for update in parsed:
            task_id = update.get("id", "")
            found = False
            for t in todos:
                if t["id"] == task_id:
                    if "content" in update:
                        t["content"] = update["content"]
                    if "status" in update:
                        t["status"] = update["status"]
                    t["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    updated.append(t)
                    found = True
                    break
            if not found:
                not_found.append(task_id)

        _save_todos(todos)
        result = {"action": "update", "updated": len(updated)}
        if updated:
            result["items"] = updated
        if not_found:
            result["not_found"] = not_found
        return result

    elif action == "delete":
        if not items:
            return {"error": "删除任务需要提供 items 参数"}
        try:
            parsed = json.loads(items) if isinstance(items, str) else items
        except json.JSONDecodeError:
            return {"error": "items 必须是有效的 JSON 字符串"}

        ids_to_delete = set()
        for item in parsed:
            tid = item.get("id", "")
            if tid:
                ids_to_delete.add(tid)

        todos = _load_todos()
        remaining = [t for t in todos if t["id"] not in ids_to_delete]
        deleted = len(todos) - len(remaining)
        _save_todos(remaining)

        return {"action": "delete", "deleted": deleted}

    else:
        return {"error": f"不支持的操作: {action}，支持: list/create/update/delete"}
