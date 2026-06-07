"""
工具执行后关联记忆检索器 — 自动查找与工具调用结果相关的历史记忆

参照 Claude 的工具后关联检索设计:
- 用 tool_name + 关键参数 + 结果摘要 构建查询
- 只保留与当前工具相关的记忆，避免无关记忆污染上下文
- 所有操作 fire-and-forget、失败不阻塞主流程
"""
from typing import Dict, Any, List, Optional
from utils.logger import setup_logger

logger = setup_logger("post_tool_retriever")


class PostToolMemoryRetriever:
    """工具执行后自动检索相关记忆"""

    def __init__(self, context_manager=None, memory_manager=None):
        self.memory_manager = memory_manager
        self.context_manager = context_manager

    async def retrieve_for_tool(
        self,
        tool_name: str,
        tool_result: str,
        tool_args: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """检索与工具执行相关的历史记忆

        Args:
            tool_name: 工具名称
            tool_result: 工具执行结果摘要
            tool_args: 工具调用参数

        Returns:
            {"tool_name": str, "related_memories": [...], "count": int}
        """
        if not self.context_manager:
            return {"tool_name": tool_name, "related_memories": [], "count": 0}

        try:
            # 构建查询: tool_name + 关键参数 + 结果片段
            query_parts = [f"tool:{tool_name}"]
            if tool_args:
                args_str = str(tool_args)[:200]
                query_parts.append(args_str)
            if tool_result:
                query_parts.append(tool_result[:200])
            query = " ".join(query_parts)

            working_context = await self.context_manager.build_working_context(
                current_goal=query,
                current_state={},
                attention_level=0.5,
            )
            related = self._filter_working_context(working_context, tool_name)

            count = len(related)
            if count > 0:
                logger.debug(
                    f"[T3] 工具 {tool_name} 检索到 {count} 条相关记忆"
                )

            return {
                "tool_name": tool_name,
                "related_memories": related,
                "count": count,
            }
        except Exception as e:
            logger.debug(f"[T3] 工具记忆检索失败 (非致命): {e}")
            return {"tool_name": tool_name, "related_memories": [], "count": 0}

    def _filter_working_context(self, working_context, tool_name: str) -> List[Dict[str, Any]]:
        """从 WorkingContext 过滤工具相关记忆"""
        related = []
        for item in getattr(working_context, "selected_memories", [])[:8]:
            content = str(item.get("content", ""))
            if tool_name.lower() in content.lower() or self._is_related(content.lower(), tool_name):
                related.append({
                    "content": content[:150],
                    "score": item.get("attention_score", 0),
                    "tier": item.get("source", "context"),
                })
        return related


    def _is_related(self, content: str, tool_name: str) -> bool:
        """检查内容是否与工具类型相关 (启发式规则)"""
        tool_keywords = {
            "calc": ["计算", "calc", "数学", "运算", "算术"],
            "calc_advanced": ["计算", "calc", "函数", "sqrt", "log"],
            "memory_match": ["记忆", "memory", "回忆", "记住", "历史"],
            "memory_score": ["记忆", "memory", "评分", "score"],
        }
        keywords = tool_keywords.get(tool_name, [tool_name])
        return any(kw in content for kw in keywords)


def format_tool_memories(tool_name: str, memories: List[Dict[str, Any]]) -> str:
    """将工具相关记忆格式化为可注入 prompt 的文本"""
    if not memories:
        return ""

    lines = [f"[{tool_name}] 相关历史记忆:"]
    for i, m in enumerate(memories[:5], 1):
        content = m.get("content", "")[:120]
        score = m.get("score", 0)
        tier = m.get("tier", "")
        tier_label = {"medium": "中期", "long": "长期"}.get(tier, tier)
        lines.append(f"  {i}. [{tier_label}] {content} (相关度: {score:.2f})")
    return "\n".join(lines)
