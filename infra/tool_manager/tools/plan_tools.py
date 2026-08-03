"""
plan 工具集 — 设计方案管理

存储在 ~/.cortex/plans/ 目录下，支持创建、查看、更新方案。
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, Any

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("plan_tools")

PLANS_DIR = Path.home() / ".cortex" / "plans"


def _ensure_dir():
    PLANS_DIR.mkdir(parents=True, exist_ok=True)


def _plan_path(plan_id: str) -> Path:
    return PLANS_DIR / f"{plan_id}.json"


@ToolRegistry.register(
    "plan",
    description=(
        "设计方案管理工具。用于创建、查看、更新设计方案。"
        "action='list' 列出所有方案，action='create' 创建新方案，"
        "action='get' 查看方案详情，action='update' 更新方案内容，action='delete' 删除方案。"
    ),
    params={
        "action": "操作类型: list / create / get / update / delete",
        "plan_id": "方案 ID（get/update/delete 时必填）",
        "title": "方案标题（create 时必填）",
        "content": "方案内容（Markdown 格式，create/update 时使用）",
        "status": "方案状态: draft / active / completed / archived（update 时使用）",
    },
    risk_level="LOW",
    category="admin",
)
def plan(action: str, plan_id: str = "", title: str = "", content: str = "",
         status: str = "draft", **kwargs) -> Dict[str, Any]:
    """设计方案管理"""
    _ensure_dir()

    if action == "list":
        plans = []
        for f in sorted(PLANS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    plans.append({
                        "id": data.get("id", f.stem),
                        "title": data.get("title", ""),
                        "status": data.get("status", "draft"),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                    })
            except Exception:
                pass
        return {"success": True, "plans": plans}

    elif action == "create":
        if not title:
            return {"success": False, "error": "create 需要 title 参数"}
        import uuid
        plan_id = uuid.uuid4().hex[:12]
        data = {
            "id": plan_id,
            "title": title,
            "content": content,
            "status": status,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(_plan_path(plan_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"success": True, "plan_id": plan_id, "title": title}

    elif action == "get":
        if not plan_id:
            return {"success": False, "error": "get 需要 plan_id 参数"}
        p = _plan_path(plan_id)
        if not p.exists():
            return {"success": False, "error": f"方案 {plan_id} 不存在"}
        with open(p, "r", encoding="utf-8") as f:
            return {"success": True, **json.load(f)}

    elif action == "update":
        if not plan_id:
            return {"success": False, "error": "update 需要 plan_id 参数"}
        p = _plan_path(plan_id)
        if not p.exists():
            return {"success": False, "error": f"方案 {plan_id} 不存在"}
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if title:
            data["title"] = title
        if content:
            data["content"] = content
        if status:
            data["status"] = status
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"success": True, "plan_id": plan_id}

    elif action == "delete":
        if not plan_id:
            return {"success": False, "error": "delete 需要 plan_id 参数"}
        p = _plan_path(plan_id)
        if not p.exists():
            return {"success": False, "error": f"方案 {plan_id} 不存在"}
        p.unlink()
        return {"success": True, "deleted": plan_id}

    return {"success": False, "error": f"未知操作: {action}"}
