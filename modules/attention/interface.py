"""
注意力接口 — 模块门面

只导出一个类 (AttentionAnalyzer) 和两个数据容器 (AttentionResult, AttentionVector)。
外部模块统一从 modules.attention 导入，无需关心内部实现。
"""
from modules.attention.analyzer import (
    AttentionAnalyzer,
    AttentionResult,
    AttentionVector,
    create_attention_analyzer,
)
