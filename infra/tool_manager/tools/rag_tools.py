"""
事件记忆查询工具 — 模型可主动调用的记忆检索

替代旧的 rag_query/rag_index（旧版 MemoryManager 已废弃），
通过 EventRetrieval 按遗忘曲线×强化×重要性排序返回相关事件。
"""
import asyncio
from typing import Any, Dict

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("memory_tools")


@ToolRegistry.register(
    "event_query",
    description="查询历史记忆事件。按语义相似度×遗忘曲线×强化×重要性排序返回最相关的事件。"
                "每个事件包含事实(fact)、经验教训(lesson)和重要性评分。",
    params={
        "query": "搜索查询，描述你想查找的记忆内容",
        "top_k": "可选，返回结果数量（默认3，最多10）",
    },
    risk_level="LOW",
    category="query",
)
def event_query(query: str, top_k: int = 3) -> Dict[str, Any]:
    """查询记忆事件 — 同步包装异步检索"""
    if not query:
        return {"error": "查询不能为空"}
    top_k = max(1, min(top_k, 10))

    try:
        # 同步桥接异步检索
        loop = _get_event_loop()
        result = loop.run_until_complete(_do_query(query, top_k))
        return result
    except Exception as e:
        logger.warning(f"[event_query] 检索失败: {e}")
        return {"error": str(e)}


async def _do_query(query: str, top_k: int) -> Dict[str, Any]:
    from modules.memory.event_retrieval import get_event_retrieval

    retrieval = get_event_retrieval()
    events = await retrieval.retrieve(query=query, top_k=top_k)

    if not events:
        return {
            "success": True,
            "query": query,
            "count": 0,
            "results": [],
            "note": "未找到相关记忆事件",
        }

    results = []
    for ev in events:
        results.append({
            "fact": ev.fact,
            "lesson": ev.lesson if ev.lesson else "",
            "type": ev.type,
            "importance": ev.importance,
        })

    return {
        "success": True,
        "query": query,
        "count": len(results),
        "results": results,
    }


def _get_event_loop():
    """获取当前事件循环或创建新循环"""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


# ── 旧工具兼容存根，引导模型使用新工具 ──────────────────────

@ToolRegistry.register(
    "rag_query",
    description="[已废弃] 请使用 event_query 代替。",
    params={"query": "搜索查询", "limit": "可选，返回结果数量"},
    risk_level="LOW",
    category="query",
)
def rag_query(query: str, limit: int = 5) -> Dict[str, Any]:
    """旧版记忆查询已废弃"""
    return {
        "error": "rag_query 已废弃，请使用 event_query 工具查询历史记忆",
        "migration": "使用 event_query(query, top_k) 替代",
    }


@ToolRegistry.register(
    "rag_index",
    description="[已废弃] 事件记忆由 EventReducer 自动管理，无需手动索引。",
    params={"path": "路径", "recursive": "是否递归"},
    risk_level="LOW",
    category="query",
)
def rag_index(path: str, recursive: bool = True) -> Dict[str, Any]:
    """旧版索引已废弃"""
    return {
        "error": "rag_index 已废弃，事件记忆由 EventReducer 在会话结束后自动提炼",
    }


@ToolRegistry.register(
    "rag_update",
    description="[已废弃] 事件记忆由 EventReducer 自动管理，无需手动更新。",
    params={"path": "路径"},
    risk_level="LOW",
    category="query",
)
def rag_update(path: str) -> Dict[str, Any]:
    """旧版更新已废弃"""
    return rag_index(path)
