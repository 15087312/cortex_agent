"""单次模型思考循环的委托抽象。

ContinuousThinker 负责决定模型是否要委托，但具体的系统动作（probe_start / runner 激活）
由这个 port 封装，这样 thinker 就不需要直接依赖 probe 工具。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol

from utils.logger import get_logger

logger = get_logger(__name__)

# 委托任务正文不再硬切 500 字：全量透传，下级按需从黑板拉上下文。
# 消息总线只做上限保护，避免超大 payload；截断时保留头尾并注记。
DELEGATE_TASK_SOFT_CAP = 8000


def build_delegation_payload(task: str, cap: int = DELEGATE_TASK_SOFT_CAP) -> str:
    """构建透传给下级的任务正文：默认全量，超限才结构化截断。"""
    text = str(task or "")
    if len(text) <= cap:
        return text
    head = text[: cap - 1000]
    tail = text[-1000:]
    return (
        f"{head}\n\n...[任务正文超长已截断中间部分，共{len(text)}字符，"
        f"下级可通过 read_context / query_delegation 从黑板拉取完整上下文]...\n\n{tail}"
    )


# 委托未显式指定超时时的兜底（delegate_task 工具已要求必填 wait_seconds）
DEFAULT_DELEGATE_THINK_TIMEOUT = 300


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


# @link:block Delegate_Emit = Guard · Emit
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

            # 编排页启停检查：已关闭的主管/专家拒绝委托（与能力表过滤一致，
            # 避免"提示词看不到、委托却接受"的脱节）
            try:
                from config.settings import settings as _active_cfg
                if not _active_cfg.get_agent_active(identity_key):
                    return DelegationResult(
                        success=False,
                        error=f"委托角色已禁用: {identity_key}（编排页激活开关）",
                    )
            except Exception as e:
                logger.debug(f"[delegation_port] 编排页启停检查失败，按启用继续: {e}")

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

            # 委托链传播：父委托 / 根任务（由 model_runner 分发时写入 request.metadata）
            parent_delegation_id = (request.metadata or {}).get("parent_delegation_id", "") or ""
            origin_task_id = (request.metadata or {}).get("origin_task_id", "") or task_id

            # 委托必须指定下级思考超时（delegate_task 工具 required）。缺省时兜底并告警。
            think_timeout = request.wait_seconds
            if not think_timeout:
                think_timeout = DEFAULT_DELEGATE_THINK_TIMEOUT
                import logging as _logging
                _logging.getLogger("delegation_port").warning(
                    f"委托未指定 wait_seconds，使用兜底超时 {think_timeout}s"
                )

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
                    "task_description": build_delegation_payload(request.task),
                    "task_full_length": len(str(request.task or "")),
                    "return_to_model_id": request.return_to_model_id or request.caller_model_id,
                    "return_to_session_id": request.return_to_session_id or request.session_id,
                    "task_id": task_id,
                    "caller_tier": request.caller_tier,
                    "caller_model_id": request.caller_model_id,
                    "priority": 5,
                    "ttl_seconds": 1800,
                    "think_timeout": int(think_timeout),
                    "parent_delegation_id": parent_delegation_id,
                    "origin_task_id": origin_task_id,
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


# ── 角色名解析：身份文件即唯一来源 ──
# 兼容旧中文别名（历史数据），仅作兜底；新增角色无需改代码，
# 只要在 roles.yaml / data/identities / 编排页自定义 Agent 里定义即可。
_LEGACY_ROLE_ALIASES: Dict[str, str] = {
    "code supervisor": "code_supervisor",
    "代码主管": "code_supervisor",
    "query supervisor": "query_supervisor",
    "查询主管": "query_supervisor",
    "创意主管": "creative_supervisor",
    "代码审查专家": "code_reviewer",
    "代码实现专家": "code_writer",
    "test_writer": "tester",
    "测试专家": "tester",
    "data_analyzer": "code_writer",
    "分析专家": "code_writer",
    "客户": "customer",
    "前端设计专家": "ui_designer",
    "创意写作专家": "code_writer",
    "情绪分析师": "code_writer",
    "记忆管理员": "code_writer",
    "总指挥": "orchestrator",
}

# 兼容旧引用：保留同名变量，内容由身份文件动态生成
ROLE_TO_IDENTITY: Dict[str, tuple[str, str]] = {}


def _rebuild_legacy_map() -> None:
    """从身份文件重建兼容映射（进程内缓存失效后重建）。"""
    try:
        from modules.thinking.identity import get_identities
        identities = get_identities()
        rebuilt: Dict[str, tuple[str, str]] = {}
        for key, ident in identities.items():
            tier = (ident.get("tier") if isinstance(ident, dict) else None) or "expert"
            rebuilt[key] = (tier, key)
            name = (ident.get("name") if isinstance(ident, dict) else "") or ""
            if name and name not in rebuilt:
                rebuilt[name] = (tier, key)
        for alias, target in _LEGACY_ROLE_ALIASES.items():
            if target in identities:
                ident = identities[target]
                tier = (ident.get("tier") if isinstance(ident, dict) else None) or "expert"
                rebuilt.setdefault(alias, (tier, target))
        ROLE_TO_IDENTITY.clear()
        ROLE_TO_IDENTITY.update(rebuilt)
    except Exception as e:
        logger.warning(f"[delegation_port] 重建角色兼容映射失败: {e}")


def _normalize_role_key(text: str) -> str:
    """归一化角色 key：去首尾空格，转小写，空格/连字符统一为下划线，压缩重复下划线。

    中文不受影响（无大小写/下划线概念）。归一化后仍对不上才报错，
    不做子串模糊匹配。
    """
    import re
    t = str(text or "").strip().lower()
    t = re.sub(r"[\s\-]+", "_", t)
    t = re.sub(r"_+", "_", t)
    return t


def _resolve_role(role_name: str) -> Optional[tuple[str, str]]:
    raw = str(role_name or "").strip()
    if not raw:
        return None
    norm = _normalize_role_key(raw)
    # 1. 身份文件精确命中（含编排页自定义 agent）：唯一真源
    try:
        from modules.thinking.identity import get_identities
        identities = get_identities()
        if raw in identities:
            ident = identities[raw]
            tier = ident.get("tier") or "expert"
            _rebuild_legacy_map()
            return tier, raw
        # 归一化后命中（Code Supervisor / code-supervisor / CODE_SUPERVISOR 同一）
        norm_index = {_normalize_role_key(k): k for k in identities}
        if norm in norm_index:
            key = norm_index[norm]
            tier = identities[key].get("tier") or "expert"
            _rebuild_legacy_map()
            return tier, key
        # name / role 字段命中（“代码主管”这类中文名，同样归一化比对）
        for key, ident in identities.items():
            if _normalize_role_key(ident.get("name") or "") == norm and norm:
                tier = ident.get("tier") or "expert"
                _rebuild_legacy_map()
                return tier, key
            if _normalize_role_key(ident.get("role") or "") == norm and norm:
                tier = ident.get("tier") or "expert"
                _rebuild_legacy_map()
                return tier, key
        # 旧别名兜底（归一化比对）
        norm_aliases = {_normalize_role_key(k): v for k, v in _LEGACY_ROLE_ALIASES.items()}
        if norm in norm_aliases:
            target = norm_aliases[norm]
            if target in identities:
                tier = identities[target].get("tier") or "expert"
                _rebuild_legacy_map()
                return tier, target
        # 无模糊兜底：子串匹配（如 code 同时命中主管/审查/实现）顺序依赖、
        #  typo 会被静默派错地方，宁可直接失败让上层报可用列表。
    except Exception as e:
        logger.warning(f"[delegation_port] 角色解析失败 role={raw!r}: {e}")
    return None
