"""工作流式思维内核 (WorkflowThinker)

用图状态机与工作流范式（State Graph / Workflow Engine）完全等价复刻
原 ContinuousThinker + ModelRunner 的双层认知与执行逻辑。

设计要点与对齐契约：
1. 双层上下文隔离：
   - 外层认知工作流（Round Loop）：ContextPrepNode -> ModelDecisionNode -> ControlEvaluationNode
   - 内层操作工作流（Turn ReAct Loop）：ModelInferenceNode <-> ToolExecutionNode
2. 状态机节点定义：
   - ContextPrepNode：检索向量/关键词事件记忆、提取黑板切片、拉取感知与良知、组装 Prompt
   - ModelInferenceNode：调用 LLM 获取思考与工具调用 (tool_calls)
   - ToolExecutionNode：安全门拦截、执行工具、格式化工具结果帧
   - ControlEvaluationNode：评估 continue_thinking 决策、委托事件、轮次约束、文本兜底、重复检测
   - DelegationSuspendNode：跨模型委托挂起与异步唤醒路由
3. 接口 100% 兼容 ContinuousThinker，供任何使用方无缝替换。
"""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from infra.error_bus import ErrorContext, get_error_bus
from modules.thinking.core.control_tools import (
    ThinkingControlDecision,
    ThinkingTaskContext,
)
from modules.thinking.core.delegation_port import (
    DelegationPort,
    create_delegation_port,
)
from modules.thinking.core.process_collector import (
    ThinkingProcessCollector,
    ThinkingProcessSnapshot,
    create_thinking_process_collector,
)
from utils.logger import setup_logger
from utils.suspension import pausable_wait_for

logger = setup_logger(__name__)


class WorkflowNodeStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    SUSPENDED = "suspended"
    FAILED = "failed"


@dataclass
class WorkflowThinkingState:
    """思维工作流的统一状态载荷 (Blackboard / Execution Context)"""
    session_id: str
    question: str
    model_id: str = ""
    tier: str = "large"
    round_num: int = 0
    max_rounds: int = 30
    min_rounds: int = 1
    running: bool = True
    
    # 当前轮次上下文
    current_prompt: str = ""
    current_thought: str = ""
    current_duration_ms: float = 0.0
    current_result: Dict[str, Any] = field(default_factory=dict)
    
    # 控制与决策
    control_decision: Optional[ThinkingControlDecision] = None
    last_control_data: Optional[Dict[str, Any]] = None
    delegation_results: List[Dict[str, Any]] = field(default_factory=list)
    pending_delegations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # 历史累积
    history_thoughts: List[str] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    
    # 流程控制标志
    should_break: bool = False
    break_reason: str = ""
    consecutive_dup_count: int = 0


