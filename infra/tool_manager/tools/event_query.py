"""事件记忆查询工具 — 大模型主动检索历史事件"""
import json
from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("event_query")


@ToolRegistry.register(
    name="event_query",
    description="查询历史记忆事件。根据问题检索相关的历史对话、经验教训、技术决策等（已按遗忘曲线+重要性自动排序）",
    params={
        "query": "查询文本（你想找什么）",
        "top_k": "返回条数，默认10（最多20）",
        "min_importance": "最低重要性过滤 0.0~1.0，默认0.0（只返回重要事件时设为0.3）",
        "types": "可选，逗号分隔的类型过滤: fact,thought,strategy,emotion",
        "start_time": "可选，只返回该时间之后的事件，格式 YYYY-MM-DD 或完整 ISO（如 2026-07-01 或 2026-07-01T10:00:00）",
        "end_time": "可选，只返回该时间之前的事件，同上（纯日期含当天）",
    },
    source="builtin",
    core=True,
)
async def event_query(query: str, top_k: str = "10", min_importance: str = "0.0", types: str = "",
                      start_time: str = "", end_time: str = "") -> str:
    """主动查询事件记忆系统

    大模型可以在需要时主动调用此工具查找历史记忆。
    常规思考时不需要调用——每轮对话已经自动注入相关事件。
    仅在需要深入查询特定历史信息时使用。

    Returns:
        JSON: {"events": [...], "count": N}
    """
    try:
        k = max(1, min(20, int(top_k)))
        imp = max(0.0, min(1.0, float(min_importance)))
        type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None

        from modules.memory.event_retrieval import get_event_retrieval
        retrieval = get_event_retrieval()
        events = await retrieval.retrieve(
            query=query,
            max_results=k,
            min_importance=imp,
            types=type_list,
            start_time=start_time.strip(),
            end_time=end_time.strip(),
        )

        formatted = []
        for ev in events:
            entry = {
                "type": ev.type,
                "importance": ev.importance,
                "time": ev.time[:16] if ev.time else "",
                "fact": ev.fact,
                "lesson": ev.lesson or "",
                "keywords": ev.keywords,
            }
            formatted.append(entry)

        return json.dumps({
            "events": formatted,
            "count": len(formatted),
            "query": query,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[event_query] 失败: {e}")
        return json.dumps({"error": str(e), "events": [], "count": 0}, ensure_ascii=False)
