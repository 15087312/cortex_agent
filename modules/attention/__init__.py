"""
注意力系统 — 模块门面

合并 V1 和 V2 后只导出核心分析器 + 数据容器。
外部模块统一从这里导入：
    from modules.attention import AttentionAnalyzer, create_attention_analyzer, get_recall_max_results
"""
from modules.attention.analyzer import (
    AttentionAnalyzer,
    AttentionResult,
    AttentionVector,
    create_attention_analyzer,
    get_recall_max_results,
)

__all__ = [
    "AttentionAnalyzer",
    "AttentionResult",
    "AttentionVector",
    "create_attention_analyzer",
    "get_recall_max_results",
]
