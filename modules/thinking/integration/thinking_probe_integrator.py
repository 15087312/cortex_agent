"""
探针-思考集成层 (精简版)

提供探针信号 → 模型身份模板 的映射，以及输出格式化。
SessionContentAccumulator 和 create_probe_inter_round_callback 已废弃移除，
由 SessionMonitor 统一替代。
"""
from typing import List, Any


class _OutputWrapper:
    """输出包装器 —— 与 SessionMonitor._entries_to_outputs() 格式保持一致"""

    def __init__(self, content: str, sender: str = "large_model", marker: str = "thinking"):
        self.content = content
        self.sender = sender
        self.marker = marker


def build_probe_guidance_block(signals: List[Any]) -> str:
    """
    格式化探针信号为 prompt 注入文本。

    按优先级排序，仅包含高置信度 (>= 0.6) 的信号。
    """
    if not signals:
        return ""

    sorted_signals = sorted(signals, key=lambda s: (
        getattr(s, 'priority', None).value if hasattr(s, 'priority') and s.priority else 0
    ), reverse=True)

    lines = ["[探针实时引导 — 检测到以下信号，请融入本轮思考]", ""]
    added_types = set()

    for sig in sorted_signals:
        confidence = getattr(sig, 'confidence', 0)
        signal_type = getattr(sig, 'signal_type', 'unknown')
        content = getattr(sig, 'content', '')[:300]

        if confidence < 0.6:
            continue
        if signal_type in added_types:
            continue
        added_types.add(signal_type)

        priority_name = getattr(sig.priority, 'name', 'MEDIUM') if hasattr(sig, 'priority') else 'MEDIUM'
        lines.append(f"- [{priority_name}][{signal_type}] (置信度 {confidence:.0%}) {content}")

    if len(lines) == 2:
        return ""

    lines.append("")
    lines.append("请基于以上信号调整你的分析方向和回答策略。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 探针信号 → 专家系统 映射
# ---------------------------------------------------------------------------

def _map_signal_to_expert(signal_type: str) -> str:
    """将探针信号类型映射到专家系统名称"""
    mapping = {
        "safety": "security",
        "values": "values",
        "values_violation": "values",
        "values_probe": "values",
        "security": "security",
        "security_risk": "security",
        "security_probe": "security",
        "memory": "memory",
        "memory_needed": "memory",
        "memory_probe": "memory",
        "tool": "tool",
        "tool_needed": "tool",
        "tool_probe": "tool",
        "code": "code",
        "deep_analysis": "analysis",
        "search": "search",
    }
    return mapping.get(signal_type, "")


# ---------------------------------------------------------------------------
# 探针信号 → 模型身份模板 映射
# ---------------------------------------------------------------------------

def _map_signal_to_identity(signal_type: str, content: str = "") -> str:
    """将探针信号类型映射到模型身份模板键

    用于 probe_start 工具调用，决定激活哪个模型角色。
    """
    content_lower = (content or "").lower()

    mapping = {
        "user_input": "large",
        "user_input_detected": "large",
        # 安全 → 安全监察专家
        "safety": "expert_security_monitor",
        "values": "expert_security_monitor",
        "values_violation": "expert_security_monitor",
        # 代码 → 代码主管
        "code": "supervisor_code",
        "code_needed": "supervisor_code",
        "code_probe": "supervisor_code",
        "code_review_needed": "expert_reviewer",
        "code_implement_needed": "expert_implementer",
        "code_test_needed": "expert_tester",
        # 搜索 → 查询主管
        "search": "supervisor_query",
        "search_needed": "supervisor_query",
        "search_probe": "supervisor_query",
        # 深度分析 → 分析专家
        "deep_analysis": "expert_analyzer",
        "analysis_needed": "expert_analyzer",
        "analysis_probe": "expert_analyzer",
        "data_needed": "expert_analyzer",
        "expert_call": "expert_implementer",
    }

    if not mapping.get(signal_type):
        if "审查" in content_lower or "review" in content_lower:
            return "expert_reviewer"
        if "测试" in content_lower or "test" in content_lower:
            return "expert_tester"
        if "分析" in content_lower or "analy" in content_lower:
            return "expert_analyzer"
        if "实现" in content_lower or "implement" in content_lower or "写" in content_lower:
            return "expert_implementer"
        if "代码" in content_lower or "code" in content_lower:
            return "supervisor_code"
        if "搜索" in content_lower or "search" in content_lower or "查找" in content_lower:
            return "supervisor_query"

    return mapping.get(signal_type, "")


def _identity_key_to_tier(identity_key: str) -> str:
    """从身份模板键推断 tier"""
    if identity_key.startswith("supervisor"):
        return "supervisor"
    if identity_key.startswith("expert"):
        return "expert"
    if identity_key == "large":
        return "large"
    return "expert"