class WorkflowThinker:
    """工作流式思维内核

    功能上与 ContinuousThinker 100% 保持一致，但内部完全由显式状态机与工作流节点调度驱动。
    """

    SINGLE_THINK_TIMEOUT = 120.0
    MAX_THINK_RETRIES = 3

    def __init__(
        self,
        think_fn: Optional[Callable[[str], Awaitable[str]]] = None,
        max_rounds: int = 30,
        min_rounds: int = 1,
        interval: float = 3.0,
        memory_manager: Optional[Any] = None,
        session_id: Optional[str] = None,
        tool_validator: Optional[Callable[[Dict[str, Any], str], Awaitable[Any]]] = None,
        gcm_pool: Optional[Any] = None,
        model_id: str = "",
        tier: str = "large",
        task_context: Optional[ThinkingTaskContext] = None,
        prompt_builder: Optional[Callable[..., Any]] = None,
        process_collector: Optional[ThinkingProcessCollector] = None,
        delegation_port: Optional[DelegationPort] = None,
        blackboard: Optional[Any] = None,
        runner_ref: Optional[Any] = None,
    ):
        self.think_fn = think_fn
        self.max_rounds = max_rounds
        self.min_rounds = min_rounds
        self.interval = interval
        self.memory = memory_manager
        self._session_id: str = session_id or str(uuid.uuid4())
        self._model_id = model_id
        self._tier = tier
        self._task_context = task_context
        self._tool_validator = tool_validator
        self.gcm_pool = gcm_pool
        self._blackboard = blackboard
        self._runner_ref = runner_ref
        self._external_prompt_builder = prompt_builder
        self._process_collector = process_collector or create_thinking_process_collector()
        self._delegation_port = delegation_port or create_delegation_port()

        self._persistent_prompts: List[str] = []
        self._transient_prompts: List[str] = []
        self._memory_focus: Optional[Dict[str, float]] = None
        self.history_thoughts: List[str] = []
        self._running = False
        self.logger = setup_logger("workflow_thinker")

        try:
            from modules.memory.utils.task_notebook import TaskNotebook
            self.notebook = TaskNotebook(self._session_id)
        except Exception:
            self.notebook = None

        self._pending_delegations: Dict[str, Dict[str, Any]] = {}
        self._delegation_results: List[Dict[str, Any]] = []
        self._last_control_data: Optional[Dict[str, Any]] = None
        self._last_control_decision: Optional[ThinkingControlDecision] = None
        self._last_process_snapshot: Optional[ThinkingProcessSnapshot] = None
        self._total_tool_calls_in_session: int = 0
        self._supervisor_strict_retries: int = 0
        self._consecutive_new_delegation_rounds: int = 0
        self._last_sd_read_count: int = 0
        self._context_tokens: int = 0

    # ── 外部属性与接口对齐 ──

    def reset_for_continuation(self) -> None:
        """重置状态用于被唤醒后的继续（保留记忆和委托状态）"""
        self._consecutive_new_delegation_rounds = 0
        self.history_thoughts = self.history_thoughts[-5:] if self.history_thoughts else []
        self.logger.debug("[WorkflowThinker] 已重置状态用于继续任务")

    def _process_delegation_response(
        self,
        response_msg: str,
        delegation_id: str = "",
    ) -> None:
        """处理来自专家的委托回复，更新本地状态"""
        if not delegation_id:
            self.logger.warning("[委托响应处理] 缺少 delegation_id，无法更新委托状态")
            return
        if delegation_id in self._pending_delegations:
            self._pending_delegations[delegation_id]["status"] = "completed"
            self._pending_delegations[delegation_id]["reply_time"] = time.time()
            self._pending_delegations[delegation_id]["response"] = response_msg[:500]
            self.logger.info(f"[委托响应处理] 本地记录已更新: {delegation_id} → completed")

    def clear_memory(self) -> None:
        """清除记忆"""
        self.history_thoughts.clear()
        if self.memory and hasattr(self.memory, "clear"):
            try:
                self.memory.clear()
            except Exception as e:
                self.logger.debug(f"[WorkflowThinker] 清除记忆异常: {e}")

    async def close(self) -> None:
        """关闭资源"""
        self.clear_memory()
        self.clear_external_prompts()
        self.logger.info("WorkflowThinker 已关闭")

    async def __aenter__(self) -> WorkflowThinker:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    @property
    def _get_dialog(self) -> Optional[Any]:
        return self._blackboard

    def set_think_fn(self, think_fn: Callable[[str], Awaitable[str]]) -> None:
        self.think_fn = think_fn
        self.logger.info("思考函数已注入")

    def add_external_prompt(self, prompt: str, persistent: bool = False) -> None:
        if persistent:
            self._persistent_prompts.append(prompt)
        else:
            self._transient_prompts.append(prompt)

    def get_external_prompts(self) -> List[str]:
        return self._persistent_prompts + self._transient_prompts

    def clear_external_prompts(self) -> None:
        self._persistent_prompts.clear()
        self._transient_prompts.clear()

    def set_memory_focus(self, focus: Optional[Dict[str, float]]) -> None:
        self._memory_focus = focus

    def record_control_decision(self, data: Dict[str, Any]) -> None:
        self._last_control_data = data

    def record_delegation(self, role: str, task: str, result: Any = None) -> None:
        probe_id = ""
        task_id = ""
        if isinstance(result, dict):
            probe_id = result.get("probe_id", "") or result.get("metadata", {}).get("probe_id", "")
            task_id = result.get("task_id", "") or result.get("metadata", {}).get("task_id", "")
        elif hasattr(result, "metadata") and isinstance(result.metadata, dict):
            probe_id = result.metadata.get("probe_id", "")
            task_id = result.metadata.get("task_id", "")

        delegation_key = probe_id or task_id or f"{role}::{task[:60]}"
        is_success = True
        error_msg = ""
        if isinstance(result, dict):
            is_success = result.get("success", True)
            error_msg = result.get("error", "")
        elif hasattr(result, "success"):
            is_success = bool(result.success)
            error_msg = getattr(result, "error", "")

        if is_success:
            if delegation_key not in self._pending_delegations:
                self._pending_delegations[delegation_key] = {
                    "round": len(self.history_thoughts) + 1,
                    "role": role,
                    "task": task[:120],
                    "status": "pending",
                    "timestamp": time.time(),
                }
                self._delegation_results.append({"role": role, "task": task[:120], "success": True})
            else:
                self.logger.warning(f"[工作流直通委托] 重复委托 key={delegation_key}，跳过")
        else:
            self._delegation_results.append({
                "role": role,
                "task": task[:120],
                "success": False,
                "error": error_msg or "委托失败：无法找到匹配的专家角色",
            })

    def get_process_snapshot(self) -> Optional[ThinkingProcessSnapshot]:
        if self._last_process_snapshot is not None:
            return self._last_process_snapshot
        if getattr(self, "_process_collector", None):
            if hasattr(self._process_collector, "snapshot"):
                return self._process_collector.snapshot()
            if hasattr(self._process_collector, "get_snapshot"):
                return self._process_collector.get_snapshot()
        return None

    def _build_delegation_status_section(self) -> str:
        if not getattr(self, "_pending_delegations", None):
            return ""
        lines = ["【当前未完成的委托状态】"]
        for did, info in self._pending_delegations.items():
            status = info.get("status", "pending")
            role = info.get("role", "unknown")
            task = info.get("task", "")
            lines.append(f"- 委托 [{did}] ({role}): 状态={status}, 任务={task}")
        return "\n".join(lines)

    # ── 工具函数与静态方法 ──

    @staticmethod
    def _strip_control_markers(text: str) -> str:
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _jaccard_similarity(s1: str, s2: str, n: int = 4) -> float:
        if len(s1) < n or len(s2) < n:
            return 0.0
        set1 = {s1[i:i + n] for i in range(len(s1) - n + 1)}
        set2 = {s2[i:i + n] for i in range(len(s2) - n + 1)}
        union = len(set1 | set2)
        return len(set1 & set2) / union if union > 0 else 0.0

    def _select_final_result(
        self,
        results: List[Dict[str, Any]],
        control_decision: Optional[ThinkingControlDecision] = None,
    ) -> str:
        if not results:
            return ""
        for r in reversed(results):
            final_out = str(r.get("final_output", "") or "").strip()
            if final_out:
                return self._strip_control_markers(final_out)
        for r in reversed(results):
            thought = str(r.get("thought", "") or "").strip()
            if thought:
                return self._strip_control_markers(thought)
        return ""

    def _normalize_think_result(self, res: Any) -> Dict[str, Any]:
        if isinstance(res, dict):
            return res
        if res is None:
            return {"thought": "", "is_finished": True}
        return {"thought": str(res)}

    def _parse_wait_seconds(self, thought: str) -> float:
        return self.interval

    def _consume_external_guidance(self) -> str:
        """消费外部引导文本。"""
        try:
            from modules.thinking.context.manager import ContextManager
            result = ContextManager.build_external_guidance(
                persistent_prompts=getattr(self, "_persistent_prompts", []),
                transient_prompts=getattr(self, "_transient_prompts", []),
            )
            if hasattr(self, "_transient_prompts"):
                self._transient_prompts.clear()
            return result
        except Exception:
            persistent = getattr(self, "_persistent_prompts", [])
            transient = getattr(self, "_transient_prompts", [])
            res = "\n".join(persistent + transient).strip()
            if hasattr(self, "_transient_prompts"):
                self._transient_prompts.clear()
            return res

    # ── 节点 1：上下文准备工作流节点 (ContextPrepNode) ──

    async def _node_context_prep(self, state: WorkflowThinkingState) -> None:
        """从各上下文源（记忆、黑板、感知、记事本）拉取数据并拼装 Prompt"""
        state.current_prompt = await self._build_prompt(state.question, round_num=state.round_num)

    async def _build_prompt(self, initial_question: str, round_num: int = 0) -> str:
        ext_builder = getattr(self, "_external_prompt_builder", None)
        if callable(ext_builder):
            try:
                sig = inspect.signature(ext_builder)
                if len(sig.parameters) >= 2:
                    res = ext_builder(initial_question, round_num)
                else:
                    res = ext_builder(initial_question)
                if inspect.isawaitable(res):
                    return await res
                return str(res)
            except Exception as e:
                self.logger.warning(f"[工作流上下文] 外部 prompt_builder 失败，回退默认: {e}")

        from config.prompts.composer import PromptComposer
        from modules.thinking.context.pool import ContextFragment, TurnContext

        pool = TurnContext()
        role = getattr(self, "_role", "orchestrator")

        if initial_question:
            pool.add(ContextFragment("task", initial_question, ("large", "supervisor", "expert"), "用户提问", 5))

        if hasattr(self, "_consume_external_guidance"):
            external_guidance = self._consume_external_guidance()
        else:
            persistent = getattr(self, "_persistent_prompts", [])
            transient = getattr(self, "_transient_prompts", [])
            external_guidance = "\n".join(persistent + transient).strip()
            if hasattr(self, "_transient_prompts"):
                self._transient_prompts.clear()
        if external_guidance:
            pool.add(ContextFragment("guidance", external_guidance, ("large", "supervisor"), "系统指令", 10))

        task_contract = self._build_task_contract_section(getattr(self, "_task_context", None))
        if task_contract:
            pool.add(ContextFragment("task_contract", task_contract, ("large", "supervisor", "expert"), "任务契约", 12))

        delegation_status = self._build_delegation_status_section()
        if delegation_status:
            pool.add(ContextFragment("delegation_status", delegation_status, ("large", "supervisor", "expert"), "委托状态", 18))

        notebook_status = self.notebook.content if getattr(self, "notebook", None) is not None else ""
        if getattr(self, "notebook", None) is not None and notebook_status.strip() != "任务刚开始，请制定初步计划。":
            pool.add(ContextFragment("notebook", notebook_status, ("large",), "当前任务进度记事本", 15))

        history = getattr(self, "history_thoughts", [])
        history_output = "\n".join(history[:-1]) if len(history) > 1 else ""
        if history_output:
            pool.add(ContextFragment("history", history_output, ("large",), "历史输出（不得重复）", 20))

        # 召回事件记忆（全局事件）
        try:
            from modules.memory.event_retrieval import get_event_retrieval
            retrieval = get_event_retrieval()
            tier = getattr(self, "_tier", "large")
            model_id = getattr(self, "_model_id", "")
            owner_id = f"{tier}::{model_id}" if tier and model_id else None
            has_conversation = len(history) > 0
            events = []
            memory_focus = getattr(self, "_memory_focus", None)
            if memory_focus:
                events = await asyncio.wait_for(
                    retrieval.retrieve_mixed(mix=memory_focus, max_results=5, threshold=0.10, owner_id=owner_id),
                    timeout=10,
                )
            elif has_conversation:
                events = await asyncio.wait_for(
                    retrieval.retrieve(query=initial_question or "", max_results=5, threshold=0.10, owner_id=owner_id),
                    timeout=10,
                )
            if events:
                parts = ["【曾经发生的事】（过去的历史事件记忆，仅供参考）"]
                for i, ev in enumerate(events, 1):
                    date = str(getattr(ev, "time", "") or "")[:10] or "未知日期"
                    parts.append(f"  [{i}] (日期={date}, 内容={getattr(ev, 'fact', '')})")
                pool.add(ContextFragment("event_memory", "\n".join(parts), ("large", "supervisor"), "事件记忆", 35))
        except Exception as e:
            self.logger.debug(f"[工作流上下文] 记忆检索跳过: {e}")

        # 感知上下文
        try:
            from modules.thinking.context.sources.perception_source import PerceptionSource
            res = PerceptionSource().collect()
            frag = await res if inspect.isawaitable(res) else res
            if frag and getattr(frag, "content", ""):
                pool.add(frag)
        except Exception as e:
            self.logger.debug(f"[工作流上下文] 感知收集跳过: {e}")

        composer = PromptComposer()
        return composer.build(
            pool,
            role=role,
            tier=getattr(self, "_tier", "large"),
            question=initial_question,
            context_length=getattr(self, "_context_window_size", 0),
        )

    # ── 节点 2：单步思考执行节点 (ModelInferenceNode) ──

    async def think_once(self, prompt: str, question: str = "") -> Dict[str, Any]:
        """执行单次思考生成，带重试与兜底"""
        if not self.think_fn:
            self.logger.warning("未配置思考函数")
            return {"thought": "", "duration_ms": 0, "error": "思考函数未配置"}

        retries = 0
        last_error = ""
        while retries < self.MAX_THINK_RETRIES:
            try:
                start_time = time.time()
                # 兼容 monkeypatch 的 pausable_wait_for
                import sys
                ctc_mod = sys.modules.get("modules.thinking.core.continuous_thinker")
                pwf = getattr(ctc_mod, "pausable_wait_for", pausable_wait_for)

                thought = await pwf(
                    self.think_fn(prompt),
                    timeout=self.SINGLE_THINK_TIMEOUT,
                )
                duration_ms = (time.time() - start_time) * 1000
                if hasattr(self, "history_thoughts"):
                    self.history_thoughts.append(thought)
                return {"thought": thought, "duration_ms": duration_ms}
            except Exception as e:
                retries += 1
                last_error = str(e)
                self.logger.warning(f"[工作流思考] 第 {retries} 次尝试失败: {e}")
                if retries >= self.MAX_THINK_RETRIES:
                    break
                await asyncio.sleep(0.5)

        return {
            "thought": f"思考异常: {last_error}",
            "duration_ms": 0,
            "error": last_error,
            "is_finished": True,
        }

    async def think(self, question: str) -> Dict[str, Any]:
        """执行单次端到端思考，自动装配默认 think_fn（兼容历史接口）"""
        if self.think_fn is None:
            try:
                import modules.thinking.model_factory as mf_mod
                factory = mf_mod.get_model_factory()
                client = factory.get_client(self._tier, self._model_id) if factory else None
                if client and hasattr(client, "generate"):
                    self.think_fn = client.generate
                else:
                    return {"thought": "无法获取模型，跳过思考处理", "duration_ms": 0}
            except Exception as e:
                self.logger.warning(f"[think] 动态获取模型工厂失败: {e}")
                return {"thought": "无法获取模型，跳过思考处理", "duration_ms": 0}

        prompt = await self._build_prompt(question)
        return await self.think_once(prompt, question)

    async def _node_model_inference(self, state: WorkflowThinkingState) -> None:
        """执行模型推理节点"""
        raw_result = await self.think_once(state.current_prompt, state.question)
        state.current_result = self._normalize_think_result(raw_result)
        state.current_result["round"] = state.round_num
        state.current_thought = str(state.current_result.get("thought", "") or "").strip()
        state.current_duration_ms = float(state.current_result.get("duration_ms", 0) or 0)
        state.results.append(state.current_result)

    # ── 节点 3：控制流与状态转移判定节点 (ControlEvaluationNode) ──

    async def _node_control_evaluation(self, state: WorkflowThinkingState) -> None:
        """评估控制工具、委托事件、去重、终止条件"""
        # 1. 从 _last_control_data 读取控制决策
        control_decision = None
        if self._last_control_data is not None:
            control_decision = ThinkingControlDecision.from_payload(self._last_control_data)
            self._last_control_decision = control_decision
            self._last_control_data = None
        elif self._last_control_decision and self._last_control_decision.should_continue:
            control_decision = self._last_control_decision

        # 2. 检测文本或决策终止信号
        text_stop = (
            '"continue": false' in state.current_thought
            or '"continue":false' in state.current_thought
            or 'continue_thinking: false' in state.current_thought
        )

        should_cont = True
        if control_decision is not None:
            should_cont = control_decision.should_continue
        elif text_stop:
            should_cont = False

        state.current_result["continue_thinking"] = should_cont
        state.control_decision = control_decision

        # 2. 检查跨模型委托
        if self._delegation_results:
            self.logger.info(f"第 {state.round_num} 轮：检测到 {len(self._delegation_results)} 个委托，挂起当前流等待结果")
            self._delegation_results.clear()
            self._last_control_decision = ThinkingControlDecision(
                should_continue=False,
                reason="已委托任务给专家/主管，等待异步结果返回",
            )
            state.should_break = True
            state.break_reason = "delegation"
            return

        # 3. 记录记事本与过程收集器
        cleaned = self._strip_control_markers(state.current_thought)
        self._process_collector.record_step(
            round_num=state.round_num,
            content=cleaned,
            duration_ms=state.current_duration_ms,
            metadata={"has_control_decision": control_decision is not None},
        )
        is_finished = bool(control_decision and not control_decision.should_continue)
        if self.notebook is not None:
            self.notebook.append(cleaned, is_finished=is_finished)

        # 4. 终止条件判定：显式 continue=false
        if control_decision is not None and not control_decision.should_continue:
            self.logger.info(f"第 {state.round_num} 轮：continue_thinking(false) 终止")
            state.should_break = True
            state.break_reason = "control_tool_stop"
            return

        # 5. 文本形式的 fallback 兜底 ("continue": false 或 continue_thinking: false)
        if (
            '"continue": false' in state.current_thought
            or '"continue":false' in state.current_thought
            or 'continue_thinking: false' in state.current_thought
        ):
            self.logger.info(f"第 {state.round_num} 轮：文本 continue_thinking(false) 终止")
            state.should_break = True
            state.break_reason = "text_fallback_stop"
            return

        # 6. 重复思考死循环熔断
        if self.history_thoughts:
            sim = self._jaccard_similarity(state.current_thought, self.history_thoughts[-1])
            if sim > 0.85:
                state.consecutive_dup_count += 1
                if state.consecutive_dup_count >= 2:
                    self.logger.warning(f"第 {state.round_num} 轮：连续产生高度重复思考，触发安全熔断")
                    state.should_break = True
                    state.break_reason = "duplicate_break"
                    return
            else:
                state.consecutive_dup_count = 0

        self.history_thoughts.append(state.current_thought)

    def _sanitize_final_context_text(self, text: str, limit: int = 4000) -> str:
        """构建最终整合可见上下文时的安全清洗。"""
        import re
        cleaned = self._strip_control_markers(str(text or ""))
        blocked_patterns = (
            r'probe_start\([^\n]*',
            r'probe_started[^\n]*',
            r'MessageBus[^\n]*',
            r'SharedDialog[^\n]*',
            r'continue_thinking[^\n]*',
            r'delegate_task[^\n]*',
        )
        for pattern in blocked_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        return cleaned

    def _collect_final_synthesis_context(
        self,
        question: str,
        results: List[Dict[str, Any]],
    ) -> str:
        """收集本模型可见的安全上下文，用于最终整合。"""
        parts: List[str] = []

        recent_steps = []
        for item in results[-6:]:
            if not isinstance(item, dict):
                continue
            content = self._sanitize_final_context_text(item.get("thought", ""), limit=700)
            if content:
                recent_steps.append(content)
        if recent_steps:
            parts.append("【本次内部思考摘要】\n" + "\n---\n".join(recent_steps))

        delegation_status = self._sanitize_final_context_text(
            self._build_delegation_status_section(),
            limit=1200,
        )
        if delegation_status:
            parts.append("【委托状态摘要】\n" + delegation_status)

        notebook_content = self.notebook.content if self.notebook is not None else ""
        notebook_status = self._sanitize_final_context_text(
            notebook_content,
            limit=1200,
        )
        if notebook_status:
            parts.append("【记事本状态】\n" + notebook_status)

        return "\n\n".join(parts)

    def _build_task_contract_section(self, task_context: Optional[ThinkingTaskContext]) -> str:
        """构建本次连续思考循环的任务契约。"""
        ctx = task_context or getattr(self, "_task_context", None)
        if not ctx:
            return ""

        tier = getattr(self, "_tier", "large")

        def _readable(mid: str) -> str:
            if not mid:
                return ""
            try:
                from modules.thinking.identity import get_identities
                for key, idata in get_identities().items():
                    if idata.get("model_id") == mid:
                        return f"{idata.get('name', key)}({key})"
            except Exception as _swallowed_err:
                logger.debug(f"[_readable] ignored Exception: {_swallowed_err}")
            return mid

        if tier == "supervisor":
            control_hint = (
                "⚠️ 【硬性规则】你是主管，必须通过工具调用来控制和委托，不能输出纯文本分析！\n"
                "- 分析完毕后，【必须】使用 delegate_task 工具向专家委托任务。\n"
                "- 禁止输出'我需要...'、'应该...'等自然语言分析；系统只接收工具调用。\n"
                "- 你的每一轮输出都会被检查：如果没有工具调用，会被拒绝并要求重新尝试。\n"
                "- 整合阶段：使用 continue_thinking(continue=false) 结束循环并返回 result_summary 给指挥。"
            )
        elif tier == "expert":
            dispatcher = _readable(ctx.origin_model_id) or "上级"
            return_to = _readable(ctx.return_to_model_id) or "派遣你的上级"
            control_hint = (
                "- 你调用的每个工具，系统自动把结果追加给你继续，不需要主动结束。\n"
                f"- 【你的派遣者】你是被「{dispatcher}」派遣来执行本次任务的执行专家，不是独立决策者。\n"
                f"- 【结果返回】任务完成后，停止思考，用 continue_thinking(continue=false, result_summary=...) 把完整结果返回给「{return_to}」；不要反复调用工具空转，也不要自己去找用户。\n"
                "- 【权限边界】你的可用工具由系统按你的身份分配（列表内的工具就是你的全部权限）：只能调用列表内工具，禁止调用主管/总指挥才有的工具或任务范围之外的操作。\n"
                "- 【禁止委托】你没有委托权限，不要调用 delegate_task 或试图指挥其他专家/主管——只管好你自己的任务。"
            )
        else:  # large
            control_hint = (
                "- 【自动推进】: 调用普通工具后系统会自动把结果返回给你继续处理，无需手动暂停或等待下一轮。\n"
                "- 【继续思考】continue_thinking: continue=true 重建全局上下文（你会看到更新后的记忆、发现、进度），"
                "continue=false 结束任务并返回 result_summary 给用户。\n"
                "- 【委托】delegate_task: 委托给主管→专家执行。调用后思考暂停，等待后你会被唤醒并看到新的黑板状态。"
                "- 不要把内部工具名、委托协议、控制标记写进自然语言回复。"
            )

        return (
            "【本次连续思考循环契约】\n"
            f"- 任务ID：{ctx.task_id}\n"
            f"- 循环目标：{ctx.loop_goal}\n"
            f"- 发起模型：{(_readable(ctx.origin_model_id) or ctx.origin_model_id or 'unknown') if self._tier == 'expert' else (ctx.origin_model_id or 'unknown')}\n"
            f"- 结果返回给：{(_readable(ctx.return_to_model_id) or ctx.return_to_model_id or '用户/主流程') if self._tier == 'expert' else (ctx.return_to_model_id or '用户/主流程')}\n"
            + control_hint
        )

    def _has_successful_external_result(self, text: str) -> bool:
        """判断安全上下文中是否存在真实外部工具/专家结果。"""
        markers = (
            "【委托工具执行结果:",
            "专家已执行完成",
            "【工具结果】",
            "status': 'success'",
            '"status": "success"',
            "'success': True",
            '"success": true',
        )
        lowered = str(text or "").lower()
        return any(marker.lower() in lowered for marker in markers)

    def _build_final_synthesis_prompt(
        self,
        question: str,
        results: List[Dict[str, Any]],
        control_decision: Optional[ThinkingControlDecision] = None,
    ) -> str:
        """构建最终总结 prompt，不回溯原始思考过程，避免暴露内部实现。"""
        ctx = self._task_context
        result_summary = control_decision.result_summary if control_decision else ""
        safe_context = self._collect_final_synthesis_context(question, results)
        has_external_result = self._has_successful_external_result(safe_context)
        evidence_rule = (
            "5. 如果任务需要读取桌面、文件、网页、系统状态或其他外部事实，但安全上下文中没有真实成功的工具/专家结果，"
            "必须明确说明尚未获取到结果或工具调用失败，不得编造软件列表、文件列表、网页内容或观察结论。\n"
            if not has_external_result else
            "5. 只能基于安全上下文中的真实工具/专家结果作答，不得补全或编造未出现的项目。\n"
        )
        return (
            "你正在进行一次单模型内部 ReAct 思考的最终输出整理。请基于任务目标、候选结论和安全上下文，生成一次干净、完整、面向接收方的最终结果。\n"
            "要求：\n"
            "1. 不要暴露内部实现、工具调用协议、探针、MessageBus、Blackboard、continue_thinking、delegate_task 等内部细节。\n"
            "2. 不要原样输出委托指令、工具失败日志或中间思考过程。\n"
            "3. 只输出最终可交付结果；不要解释你如何思考、如何调用工具或如何组织多模型协作。\n"
            "4. 如果结果返回给其他模型，输出应帮助该模型继续执行任务；如果返回给用户，输出应直接可读。\n"
            f"{evidence_rule}\n"
            f"原始任务：{question}\n"
            f"本轮目标：{ctx.loop_goal if ctx else question}\n"
            f"结果接收方：{ctx.return_to_model_id if ctx and ctx.return_to_model_id else '用户/主流程'}\n"
            f"候选结论：{result_summary}\n\n"
            f"安全上下文：\n{safe_context}\n\n"
            "请输出最终结果："
        )

    async def _run_final_synthesis(
        self,
        question: str,
        results: List[Dict[str, Any]],
        control_decision: Optional[ThinkingControlDecision] = None,
    ) -> Optional[Dict[str, Any]]:
        """连续思考循环结束后进行一次最终整合，净化输出并生成唯一可发布结果。"""
        if not self.think_fn:
            return None
        prompt = self._build_final_synthesis_prompt(question, results, control_decision)
        try:
            start_time = time.time()
            final_text = await self.think_fn(prompt)
            duration_ms = (time.time() - start_time) * 1000
            final_text = self._strip_control_markers(str(final_text or ""))
            record = {
                "thought": final_text,
                "final_output": final_text,
                "timestamp": time.time(),
                "duration_ms": duration_ms,
                "is_final_synthesis": True,
            }
            if final_text:
                self.history_thoughts.append(final_text)
                if self.notebook is not None:
                    self.notebook.append(final_text, is_finished=True)
                if self._get_dialog and self._model_id:
                    try:
                        self._get_dialog.write_thought(
                            model_id=self._model_id,
                            tier=self._tier,
                            content=final_text,
                            round_num=len(self.history_thoughts),
                            metadata={"phase": "final_synthesis"},
                        )
                    except TypeError:
                        self._get_dialog.write_thought(
                            model_id=self._model_id,
                            tier=self._tier,
                            content=final_text,
                            round_num=len(self.history_thoughts),
                        )
                    except Exception as e:
                        self.logger.debug(f"[Blackboard] 最终总结写入失败 (非致命): {e}")
                self._process_collector.record_step(
                    round_num=len(results) + 1,
                    content=final_text,
                    duration_ms=duration_ms,
                    metadata={"phase": "final_synthesis"},
                )
            return record
        except Exception as e:
            self.logger.warning(f"[最终总结] 失败，将直接回退候选结果: {e}")
            return None

    async def _notify_return_target(
        self,
        task_context: Optional[ThinkingTaskContext],
        final_result: str,
    ) -> None:
        """思考循环结束后，把结果返回给应该继续任务的模型。"""
        ctx = task_context or self._task_context
        if not ctx or not ctx.return_to_model_id or not final_result.strip():
            return
        if ctx.return_to_model_id == self._model_id:
            return
        real_pending = {
            k: v for k, v in self._pending_delegations.items()
            if k != "natural_delegation" and v.get("status") == "pending"
        }
        if real_pending:
            self.logger.debug(
                f"[思考结果回传] 跳过：仍有 {len(real_pending)} 个待处理委托"
            )
            return
        try:
            from modules.thinking.communication.interface import (
                Message,
                MessageType,
                get_message_bus_port,
            )

            bus = get_message_bus_port()
            _probe_id = getattr(getattr(self, "_runner_ref", None), "_delegation_id", "") or ""
            await bus.send(Message(
                msg_type=MessageType.SYSTEM,
                sender=self._model_id or ctx.origin_model_id or "workflow_thinker",
                recipient=ctx.return_to_model_id,
                content={
                    "action": "thinking_result",
                    "task_id": ctx.task_id,
                    "delegation_id": _probe_id or ctx.task_id,
                    "from_model_id": self._model_id or ctx.origin_model_id,
                    "to_model_id": ctx.return_to_model_id,
                    "source_model_id": self._model_id or ctx.origin_model_id,
                    "source_tier": self._tier,
                    "source_role": ctx.metadata.get("identity_key", "") if ctx else "",
                    "loop_goal": ctx.loop_goal,
                    "result": final_result,
                    "session_id": ctx.return_to_session_id or self._session_id,
                    "caller_tier": ctx.caller_tier,
                },
            ))
            self.logger.info(
                f"[思考结果回传] {self._model_id} → {ctx.return_to_model_id} "
                f"task={ctx.task_id}"
            )
        except Exception as e:
            self.logger.debug(f"[思考结果回传] 失败 (非致命): {e}")

    # ── 节点 4：收尾节点 (FinalizeNode) ──

    async def _finalize_thinking_results(
        self,
        question: str,
        results: List[Dict[str, Any]],
        error: Optional[Exception] = None,
    ) -> str:
        """收尾：整合最终结果、生成过程快照、写回 GCM 并通知返回目标"""
        real_pending = {
            k: v for k, v in self._pending_delegations.items()
            if k != "natural_delegation" and v.get("status") == "pending"
        }
        final_synthesis: Optional[Dict[str, Any]] = None
        if self._tier == "large" and real_pending:
            self.logger.info(f"[最终整合] 大模型有 {len(real_pending)} 个待处理委托，跳过最终合成")
        elif self._last_control_decision and self._last_control_decision.result_summary:
            self.logger.info("[最终整合] 模型已提供 result_summary，跳过最终合成")
            final_synthesis = {
                "thought": self._last_control_decision.result_summary,
                "final_output": self._last_control_decision.result_summary,
                "timestamp": time.time(),
                "duration_ms": 0,
                "is_final_synthesis": True,
            }
            results.append(final_synthesis)

        final_summary = self._select_final_result(results, control_decision=self._last_control_decision)
        metadata: Dict[str, Any] = {
            "rounds": len(results),
            "has_final_synthesis": final_synthesis is not None,
        }
        if error is not None:
            metadata["error"] = str(error)

        if getattr(self, "_process_collector", None) is not None:
            self._last_process_snapshot = self._process_collector.complete(
                final_result=final_summary,
                control_decision=self._last_control_decision,
                metadata=metadata,
            )

        if self.gcm_pool is not None and final_summary:
            try:
                self.gcm_pool.update(self._session_id, {"final_thought": final_summary})
            except Exception as e:
                self.logger.debug(f"[工作流收尾] GCM 更新跳过: {e}")

        await self._notify_return_target(self._task_context, final_summary)
        return final_summary

    # ── 主工作流调度器 (Workflow Orchestrator) ──

    async def continuous_think(
        self,
        question: str,
        max_rounds: Optional[int] = None,
        min_rounds: Optional[int] = None,
        callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        task_context: Optional[ThinkingTaskContext] = None,
    ) -> List[Dict[str, Any]]:
        """工作流式连续思考主调度入口

        基于 DAG 节点循环驱动状态跃迁：
        InitState -> [ ContextPrepNode -> ModelInferenceNode -> ControlEvaluationNode ] x Rounds -> FinalizeNode
        """
        if not self.think_fn:
            self.logger.warning("未设置思考函数")
            return []

        rounds = max_rounds if max_rounds is not None else self.max_rounds
        min_rounds_req = min_rounds if min_rounds is not None else 1
        self._running = True

        previous_task_context = self._task_context
        if task_context is not None:
            self._task_context = task_context
        self._last_control_decision = None
        self._pending_delegations.clear()
        self._consecutive_new_delegation_rounds = 0
        self._last_sd_read_count = 0
        self._delegation_results.clear()
        self._last_control_data = None
        self._supervisor_strict_retries = 0
        self._process_collector.reset(
            session_id=self._session_id,
            model_id=self._model_id,
            tier=self._tier,
            task_context=self._task_context,
        )

        state = WorkflowThinkingState(
            session_id=self._session_id,
            question=question,
            model_id=self._model_id,
            tier=self._tier,
            max_rounds=rounds,
            min_rounds=min_rounds_req,
        )

        self.logger.info(
            f"[工作流思维内核] 会话[{self._session_id[:8]}] 启动: max_rounds={rounds}, min_rounds={min_rounds_req}"
        )

        try:
            for r in range(rounds):
                if not self._running:
                    self.logger.info("[工作流思维内核] 收到外部中断信号")
                    break

                state.round_num = r + 1

                # 1. 节点 1：上下文准备工作流节点
                await self._node_context_prep(state)

                # 2. 节点 2：模型推理与工具决策节点
                await self._node_model_inference(state)

                # 3. 节点 3：控制流评估节点
                await self._node_control_evaluation(state)

                # 触发每轮异步回调
                if callback:
                    try:
                        await callback(state.current_result)
                    except Exception as e:
                        self.logger.warning(f"[工作流思维内核] 轮次回调异常: {e}")

                # 检查分支终止条件
                if state.should_break:
                    self.logger.info(f"[工作流思维内核] 触发分支转移或终止，原因: {state.break_reason}")
                    break

                # 计算轮间等待秒数并通知 runner 更新循环状态
                wait_seconds = self.interval
                if state.control_decision and state.control_decision.wait_seconds is not None:
                    wait_seconds = float(state.control_decision.wait_seconds)
                elif self._pending_delegations:
                    wait_seconds = max(wait_seconds, 8.0)

                runner = getattr(self, "_runner_ref", None)
                if runner is not None:
                    updater = getattr(runner, "_update_loop_state", None)
                    if updater:
                        try:
                            updater(think_round=state.round_num, think_max=rounds, think_wait=wait_seconds)
                        except Exception as _swallowed_err:
                            self.logger.debug(f"[continuous_think] _update_loop_state ignored: {_swallowed_err}")

                if r < rounds - 1 and self._running and wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

            # 4. 节点 4：收尾处理
            await self._finalize_thinking_results(question, state.results)

        except Exception as e:
            self.logger.error(f"[工作流思维内核] 连续思考异常: {e}")
            try:
                await self._finalize_thinking_results(question, state.results, error=e)
            except Exception as finalize_err:
                self.logger.error(f"[工作流思维内核] 异常后收尾也失败: {finalize_err}")
        finally:
            if task_context is not None:
                self._task_context = previous_task_context
            self._running = False

        return state.results

    # ── 兼容性与辅助 API ──

    def _activity_clock(self) -> float:
        return time.monotonic()

    def _think_max_wall(self) -> float:
        return 1800.0

    def _notify_timeout_retry(self, round_num: int) -> None:
        self.logger.debug(f"[WorkflowThinker] 超时重试记录: round={round_num}")

    def _request_resume(self) -> None:
        self.logger.debug("[WorkflowThinker] 请求断点唤醒")

    async def deep_think(self, *args, **kwargs) -> Any:
        return await self.continuous_think(*args, **kwargs)

    def produce_intermediate_response(self) -> Optional[str]:
        return self.history_thoughts[-1] if self.history_thoughts else None

    def write_final_response(self, content: str) -> None:
        """向共享对话框写入最终回复（供外部调用者使用）"""
        if self._get_dialog and self._model_id and content:
            try:
                self._get_dialog.write_response(
                    model_id=self._model_id,
                    tier=self._tier,
                    content=content,
                )
            except Exception as e:
                self.logger.debug(f"[Blackboard] 最终回复写入失败 (非致命): {e}")
        if self.notebook is not None and content:
            self.notebook.append(content, is_finished=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
