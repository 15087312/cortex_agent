"""黑板协作历史查询工具 — 大模型主动检索多模型协作过程（含内存溢出清理的旧观察）

黑板（CognitiveBlackboard）内存仅保留最近 200 条观察（优先新），
本工具查落库的 blackboard_observations 表，可追溯完整协作历史。
"""
import json
from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("blackboard_history")


@ToolRegistry.register(
    name="blackboard_history",
    description="查询黑板协作观察历史（多模型协作过程：委托引导、对话历史注入、专家发现、思考记录等）。"
                "黑板内存仅保留最近 200 条，本工具查落库的完整历史（含已溢出的旧观察），用于追溯旧协作内容",
    params={
        "query": "关键词，可选（按内容模糊匹配）",
        "session_id": "会话 ID，可选（只查指定会话；空则查全部）",
        "tier": "身份过滤，可选：large / supervisor / expert / user",
        "time_range": "时间范围，可选，格式 start~end（ISO，如 2026-07-01~2026-07-31），空边界不限",
        "limit": "返回条数，默认 10（最多 50）",
    },
    source="builtin",
    core=True,
)
async def blackboard_history(
    query: str = "",
    session_id: str = "",
    tier: str = "",
    time_range: str = "",
    limit: str = "10",
) -> str:
    """查询黑板协作观察历史。

    Returns:
        JSON: {"observations": [...], "count": N, "query": Q}
    """
    try:
        from modules.database.blackboard_repo import query_observations

        k = max(1, min(50, int(limit)))
        start = end = ""
        if "~" in time_range:
            parts = time_range.split("~", 1)
            start = (parts[0] or "").strip()
            end = (parts[1] or "").strip()

        rows = query_observations(
            session_id=session_id.strip(),
            query=query.strip(),
            start=start,
            end=end,
            limit=k,
            tier=tier.strip(),
        )
        if not rows:
            return json.dumps({"observations": [], "count": 0, "query": query}, ensure_ascii=False)

        formatted = [{
            "time": (r["created_at"] or "")[:16],
            "tier": r["tier"],
            "content": r["content"],
            "session_id": r["session_id"][:12],
        } for r in rows]
        return json.dumps(
            {"observations": formatted, "count": len(formatted), "query": query},
            ensure_ascii=False, indent=2,
        )
    except Exception as e:
        logger.warning(f"[blackboard_history] 失败: {e}")
        return json.dumps({"error": str(e), "observations": [], "count": 0}, ensure_ascii=False)
