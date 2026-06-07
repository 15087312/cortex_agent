"""委托角色解析器。"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# 自然语言角色名 → (tier, identity_key)
# 包括英文无空格版本（delegate_task 参数）和自然语言别名
ROLE_TO_IDENTITY: Dict[str, Tuple[str, str]] = {
    # ===== 主管 (supervisor) =====
    # code_supervisor
    "code_supervisor": ("supervisor", "supervisor_code"),  # ← delegate_task role 参数
    "code supervisor": ("supervisor", "supervisor_code"),  # ← 自然语言别名
    "代码主管": ("supervisor", "supervisor_code"),
    "编码主管": ("supervisor", "supervisor_code"),

    # query_supervisor
    "query_supervisor": ("supervisor", "supervisor_query"),  # ← delegate_task role 参数
    "query supervisor": ("supervisor", "supervisor_query"),  # ← 自然语言别名
    "查询主管": ("supervisor", "supervisor_query"),
    "信息主管": ("supervisor", "supervisor_query"),
    "搜索主管": ("supervisor", "supervisor_query"),
    "查询专家": ("supervisor", "supervisor_query"),
    "搜索专家": ("supervisor", "supervisor_query"),
    "信息专家": ("supervisor", "supervisor_query"),
    "检索专家": ("supervisor", "supervisor_query"),

    # creative_supervisor
    "creative_supervisor": ("supervisor", "supervisor_creative"),  # ← delegate_task role 参数
    "creative supervisor": ("supervisor", "supervisor_creative"),  # ← 自然语言别名
    "创意主管": ("supervisor", "supervisor_creative"),
    "创意": ("supervisor", "supervisor_creative"),

    # ===== 专家 (expert) =====
    # code_reviewer
    "code_reviewer": ("expert", "expert_reviewer"),  # ← delegate_task role 参数
    "代码审查专家": ("expert", "expert_reviewer"),
    "审查专家": ("expert", "expert_reviewer"),
    "代码审查": ("expert", "expert_reviewer"),
    "reviewer": ("expert", "expert_reviewer"),

    # code_writer
    "code_writer": ("expert", "expert_implementer"),  # ← delegate_task role 参数
    "代码实现专家": ("expert", "expert_implementer"),
    "实现专家": ("expert", "expert_implementer"),
    "代码编写": ("expert", "expert_implementer"),
    "implementer": ("expert", "expert_implementer"),
    "coder": ("expert", "expert_implementer"),
    "file_expert": ("expert", "expert_implementer"),  # 文件操作专家 → 实现专家
    "文件专家": ("expert", "expert_implementer"),
    "file_operator": ("expert", "expert_implementer"),

    # test_writer
    "test_writer": ("expert", "expert_tester"),  # ← delegate_task role 参数
    "测试专家": ("expert", "expert_tester"),
    "测试编写": ("expert", "expert_tester"),
    "tester": ("expert", "expert_tester"),

    # data_analyzer
    "data_analyzer": ("expert", "expert_analyzer"),  # ← delegate_task role 参数
    "分析专家": ("expert", "expert_analyzer"),
    "数据分析": ("expert", "expert_analyzer"),
    "analyst": ("expert", "expert_analyzer"),

    # security_monitor
    "security_monitor": ("expert", "expert_security_monitor"),  # ← delegate_task role 参数
    "安全监察": ("expert", "expert_security_monitor"),
    "安全审查": ("expert", "expert_security_monitor"),
    "安全审核": ("expert", "expert_security_monitor"),
    "安全专家": ("expert", "expert_security_monitor"),

    # customer
    "customer": ("expert", "expert_customer"),  # ← delegate_task role 参数
    "客户": ("expert", "expert_customer"),
    "客户专家": ("expert", "expert_customer"),
    "验收专家": ("expert", "expert_customer"),
    "用户代表": ("expert", "expert_customer"),

    # creative_writer
    "creative_writer": ("expert", "expert_creative_writer"),  # ← delegate_task role 参数
    "文学创作专家": ("expert", "expert_creative_writer"),
    "写作专家": ("expert", "expert_creative_writer"),
    "创意写作专家": ("expert", "expert_creative_writer"),

    # emotion
    "emotion": ("expert", "expert_emotion"),  # ← delegate_task role 参数
    "情绪分析师": ("expert", "expert_emotion"),
    "情绪分析": ("expert", "expert_emotion"),

    # memory_manager
    "memory_manager": ("expert", "expert_memory_manager"),  # ← delegate_task role 参数
    "记忆管理员": ("expert", "expert_memory_manager"),
    "记忆管理": ("expert", "expert_memory_manager"),

    # ===== 大模型 (large) =====
    # orchestrator
    "orchestrator": ("large", "large"),  # ← delegate_task role 参数
    "总指挥": ("large", "large"),
    "协调": ("large", "large"),
}


def resolve_role(role_name: str) -> Optional[Tuple[str, str]]:
    """将模型控制工具中的角色名称解析为 (tier, identity_key)。"""
    role_name = str(role_name or "").strip()
    if not role_name:
        return None

    if role_name in ROLE_TO_IDENTITY:
        return ROLE_TO_IDENTITY[role_name]

    role_lower = role_name.lower()
    for name, identity in ROLE_TO_IDENTITY.items():
        name_lower = name.lower()
        if name_lower in role_lower or role_lower in name_lower:
            return identity

    return None
