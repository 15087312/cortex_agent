"""
上下文预算管理器 - 动态分配 tokens 给工具描述、对话历史、记忆检索

核心概念:
- 上下文总预算: 通常 8K tokens (per turn)
- 子预算分配:
  * system_prompt: ~5% (400 tokens, 身份 + 约束)
  * tool_descriptions: 20-40% (1600-3200 tokens)
  * conversation_history: 40-60% (3200-4800 tokens)
  * memory_retrieval: 5-10% (400-800 tokens)

动态调整策略:
- 工具多的角色: tool_descriptions 用 40%, 对话用 50%
- 工具少的角色: tool_descriptions 用 20%, 对话用 70%
- 历史记录长: 自动减少工具描述详细程度
"""

import threading
from dataclasses import dataclass
from typing import Dict, Any, List, Optional


@dataclass
class ContextBudget:
    """上下文预算配置"""
    total_tokens: int = 8000  # 单轮上下文总预算
    system_prompt_percent: int = 5  # 系统提示占比 %
    tool_descriptions_min_percent: int = 15  # 工具描述最小占比 %
    tool_descriptions_max_percent: int = 40  # 工具描述最大占比 %
    conversation_history_percent: int = 50  # 对话历史占比 %
    memory_retrieval_percent: int = 10  # 记忆检索占比 %

    def allocate(self, actual_tool_count: int = None) -> Dict[str, int]:
        """根据实际工具数量动态分配预算

        Args:
            actual_tool_count: 角色实际可用工具数

        Returns:
            {
                'system_prompt': 400,
                'tool_descriptions': 2400,
                'conversation_history': 3600,
                'memory_retrieval': 800,
            }
        """
        # 基础分配
        result = {
            "system_prompt": int(self.total_tokens * self.system_prompt_percent / 100),
            "conversation_history": int(self.total_tokens * self.conversation_history_percent / 100),
            "memory_retrieval": int(self.total_tokens * self.memory_retrieval_percent / 100),
        }

        # 工具描述: 根据工具数量动态调整
        remaining = self.total_tokens - sum(result.values())
        if actual_tool_count is not None and actual_tool_count < 5:
            # 工具少: 减少工具描述空间，增加对话空间
            tool_desc_percent = self.tool_descriptions_min_percent
        elif actual_tool_count is not None and actual_tool_count > 15:
            # 工具多: 增加工具描述空间，减少对话空间
            tool_desc_percent = self.tool_descriptions_max_percent
        else:
            # 工具中等: 平衡分配
            tool_desc_percent = (
                self.tool_descriptions_min_percent +
                self.tool_descriptions_max_percent
            ) // 2

        result["tool_descriptions"] = int(self.total_tokens * tool_desc_percent / 100)
        return result


class ContextBudgetManager:
    """上下文预算管理器 - 监控和优化上下文使用"""

    def __init__(self, budget_config: Optional[ContextBudget] = None):
        self.budget = budget_config or ContextBudget()
        self._token_cache = {}  # {key: estimated_tokens}

    def estimate_tokens(self, text: str) -> int:
        """粗略估计文本的 token 数（中文 ~1.3 字符/token，英文 ~4 字符/token）"""
        if not text:
            return 0
        # 简单启发式: 中文 3 字符 = 1 token, 英文 4 字符 = 1 token
        chinese_chars = sum(1 for c in text if ord(c) >= 0x4E00 and ord(c) <= 0x9FFF)
        english_chars = len(text) - chinese_chars
        return (chinese_chars + english_chars // 4) // 3

    def estimate_tool_descriptions_tokens(self, tool_count: int) -> int:
        """估计工具描述占用的 token 数

        经验值:
        - 0-3 个工具: ~200 tokens
        - 4-10 个工具: ~800 tokens
        - 11-20 个工具: ~2000 tokens
        - 20+ 个工具: ~4000 tokens
        """
        if tool_count <= 3:
            return 200
        elif tool_count <= 10:
            return 800
        elif tool_count <= 20:
            return 2000
        else:
            return 4000

    def should_simplify_tool_descriptions(
        self,
        tool_count: int,
        budget_allocation: Dict[str, int],
    ) -> bool:
        """判断是否应该简化工具描述以节省空间

        当对话历史已占用大量 tokens 时，应简化工具描述
        """
        tool_desc_budget = budget_allocation.get("tool_descriptions", 1600)
        estimated_tool_tokens = self.estimate_tool_descriptions_tokens(tool_count)

        # 如果工具描述超出预算 20% 以上，建议简化
        return estimated_tool_tokens > tool_desc_budget * 1.2

    def recommend_memory_search_count(self, budget_allocation: Dict[str, int]) -> int:
        """根据预算推荐记忆搜索结果数量

        每个记忆结果约 200-300 tokens，根据预算推荐数量
        """
        memory_budget = budget_allocation.get("memory_retrieval", 400)
        avg_memory_tokens = 250
        return max(1, memory_budget // avg_memory_tokens)

    def recommend_conversation_turns(
        self,
        avg_turn_tokens: int,
        budget_allocation: Dict[str, int],
    ) -> int:
        """根据预算推荐保留的对话轮次

        Args:
            avg_turn_tokens: 平均每轮对话的 token 数
            budget_allocation: 预算分配结果

        Returns:
            推荐保留的轮次数
        """
        conv_budget = budget_allocation.get("conversation_history", 3200)
        if avg_turn_tokens <= 0:
            return 5  # 默认保留 5 轮
        return max(1, conv_budget // avg_turn_tokens)

    def create_budget_for_role(self, role: str, tool_count: int) -> Dict[str, int]:
        """为特定角色创建预算分配

        Args:
            role: 模型角色 (customer / code_writer / large / etc)
            tool_count: 该角色可用工具数

        Returns:
            预算分配字典
        """
        config = ContextBudget()

        # 根据角色调整预算
        if role in ("customer", "emotion"):
            # 工具少的角色: 减少工具描述预算
            config.tool_descriptions_min_percent = 10
            config.tool_descriptions_max_percent = 20
            config.conversation_history_percent = 70
        elif role in ("large", "supervisor"):
            # 工具多的角色: 增加工具描述预算
            config.tool_descriptions_max_percent = 50
            config.conversation_history_percent = 40
        else:
            # 专家: 平衡分配
            config.tool_descriptions_min_percent = 20
            config.tool_descriptions_max_percent = 35

        return config.allocate(tool_count)


# 全局实例
_budget_manager = None
_budget_manager_lock = threading.Lock()


def get_context_budget_manager() -> ContextBudgetManager:
    """获取全局上下文预算管理器实例"""
    global _budget_manager
    if _budget_manager is None:
        with _budget_manager_lock:
            if _budget_manager is None:
                _budget_manager = ContextBudgetManager()
    return _budget_manager
