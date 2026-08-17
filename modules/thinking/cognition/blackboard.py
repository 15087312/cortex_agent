"""
认知黑板 — 唯一的认知状态源 (Single Source of Truth)

替代现在分散的：
- SharedDialog._blackboard（delegations/tool_results/expert_findings/final_draft）
- ContinuousThinker._pending_delegations
- ContinuousThinker._last_sd_read_count（改为 _write_cursors）

设计原则：
- 所有 Agent 的读写都通过 CognitiveBlackboard
- 支持权限控制（某些字段只有特定 tier 可写）
- 支持切片（通过 snapshot_for 获取对应 tier 的视图）
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from collections import deque
from utils.logger import setup_logger

logger = setup_logger("cognitive_blackboard")


@dataclass
class Delegation:
    """委托任务 — 扩展为完整委托链节点

    delegation_id 使用每次委托唯一的 probe_id（可跨层级区分），
    与沿链共享的 task_id 解耦；parent/children 构成委托树。
    """
    delegation_id: str
    role: str  # supervisor_code / supervisor_query / ...
    task: str
    status: str = "pending"  # pending / running / replied / stale
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # ── 委托链字段 ──
    caller_model_id: str = ""          # 发起委托的模型（直接父级）
    caller_tier: str = ""              # 发起方层级
    parent_delegation_id: str = ""     # 父委托的 delegation_id（链）
    return_to_model_id: str = ""       # 结果返回给谁（缺省 = caller_model_id）
    return_to_session_id: str = ""
    origin_task_id: str = ""           # 根任务 id（沿链传播）
    probe_id: str = ""                 # 本次委托唯一探针
    target_tier: str = ""              # 目标层级
    target_model_id: str = ""          # 实际激活的下级 runner model_id
    progress: str = ""                 # 进度描述（供 query_delegation 查看）
    context_summary: str = ""          # 委托相关上下文摘要
    child_delegation_ids: List[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


@dataclass
class Observation:
    """观察结果（通常由 expert 写入）"""
    observation_id: str
    tier: str  # large / supervisor / expert
    content: str
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExpertFinding:
    """专家发现（由 supervisor/expert 写入）"""
    finding_id: str
    source_tier: str
    role: str  # 角色标识
    content: str
    status: str = "pending"  # pending / completed / rejected
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DialogEntry:
    """对话框条目 — 统一 UserInput / Thought / Response"""
    entry_id: str = ""
    entry_type: str = "thought"      # thought / response / user_input
    model_id: str = ""
    tier: str = ""
    content: str = ""
    round_num: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        import uuid
        if not self.entry_id:
            self.entry_id = f"dlg_{int(self.timestamp)}_{uuid.uuid4().hex[:6]}"

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "type": self.entry_type,
            "model_id": self.model_id,
            "tier": self.tier,
            "content": self.content,
            "round": self.round_num,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class BlackboardSnapshot:
    """黑板快照 — 供 Agent 读取的上下文"""
    goal: str
    current_plan: List[Dict[str, Any]]
    observations: List[Observation]
    delegations: Dict[str, Delegation]
    expert_findings: Dict[str, ExpertFinding]
    runtime_state: Dict[str, Any]
    metadata: Dict[str, Any]


class CognitiveBlackboard:
    """
    认知黑板 — 多 Agent 共享的唯一状态源

    特点：
    - Thread-safe（使用 RLock）
    - 支持时间戳过滤（用于增量读取）
    - 权限隐式检查（通过方法签名）
    - 支持快照（snapshot_for）
    """

    # 观察列表容量上限：超过则清理最旧的（读取方只取最近几条，旧观察无推理价值）
    MAX_OBSERVATIONS = 200

    def __init__(self, session_id: str, turn_id: str, blackboard_id: Optional[str] = None):
        self._session_id = session_id
        self._turn_id = turn_id
        import uuid as _uuid
        self._blackboard_id = blackboard_id or f"bb_{_uuid.uuid4().hex[:12]}"
        self._lock = threading.RLock()

        # ── 认知状态字段 ──
        self.goal: str = ""
        self.current_plan: List[Dict[str, Any]] = []
        self.active_tasks: List[Dict[str, Any]] = []
        self.observations: List[Observation] = []
        self.risks: List[Dict[str, Any]] = []
        self.memory_refs: List[str] = []
        # 已从 observations 头部清理掉的条数（用于增量读取 cursor 的逻辑索引→物理索引映射）
        self._deleted_obs: int = 0

        # ── 黑板区块 ──
        self.delegations: Dict[str, Delegation] = {}
        self.expert_findings: Dict[str, ExpertFinding] = {}
        self.decisions: List[Dict[str, Any]] = []

        # ── 运行时状态 ──
        self.runtime_state: Dict[str, Any] = {}
        self.final_response: Optional[str] = None

        # ── 安全拦截信号 ──
        self._security_block: Optional[Dict[str, Any]] = None

        # ── 增量读取追踪（替代 _last_sd_read_count）──
        self._write_cursors: Dict[str, int] = {}  # tier → 最后读取到的 index

        # ── 对话历史 (替代 SharedDialog) ──
        self._dialog_entries: deque = deque(maxlen=500)
        self._last_read_index: int = 0
        self._change_callbacks: List[Callable[[str], None]] = []

        # ── 断点续思考上下文（工具循环 messages 快照，随黑板按 session+blackboard_id 落库）──
        self.resume_context: Optional[List[Dict[str, Any]]] = None

        logger.debug(
            f"[CognitiveBlackboard] 创建: session={session_id[:8]}, turn={turn_id[:8]}"
        )

    # ── 生命周期 ──

    def clear_turn_state(self) -> None:
        """新一轮开始时清空状态（替代 SharedDialog.mark_final_drafts_superseded() + clear()）"""
        with self._lock:
            self.goal = ""
            self.current_plan = []
            self.active_tasks = []
            self.observations = []
            self.risks = []
            self.delegations.clear()
            self.expert_findings.clear()
            self.decisions = []
            self.runtime_state = {}
            self.final_response = None
            self._write_cursors.clear()
            self._dialog_entries.clear()
            self._last_read_index = 0
        logger.info(f"[CognitiveBlackboard] turn={self._turn_id[:8]} 已清空")

    # ── 写入接口 ──

    def set_goal(self, goal: str) -> None:
        """设置对话目标"""
        with self._lock:
            self.goal = goal

    def set_plan(self, plan: List[Dict[str, Any]]) -> None:
        """更新执行计划"""
        with self._lock:
            self.current_plan = plan

    def add_observation(
        self,
        tier: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """添加观察结果（超过 MAX_OBSERVATIONS 时清理最旧的 observation）"""
        import uuid
        observation_id = f"obs_{uuid.uuid4().hex[:8]}"
        obs = Observation(
            observation_id=observation_id,
            tier=tier,
            content=content,
            metadata=metadata or {},
        )
        with self._lock:
            self.observations.append(obs)
            # 清理旧 observation：只保留最近 MAX_OBSERVATIONS 条。
            # 读取方（slice_for_large/system prompt/expert）只取最近 5 条，
            # 删除最旧的不影响推理，可防止长轮次思考时观察列表无限膨胀。
            overflow = len(self.observations) - self.MAX_OBSERVATIONS
            if overflow > 0:
                del self.observations[:overflow]
                self._deleted_obs += overflow
                logger.debug(
                    f"[CognitiveBlackboard] 清理旧 observation {overflow} 条 "
                    f"(当前 {len(self.observations)}/{self.MAX_OBSERVATIONS})"
                )
        # 落库追溯（内存溢出清理后旧观察仍可查询，不阻塞/不影响内存逻辑）
        try:
            from modules.database.blackboard_repo import save_observation
            save_observation(self._session_id, obs)
        except Exception:
            pass
        logger.debug(f"[CognitiveBlackboard] 添加观察: {observation_id}")
        return observation_id

    def write_delegation(
        self,
        role: str,
        task: str,
        metadata: Optional[Dict] = None,
        *,
        delegation_id: Optional[str] = None,
        caller_model_id: str = "",
        caller_tier: str = "",
        parent_delegation_id: str = "",
        return_to_model_id: str = "",
        return_to_session_id: str = "",
        origin_task_id: str = "",
        probe_id: str = "",
        target_tier: str = "",
    ) -> str:
        """创建委托（通常由 large 调用），记录完整委托链字段

        delegation_id 缺省用 probe_id（唯一），再缺省生成 dlg_xxx。
        """
        import uuid
        did = delegation_id or probe_id or f"dlg_{uuid.uuid4().hex[:8]}"
        delegation = Delegation(
            delegation_id=did,
            role=role,
            task=task,
            metadata=metadata or {},
            caller_model_id=caller_model_id,
            caller_tier=caller_tier,
            parent_delegation_id=parent_delegation_id,
            return_to_model_id=return_to_model_id or caller_model_id,
            return_to_session_id=return_to_session_id,
            origin_task_id=origin_task_id,
            probe_id=probe_id or did,
            target_tier=target_tier,
            updated_at=time.time(),
        )
        with self._lock:
            # 维护父子链
            if parent_delegation_id and parent_delegation_id in self.delegations:
                parent = self.delegations[parent_delegation_id]
                if did not in parent.child_delegation_ids:
                    parent.child_delegation_ids.append(did)
            self.delegations[did] = delegation
        logger.debug(f"[CognitiveBlackboard] 创建委托: {did} → {role}")
        return did

    def update_delegation_status(
        self,
        delegation_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """更新委托状态（只有对应角色的回复才能触发）"""
        with self._lock:
            delegation = self.delegations.get(delegation_id)
            if not delegation:
                return False
            delegation.status = status
            if metadata:
                delegation.metadata.update(metadata)
        logger.debug(f"[CognitiveBlackboard] 委托 {delegation_id} 状态更新: {status}")
        return True

    def update_delegation_progress(
        self,
        delegation_id: str,
        progress: str = "",
        status: Optional[str] = None,
        target_model_id: str = "",
        context_summary: str = "",
    ) -> bool:
        """更新委托进度 / 状态 / 目标模型 / 上下文摘要（供 query_delegation 查看）"""
        with self._lock:
            delegation = self.delegations.get(delegation_id)
            if not delegation:
                return False
            if progress:
                delegation.progress = progress
            if context_summary:
                delegation.context_summary = context_summary
            if status:
                delegation.status = status
            if target_model_id:
                delegation.target_model_id = target_model_id
            delegation.updated_at = time.time()
        logger.debug(f"[CognitiveBlackboard] 委托 {delegation_id} 进度更新: {progress[:60]}")
        return True

    def get_delegation(self, delegation_id: str) -> Optional[Dict[str, Any]]:
        """获取单个委托（序列化视图，供工具返回）"""
        with self._lock:
            d = self.delegations.get(delegation_id)
            if not d:
                return None
            from dataclasses import asdict
            view = asdict(d)
            view["status"] = d.status
            return view

    def get_delegation_chain(self, delegation_id: str) -> List[Dict[str, Any]]:
        """获取委托链：从根（父）到叶子（子）的完整序列化列表"""
        from dataclasses import asdict
        with self._lock:
            # 回溯到根
            chain = []
            node = self.delegations.get(delegation_id)
            if not node:
                return chain
            ancestors = []
            cur = node
            while cur.parent_delegation_id and cur.parent_delegation_id in self.delegations:
                cur = self.delegations[cur.parent_delegation_id]
                ancestors.append(cur)
            for a in reversed(ancestors):
                chain.append(asdict(a))
            chain.append(asdict(node))
            # 向下收集子委托
            stack = list(node.child_delegation_ids)
            seen = set()
            while stack:
                cid = stack.pop(0)
                if cid in seen or cid not in self.delegations:
                    continue
                seen.add(cid)
                child = self.delegations[cid]
                chain.append(asdict(child))
                stack.extend(child.child_delegation_ids)
            return chain

    def build_delegation_context(
        self,
        delegation_id: str,
        context_limit: int = 2000,
    ) -> str:
        """构建委托进度 + 上下文文本（供 query_delegation 查看），按 context_limit 截取"""
        from dataclasses import asdict
        with self._lock:
            d = self.delegations.get(delegation_id)
            if not d:
                return ""
            parts = []
            parts.append(f"【委托 {delegation_id}】状态={d.status}")
            parts.append(f"目标角色: {d.role}  target_model={d.target_model_id or '待激活'}")
            parts.append(f"调用方: {d.caller_model_id or '未知'} ({d.caller_tier or '?'})")
            if d.return_to_model_id:
                parts.append(f"结果返回: {d.return_to_model_id}")
            if d.progress:
                parts.append(f"进度: {d.progress}")
            parts.append(f"任务: {d.task}")
            if d.context_summary:
                parts.append(f"上下文: {d.context_summary}")
            resp = d.metadata.get("response") or d.metadata.get("result") or ""
            if resp:
                parts.append(f"结果: {resp}")
            if d.child_delegation_ids:
                parts.append(f"子委托: {', '.join(d.child_delegation_ids)}")
            text = "\n".join(parts)
            if context_limit and context_limit > 0 and len(text) > context_limit:
                text = text[:context_limit] + f"...[已截断，共{len(text)}字符]"
            return text

    def write_expert_finding(
        self,
        source_tier: str,
        role: str,
        content: str,
        status: str = "completed",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """专家发现（由 supervisor/expert 调用）"""
        import uuid
        finding_id = f"find_{uuid.uuid4().hex[:8]}"
        finding = ExpertFinding(
            finding_id=finding_id,
            source_tier=source_tier,
            role=role,
            content=content,
            status=status,
            metadata=metadata or {},
        )
        with self._lock:
            self.expert_findings[finding_id] = finding
        logger.debug(f"[CognitiveBlackboard] 专家发现: {finding_id} from {role}")
        return finding_id

    def set_runtime_state(self, state: Dict[str, Any]) -> None:
        """更新运行时状态"""
        with self._lock:
            self.runtime_state.update(state)

    def set_final_response(self, content: str) -> None:
        """设置最终回复"""
        with self._lock:
            self.final_response = content
        logger.info(
            f"[CognitiveBlackboard] 最终回复已设置: {len(content)} 字符"
        )

    def set_security_block(self, category: str, description: str, risk_level: str = "high") -> None:
        """设置安全拦截信号（由 SecurityMonitor 调用）"""
        with self._lock:
            self._security_block = {
                "category": category,
                "description": description,
                "risk_level": risk_level,
            }
        logger.warning(f"[CognitiveBlackboard] 安全拦截: {category} - {description[:80]}")

    def has_security_block(self) -> bool:
        """是否有安全拦截信号"""
        with self._lock:
            return self._security_block is not None

    def get_security_block(self) -> Optional[Dict[str, Any]]:
        """获取安全拦截信息"""
        with self._lock:
            return self._security_block

    def clear_security_block(self) -> None:
        """清除安全拦截信号"""
        with self._lock:
            self._security_block = None

    # ── 对话框写入（替代 SharedDialog）──

    def on_change(self, callback: Callable[[str], None]) -> None:
        """注册变更回调 — 每次写入新 dialog entry 时调用"""
        self._change_callbacks.append(callback)

    def _notify_change(self) -> None:
        for cb in self._change_callbacks:
            try:
                cb(self._session_id)
            except Exception as e:
                logger.debug(f"[CognitiveBlackboard] 变更回调异常: {e}")

    def _broadcast(self, entry: DialogEntry) -> None:
        """通过 MessageBus 广播到所有订阅者（fire-and-forget）"""
        try:
            import asyncio
            from modules.thinking.communication.message_bus import (
                Message, MessageType, get_message_bus,
            )
            bus = get_message_bus()
            msg = Message(
                msg_type=MessageType.BROADCAST,
                sender=entry.model_id,
                recipient="broadcast",
                content={
                    "dialog_id": self._session_id,
                    "entry_type": entry.entry_type,
                    "model_id": entry.model_id,
                    "tier": entry.tier,
                    "content": entry.content,
                    "round": entry.round_num,
                    "timestamp": entry.timestamp,
                    "metadata": entry.metadata,
                },
                metadata={"dialog_id": self._session_id, "tier": entry.tier},
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bus.broadcast(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.debug(f"[CognitiveBlackboard] 广播失败 (非致命): {e}")

    def write_thought(
        self, model_id: str, tier: str, content: str,
        round_num: int = 0, metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[DialogEntry]:
        """写入模型思考过程"""
        content_str = str(content)
        if not content_str.strip():
            return None  # 空内容不写入
        entry = DialogEntry(
            entry_type="thought", model_id=model_id, tier=tier,
            content=content_str, round_num=round_num,
            metadata=metadata or {},
        )
        with self._lock:
            self._dialog_entries.append(entry)
        self._notify_change()
        self._broadcast(entry)
        return entry

    def write_response(
        self, model_id: str, tier: str, content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DialogEntry:
        """写入模型最终回复"""
        content_str = str(content)
        entry = DialogEntry(
            entry_type="response", model_id=model_id, tier=tier,
            content=content_str, metadata=metadata or {},
        )
        with self._lock:
            self._dialog_entries.append(entry)
        self._notify_change()
        self._broadcast(entry)
        return entry

    def write_user_input(self, content: str) -> DialogEntry:
        """写入用户输入"""
        content_str = str(content)
        entry = DialogEntry(
            entry_type="user_input", model_id="user", tier="user",
            content=content_str,
        )
        with self._lock:
            self._dialog_entries.append(entry)
        self._notify_change()
        self._broadcast(entry)
        return entry

    # ── 对话框读取（替代 SharedDialog）──

    def read_dialog(self, limit: int = 50) -> List[Dict[str, Any]]:
        """读取最近的对话框记录"""
        with self._lock:
            entries = list(self._dialog_entries)[-limit:]
            return [e.to_dict() for e in entries]

    def new_entries(self) -> List[Dict[str, Any]]:
        """获取自上次读取以来的新条目（用于 CLI 实时流）"""
        with self._lock:
            current = len(self._dialog_entries)
            if current <= self._last_read_index:
                return []
            new = list(self._dialog_entries)[self._last_read_index:]
            self._last_read_index = current
            return [e.to_dict() for e in new]

    def get_latest_response(
        self, tier: Optional[str] = None, after_timestamp: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取最新的一条 response 类型记录"""
        with self._lock:
            for e in reversed(list(self._dialog_entries)):
                if after_timestamp is not None and e.timestamp < after_timestamp:
                    break
                if e.entry_type == "response" and (tier is None or e.tier == tier):
                    return e.to_dict()
        return None

    def get_latest_thought(
        self, tier: Optional[str] = None, after_timestamp: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取最新的一条 thought 类型记录"""
        with self._lock:
            for e in reversed(list(self._dialog_entries)):
                if after_timestamp is not None and e.timestamp < after_timestamp:
                    break
                if e.entry_type == "thought" and (tier is None or e.tier == tier):
                    return e.to_dict()
        return None

    def format_for_model(
        self, limit: int = 15, exclude_tier: Optional[str] = None,
        after_index: Optional[int] = None,
    ) -> str:
        """将对话框内容格式化为模型可读的文本"""
        with self._lock:
            entries = list(self._dialog_entries)
        if after_index is not None:
            entries = entries[after_index:]
        if exclude_tier:
            entries = [e for e in entries if e.tier != exclude_tier]
        entries = [
            e for e in entries
            if e.metadata.get("visibility") != "hidden"
            and e.metadata.get("final_visible") is not False
        ]
        entries = entries[-limit:]

        tier_labels = {
            "large": "[总指挥]", "supervisor": "[主管]",
            "expert": "[专家]", "user": "[用户]",
        }

        lines = []
        if entries:
            lines.append("【共享对话框 — 其他模型的最新输出】")
            for e in entries:
                label = tier_labels.get(e.tier, f"[{e.tier}]")
                text = e.content[:300]
                # 最高指示置顶 + 醒目标记
                if e.metadata.get("must_follow"):
                    lines.append(f"⚡【最高指示 — 必须遵循】{text}")
                elif e.entry_type == "user_input":
                    lines.append(f"{label} [用户新输入]: {text}")
                else:
                    lines.append(f"{label} {e.model_id}: {text}")
        return "\n".join(lines)

    def size(self) -> int:
        """对话框条目数量"""
        with self._lock:
            return len(self._dialog_entries)

    # ── 读取接口 ──

    def snapshot_for(
        self,
        tier: str,
        cursor: int = 0,
    ) -> BlackboardSnapshot:
        """
        为指定 tier 生成快照（含权限过滤）

        cursor：增量读取位置（用于只读新增条目）
        """
        with self._lock:
            # 增量读取起点：cursor 为逻辑索引，补偿已清理的头部 observation
            _obs_start = max(0, cursor - self._deleted_obs)
            # 基础字段所有人都能读
            snapshot_dict: dict = {
                "goal": self.goal,
                "current_plan": self.current_plan.copy(),
                "runtime_state": self.runtime_state.copy(),
                "metadata": {
                    "turn_id": self._turn_id,
                    "session_id": self._session_id,
                    "snapshot_time": time.time(),
                },
            }

            # 按 tier 过滤可见内容
            if tier == "large":
                # Large 看到全部
                snapshot_dict["observations"] = self.observations[_obs_start:]
                snapshot_dict["delegations"] = self.delegations.copy()
                snapshot_dict["expert_findings"] = self.expert_findings.copy()
            elif tier == "supervisor":
                # Q-17: Supervisor只看到自己的发现，不能访问expert的私密发现
                snapshot_dict["observations"] = [
                    o for o in self.observations[_obs_start:]
                    if o.tier in ("expert", "supervisor")
                ]
                snapshot_dict["delegations"] = self.delegations.copy()
                # Only show findings from supervisor tier or marked as shared
                snapshot_dict["expert_findings"] = {
                    k: v for k, v in self.expert_findings.items()
                    if v.source_tier == "supervisor" or v.metadata.get("shared", False)
                }
            elif tier == "expert":
                # Expert 只看到最近的观察和执行历史
                snapshot_dict["observations"] = self.observations[_obs_start:][-5:]
                snapshot_dict["delegations"] = {}
                snapshot_dict["expert_findings"] = {}
            else:
                snapshot_dict["observations"] = []
                snapshot_dict["delegations"] = {}
                snapshot_dict["expert_findings"] = {}

            return BlackboardSnapshot(**snapshot_dict)

    def get_observations_since(self, cursor: int) -> List[Observation]:
        """获取自 cursor 之后的新观察（cursor 为逻辑索引，自动补偿已清理的头部）"""
        with self._lock:
            start = max(0, cursor - self._deleted_obs)
            return self.observations[start:]

    def get_status(self) -> dict:
        """获取黑板状态摘要"""
        with self._lock:
            return {
                "turn_id": self._turn_id[:8],
                "goal_set": bool(self.goal),
                "plan_steps": len(self.current_plan),
                "observations_count": len(self.observations),
                "delegations_count": len(self.delegations),
                "findings_count": len(self.expert_findings),
                "has_final_response": bool(self.final_response),
                "dialog_entries": len(self._dialog_entries),
            }

    # ── 持久化：按 (session_id, blackboard_id) 落库 ──

    @property
    def blackboard_id(self) -> str:
        return self._blackboard_id

    def to_state_dict(self) -> Dict[str, Any]:
        """序列化黑板完整状态（供 DB 持久化 / 断点恢复）"""
        from dataclasses import asdict
        with self._lock:
            return {
                "session_id": self._session_id,
                "turn_id": self._turn_id,
                "blackboard_id": self._blackboard_id,
                "goal": self.goal,
                "current_plan": self.current_plan,
                "active_tasks": self.active_tasks,
                "observations": [asdict(o) for o in self.observations],
                "risks": self.risks,
                "memory_refs": self.memory_refs,
                "delegations": {k: asdict(v) for k, v in self.delegations.items()},
                "expert_findings": {k: asdict(v) for k, v in self.expert_findings.items()},
                "decisions": self.decisions,
                "runtime_state": self.runtime_state,
                "final_response": self.final_response,
                "dialog_entries": [d.to_dict() for d in self._dialog_entries],
                "resume_context": self.resume_context,
            }

    def _restore(self, data: Dict[str, Any]) -> None:
        """从序列化状态恢复黑板内容"""
        from dataclasses import asdict
        with self._lock:
            self.goal = data.get("goal", "")
            self.current_plan = data.get("current_plan", []) or []
            self.active_tasks = data.get("active_tasks", []) or []
            for raw in data.get("observations", []) or []:
                self.observations.append(Observation(
                    observation_id=raw.get("observation_id", ""),
                    tier=raw.get("tier", ""),
                    content=raw.get("content", ""),
                    created_at=raw.get("created_at", time.time()),
                    metadata=raw.get("metadata", {}) or {},
                ))
            self.risks = data.get("risks", []) or []
            self.memory_refs = data.get("memory_refs", []) or []
            for k, raw in (data.get("delegations", {}) or {}).items():
                self.delegations[k] = Delegation(
                    delegation_id=raw.get("delegation_id", ""),
                    role=raw.get("role", ""),
                    task=raw.get("task", ""),
                    status=raw.get("status", "pending"),
                    created_at=raw.get("created_at", time.time()),
                    metadata=raw.get("metadata", {}) or {},
                    caller_model_id=raw.get("caller_model_id", ""),
                    caller_tier=raw.get("caller_tier", ""),
                    parent_delegation_id=raw.get("parent_delegation_id", ""),
                    return_to_model_id=raw.get("return_to_model_id", ""),
                    return_to_session_id=raw.get("return_to_session_id", ""),
                    origin_task_id=raw.get("origin_task_id", ""),
                    probe_id=raw.get("probe_id", ""),
                    target_tier=raw.get("target_tier", ""),
                    target_model_id=raw.get("target_model_id", ""),
                    progress=raw.get("progress", ""),
                    context_summary=raw.get("context_summary", ""),
                    child_delegation_ids=raw.get("child_delegation_ids", []) or [],
                    updated_at=raw.get("updated_at", time.time()),
                )
            for k, raw in (data.get("expert_findings", {}) or {}).items():
                self.expert_findings[k] = ExpertFinding(
                    finding_id=raw.get("finding_id", ""),
                    source_tier=raw.get("source_tier", ""),
                    role=raw.get("role", ""),
                    content=raw.get("content", ""),
                    status=raw.get("status", "pending"),
                    created_at=raw.get("created_at", time.time()),
                    metadata=raw.get("metadata", {}) or {},
                )
            self.decisions = data.get("decisions", []) or []
            self.runtime_state = data.get("runtime_state", {}) or {}
            self.final_response = data.get("final_response")
            for raw in data.get("dialog_entries", []) or []:
                self._dialog_entries.append(DialogEntry(
                    entry_id=raw.get("entry_id", ""),
                    entry_type=raw.get("type", "thought"),
                    model_id=raw.get("model_id", ""),
                    tier=raw.get("tier", ""),
                    content=raw.get("content", ""),
                    round_num=raw.get("round", 0),
                    timestamp=raw.get("timestamp", time.time()),
                    metadata=raw.get("metadata", {}) or {},
                ))
            self.resume_context = data.get("resume_context")

    def persist(self) -> None:
        """按 (session_id, blackboard_id) 落库黑板快照（失败不阻塞）"""
        try:
            from modules.database.blackboard_repo import save_blackboard
            save_blackboard(self.to_state_dict())
        except Exception:
            pass

    @classmethod
    def load(cls, session_id: str, blackboard_id: str) -> Optional["CognitiveBlackboard"]:
        """按 (session_id, blackboard_id) 恢复黑板快照"""
        try:
            from modules.database.blackboard_repo import load_blackboard
            data = load_blackboard(session_id, blackboard_id)
        except Exception:
            data = None
        if not data:
            return None
        bb = cls(
            session_id=data.get("session_id", session_id),
            turn_id=data.get("turn_id", ""),
            blackboard_id=blackboard_id,
        )
        bb._restore(data)
        return bb
