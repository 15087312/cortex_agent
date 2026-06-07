"""
工具搜索 — 搜索已注册的工具
"""
from typing import Dict, Any

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("tools_search")


@ToolRegistry.register(
    "tools_search",
    description=(
        "搜索系统中所有可用工具（包括内置工具和插件工具）。"
        "支持按关键词、类别、风险等级过滤。"
        "当需要查找某个功能的工具时使用，返回匹配的工具名称和描述。"
    ),
    params={
        "keyword": "可选，搜索关键词，匹配工具名称和描述",
        "category": "可选，按类别过滤：query / mutation / admin",
        "risk_level": "可选，按风险等级过滤：LOW / MEDIUM / HIGH",
        "source": "可选，按来源过滤：builtin / plugin / dynamic",
    },
    risk_level="LOW",
    category="query",
    tags=["system"],
    core=True,
)
def tools_search(
    keyword: str = "",
    category: str = "",
    risk_level: str = "",
    source: str = "",
) -> Dict[str, Any]:
    """搜索已注册的工具。"""
    all_tools = ToolRegistry.list_tools()
    results = []

    for name, info in all_tools.items():
        if keyword:
            kw = keyword.lower()
            if kw not in name.lower() and kw not in info.get("description", "").lower():
                continue
        if category and info.get("category", "") != category:
            continue
        if risk_level and info.get("risk_level", "") != risk_level:
            continue
        if source and info.get("source", "") != source:
            continue

        results.append({
            "name": name,
            "description": info.get("description", ""),
            "category": info.get("category", ""),
            "risk_level": info.get("risk_level", ""),
            "source": info.get("source", ""),
        })

    return {
        "success": True,
        "count": len(results),
        "tools": results,
    }
