"""单次模型思考循环的委托抽象。

ContinuousThinker 负责决定模型是否要委托，但具体的系统动作（probe_start / runner 激活）
由这个 port 封装，这样 thinker 就不需要直接依赖 probe 工具。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass
class DelegationRequest:
    """一次思考调用中发出的模型内部委托请求。"""

    role: str
    task: str
    session_id: str = ""
    caller_model_id: str = ""
    caller_tier: str = "large"
    return_to_model_id: str = ""
    return_to_session_id: str = ""
    task_id: str = ""
    wait_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegationResult:
    """分发委托请求的结果。"""

    success: bool
    probe_id: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class DelegationPort(Protocol):
    """ContinuousThinker 用于分发委托的抽象端口。"""

    def delegate(self, request: DelegationRequest) -> DelegationResult:
        ...


class ProbeDelegationAdapter:
    """基于 probe_start 的委托适配器。"""

    def delegate(self, request: DelegationRequest) -> DelegationResult:
        try:
            from modules.thinking.probes.probe_tools import probe_start

            identity = _resolve_role(request.role)
            if identity is None:
                return DelegationResult(
                    success=False,
                    error=f"未知委托角色: {request.role}",
                )

            target_tier, identity_key = identity
            raw = probe_start(
                target_tier=target_tier,
                identity_key=identity_key,
                task_description=request.task,
                probe_priority=str(request.metadata.get("probe_priority", "MEDIUM")),
                ttl_seconds=int(request.metadata.get("ttl_seconds", 1800)),
                _caller_role=request.caller_tier,
                _caller_model_id=request.caller_model_id,
                _session_id=request.session_id,
                return_to_model_id=request.return_to_model_id or request.caller_model_id,
                return_to_session_id=request.return_to_session_id or request.session_id,
                task_id=request.task_id,
            )
            return DelegationResult(
                success=bool(raw.get("success")),
                probe_id=str(raw.get("probe_id", "") or ""),
                error=str(raw.get("error", "") or ""),
                metadata=dict(raw),
            )
        except Exception as e:
            return DelegationResult(success=False, error=str(e))


def create_delegation_port() -> DelegationPort:
    """创建默认委托端口的工厂函数。"""
    return ProbeDelegationAdapter()


# ── 角色名解析表 ──

ROLE_TO_IDENTITY: Dict[str, tuple[str, str]] = {
    # 主管
    "code_supervisor": ("supervisor", "supervisor_code"),
    "code supervisor": ("supervisor", "supervisor_code"),
    "代码主管": ("supervisor", "supervisor_code"),
    "query_supervisor": ("supervisor", "supervisor_query"),
    "query supervisor": ("supervisor", "supervisor_query"),
    "查询主管": ("supervisor", "supervisor_query"),
    "creative_supervisor": ("supervisor", "supervisor_creative"),
    "创意主管": ("supervisor", "supervisor_creative"),
    # 专家
    "code_reviewer": ("expert", "expert_reviewer"),
    "代码审查专家": ("expert", "expert_reviewer"),
    "code_writer": ("expert", "expert_implementer"),
    "代码实现专家": ("expert", "expert_implementer"),
    "test_writer": ("expert", "expert_tester"),
    "测试专家": ("expert", "expert_tester"),
    "data_analyzer": ("expert", "expert_analyzer"),
    "分析专家": ("expert", "expert_analyzer"),
    "customer": ("expert", "expert_customer"),
    "客户": ("expert", "expert_customer"),
    "creative_writer": ("expert", "expert_creative_writer"),
    "创意写作专家": ("expert", "expert_creative_writer"),
    "emotion": ("expert", "expert_emotion"),
    "情绪分析师": ("expert", "expert_emotion"),
    "memory_manager": ("expert", "expert_memory_manager"),
    "记忆管理员": ("expert", "expert_memory_manager"),
    # 大模型
    "orchestrator": ("large", "orchestrator"),
    "总指挥": ("large", "large"),
}

def _resolve_role(role_name: str) -> Optional[tuple[str, str]]:
    role_name = str(role_name or "").strip()
    if not role_name:
        return None
    if role_name in ROLE_TO_IDENTITY:
        return ROLE_TO_IDENTITY[role_name]
    role_lower = role_name.lower()
    for name, identity in ROLE_TO_IDENTITY.items():
        if name.lower() in role_lower or role_lower in name.lower():
            return identity
    return None
