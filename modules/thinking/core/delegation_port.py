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

    async def delegate(self, request: DelegationRequest) -> DelegationResult:
        ...


class ProbeDelegationAdapter:
    """基于 MessageBus 的委托适配器 — 通过探针消息激活目标模型"""

    async def delegate(self, request: DelegationRequest) -> DelegationResult:
        try:
            from modules.thinking.communication.message_bus import Message, MessageType, get_message_bus
            from modules.thinking.probes.probe_permission import get_probe_permission_manager
            from modules.thinking.identity import get_identities
            import uuid

            identity = _resolve_role(request.role)
            if identity is None:
                return DelegationResult(
                    success=False,
                    error=f"未知委托角色: {request.role}",
                )

            target_tier, identity_key = identity

            ppm = get_probe_permission_manager()
            error = ppm.validate_probe_start(request.caller_tier, target_tier, identity_key)
            if error:
                return DelegationResult(success=False, error=error)

            if identity_key not in get_identities():
                return DelegationResult(
                    success=False,
                    error=f"未知的身份模板: {identity_key}",
                )

            probe_id = f"probe_{target_tier}_{identity_key}_{uuid.uuid4().hex[:6]}"
            task_id = request.task_id or f"task_{uuid.uuid4().hex[:8]}"

            bus = get_message_bus()
            msg = Message(
                msg_type=MessageType.SYSTEM,
                sender=request.caller_model_id or "delegation_adapter",
                recipient=f"model_runner_manager_{request.session_id[:8]}",
                content={
                    "action": "probe_started",
                    "probe_id": probe_id,
                    "target_tier": target_tier,
                    "identity_key": identity_key,
                    "task_description": request.task[:500],
                    "return_to_model_id": request.return_to_model_id or request.caller_model_id,
                    "return_to_session_id": request.return_to_session_id or request.session_id,
                    "task_id": task_id,
                    "caller_tier": request.caller_tier,
                    "priority": 5,
                    "ttl_seconds": 1800,
                },
            )
            await bus.send(msg)

            return DelegationResult(
                success=True,
                probe_id=probe_id,
                metadata={"probe_id": probe_id, "task_id": task_id, "target_tier": target_tier},
            )
        except Exception as e:
            return DelegationResult(success=False, error=str(e))


def create_delegation_port() -> DelegationPort:
    """创建默认委托端口的工厂函数。"""
    return ProbeDelegationAdapter()


# ── 角色名解析表 ──

ROLE_TO_IDENTITY: Dict[str, tuple[str, str]] = {
    "code_supervisor": ("supervisor", "code_supervisor"),
    "code supervisor": ("supervisor", "code_supervisor"),
    "代码主管": ("supervisor", "code_supervisor"),
    "query_supervisor": ("supervisor", "query_supervisor"),
    "query supervisor": ("supervisor", "query_supervisor"),
    "查询主管": ("supervisor", "query_supervisor"),
    "creative_supervisor": ("supervisor", "creative_supervisor"),
    "创意主管": ("supervisor", "creative_supervisor"),
    "code_reviewer": ("expert", "code_reviewer"),
    "代码审查专家": ("expert", "code_reviewer"),
    "code_writer": ("expert", "code_writer"),
    "代码实现专家": ("expert", "code_writer"),
    "test_writer": ("expert", "tester"),
    "测试专家": ("expert", "tester"),
    "data_analyzer": ("expert", "code_writer"),
    "分析专家": ("expert", "code_writer"),
    "customer": ("expert", "customer"),
    "客户": ("expert", "customer"),
    "creative_writer": ("expert", "code_writer"),
    "创意写作专家": ("expert", "code_writer"),
    "emotion": ("expert", "code_writer"),
    "情绪分析师": ("expert", "code_writer"),
    "memory_manager": ("expert", "code_writer"),
    "记忆管理员": ("expert", "code_writer"),
    "orchestrator": ("large", "orchestrator"),
    "总指挥": ("large", "orchestrator"),
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
