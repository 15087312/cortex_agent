"""
多模型编排器

直接执行：安全验证 → 记忆上下文 → 专家引导(情绪+价值观+安全) →
多模型思考(Multi-Model Thinking) → 输出审查 → 记忆存储

模型不直接调用模型 → 模型调用工具 → 工具操纵探针 → 探针激活模型
"""
import os
import time
import uuid
import asyncio
import threading
from typing import List, Dict, Optional, Any, Callable

from modules.thinking.ports import (
    ActivityNotifierPort,
    GuidancePort,
    OutputReviewPort,
    SecurityPort,
)

from utils.logger import setup_logger

logger = setup_logger("multi_model_orchestrator")

# ── 全局会话注册表（供管理 API 和 probe_tools 查询）──
_session_registry: Dict[str, Dict[str, Any]] = {}
_session_registry_lock = threading.Lock()


def get_active_sessions() -> List[Dict[str, Any]]:
    """获取所有活跃会话信息"""
    with _session_registry_lock:
        return list(_session_registry.values())


class MultiModelOrchestrator:
    """多模型编排器"""

    def __init__(
        self,
        gcm_pool=None,
        activity_notifier: Optional[ActivityNotifierPort] = None,
        security: Optional[SecurityPort] = None,
        guidance_service: Optional[GuidancePort] = None,
        output_reviewer: Optional[OutputReviewPort] = None,
    ):
        self._gcm_pool = gcm_pool
        self._activity_notifier = activity_notifier
        self._security = security
        self._guidance_service = guidance_service
        self._output_reviewer = output_reviewer
        # S7: WebSocket 消息队列（per-session 串行处理）
        self._request_queues: Dict[str, asyncio.Queue] = {}
        self._queue_consumers: Dict[str, asyncio.Task] = {}

    def _get_activity_notifier(self) -> ActivityNotifierPort:
        if self._activity_notifier is None:
            from modules.thinking.adapters import DifferenceDetectorActivityNotifier

            self._activity_notifier = DifferenceDetectorActivityNotifier()
        return self._activity_notifier

    def _get_security(self) -> SecurityPort:
        if self._security is None:
            from modules.thinking.adapters import SecurityApiAdapter

            self._security = SecurityApiAdapter()
        return self._security

    def _get_guidance_service(self) -> GuidancePort:
        if self._guidance_service is None:
            from modules.thinking.adapters import PreGenExpertGuidanceAdapter

            self._guidance_service = PreGenExpertGuidanceAdapter()
        return self._guidance_service

    def _get_output_reviewer(self) -> OutputReviewPort:
        if self._output_reviewer is None:
            from modules.thinking.adapters import OutputSystemReviewAdapter

            self._output_reviewer = OutputSystemReviewAdapter()
        return self._output_reviewer

    # ------------------------------------------------------------------
    # S7: WebSocket 消息队列 (per-session 串行处理)
    # ------------------------------------------------------------------

    def _get_or_create_queue(self, session_id: str) -> asyncio.Queue:
        """获取或创建会话的请求队列，并启动消费者（若未运行）"""
        if session_id not in self._request_queues:
            self._request_queues[session_id] = asyncio.Queue(maxsize=20)
            # 启动该 session 的串行消费者
            consumer_task = asyncio.ensure_future(self._session_consumer(session_id))
            self._queue_consumers[session_id] = consumer_task
            logger.info(f"[Orchestrator] 启动会话消费者: {session_id[:12]}")
        return self._request_queues[session_id]

    async def _session_consumer(self, session_id: str) -> None:
        """串行处理队列中的请求，一次只处理一条，防止并发导致消息丢失"""
        queue = self._request_queues[session_id]
        idle_timeout = 300  # 5 分钟无请求则关闭消费者

        while True:
            try:
                # 等待队列中的下一个请求，超时则退出消费者
                request_data = await asyncio.wait_for(queue.get(), timeout=idle_timeout)

                if request_data is None:
                    # 收到关闭信号
                    logger.info(f"[Orchestrator] 会话消费者关闭: {session_id[:12]}")
                    break

                user_input, kwargs, result_queue = request_data

                try:
                    # 异步调用 process()，获取结果
                    result = await self.process(user_input, session_id=session_id, **kwargs)
                    await result_queue.put(("success", result))
                except Exception as e:
                    logger.error(f"[Orchestrator] 会话处理异常: {e}")
                    await result_queue.put(("error", str(e)))
                finally:
                    queue.task_done()

            except asyncio.TimeoutError:
                # 队列空闲超时，退出消费者（下次请求时重建）
                logger.info(f"[Orchestrator] 会话消费者空闲超时，关闭: {session_id[:12]}")
                self._request_queues.pop(session_id, None)
                self._queue_consumers.pop(session_id, None)
                break
            except Exception as e:
                logger.error(f"[Orchestrator] 会话消费者异常: {e}")
                self._request_queues.pop(session_id, None)
                self._queue_consumers.pop(session_id, None)
                break

    async def process_async(
        self,
        user_input: str,
        context: List[Dict] = None,
        short_term_memory: List[str] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        session_id: Optional[str] = None,
    ) -> Dict:
        """异步处理入口 — 通过队列确保同一会话的请求串行处理（防止 WebSocket 消息丢失）

        Returns:
            与 process() 相同的返回格式
        """
        if not session_id:
            session_id = f"session_{uuid.uuid4().hex[:12]}"

        queue = self._get_or_create_queue(session_id)
        result_queue: asyncio.Queue = asyncio.Queue()

        # 将请求入队
        kwargs = {
            "context": context,
            "short_term_memory": short_term_memory,
            "event_callback": event_callback,
        }
        await queue.put((user_input, kwargs, result_queue))

        # 等待处理结果（超时 300 秒）
        try:
            result_type, result_data = await asyncio.wait_for(
                result_queue.get(),
                timeout=300.0
            )
        except asyncio.TimeoutError:
            logger.error(
                f"[Orchestrator] 会话处理超时 (300s): {session_id[:12]}, "
                f"consumer 可能异常"
            )
            raise RuntimeError(
                f"处理超时：会话可能已异常退出"
            )

        if result_type == "success":
            return result_data
        else:
            raise RuntimeError(f"处理失败: {result_data}")

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def process(
        self,
        user_input: str,
        context: List[Dict] = None,
        short_term_memory: List[str] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        session_id: Optional[str] = None,
    ) -> Dict:
        """
        处理用户输入 — 主入口（纯异步）

        Returns:
            调度结果 dict:
            {response, focus, active_modules, sleep_modules, degraded,
             module_results, decisions, resource_status, security_passed,
             elapsed_ms, trace_id}
        """
        if context is None:
            context = []
        if short_term_memory is None:
            short_term_memory = []

        # S5 修复：确保 session_id 非空
        if not session_id:
            session_id = f"session_{uuid.uuid4().hex[:12]}"
            logger.info(f"[编排器] 自动生成 session_id: {session_id}")

        start_time = time.time()
        trace_id = str(uuid.uuid4())
        # 透传事件回调（后续被 ContinuousThinker 使用）
        logger.info(f"[编排器] 接收输入: {user_input}...")

        # 通知差异检测器有活动
        try:
            self._get_activity_notifier().notify_activity()
        except Exception as e:
            logger.debug(f"[活动通知] 非致命错误: {e}")

        # 重置主动搭话冷却（用户正在说话，不需要搭话）
        try:
            from modules.perception.setup import get_perception_system
            ps = get_perception_system()
            if ps.proactive_trigger:
                ps.proactive_trigger.reset_cooldown()
        except Exception as e:
            logger.debug(f"[主动搭话] 重置冷却失败 (非致命): {e}")

        # ---- 1. 安全验证 ----
        security_passed, security_error = await self._validate_security(user_input)
        if not security_passed:
            logger.warning(f"[安全拦截] {security_error}")
            return self._build_security_error(security_error, start_time)

        # ---- 2. 设置执行模式 ----
        try:
            from modules.thinking.context.controller import get_context_controller
            from config.settings import settings as _cfg
            get_context_controller().set_mode(_cfg.effective_execution_mode)
        except Exception as e:
            logger.warning(f"Orchestrator 设置执行模式失败: {e}")

        # ---- 3. 专家引导 (情绪 + 价值观) ----
        # 由激活的 Skill 决定是否运行，目前默认始终运行
        expert_guidance = await self._run_expert_pipeline(
            user_input
        )

        # ---- 3.5 技能匹配：基于关键词自动匹配用户输入与技能标题 ----
        skill_id = self._match_skill(user_input)
        if skill_id:
            logger.info(f"[编排器] 自动匹配技能: {skill_id}")

        # ---- 4. 多模型思考 (核心) ----
        thinking_result = await self._execute_multi_model_thinking(
            user_input=user_input,
            session_id=session_id,
            expert_guidance=expert_guidance,
            event_callback=event_callback,
            skill_id=skill_id,
            context=context,  # 传递对话历史用于短期记忆
        )

        raw_response = thinking_result.get("response", "")
        thinking_history = thinking_result.get("thinking_history", [])
        thinking_turns = thinking_result.get("thinking_turns", 0)
        probe_signals = thinking_result.get("probe_signals", [])
        blackboard = thinking_result.get("blackboard")

        # ---- 5. 输出审查 (专家系统) ----
        final_response = await self._review_output(raw_response, user_input, expert_guidance, blackboard)

        # ---- 6. 价值观演化 (fire-and-forget, 不阻塞主流程) ----
        asyncio.create_task(self._maybe_evolve_values(user_input, final_response))

        elapsed_ms = (time.time() - start_time) * 1000

        # ---- 组装返回 ----
        return {
            "response": final_response,
            "focus": "multi_model",
            "active_modules": ["thinking"],
            "sleep_modules": [],
            "degraded": False,
            "module_results": [
                {
                    "module": "thinking",
                    "success": True,
                    "output": {
                        "response": raw_response,
                        "thinking_history": thinking_history,
                        "thinking_turns": thinking_turns,
                        "probe_signals": probe_signals,
                    },
                    "latency_ms": elapsed_ms,
                }
            ],
            "decisions": {
                "priority_weights": {},
                "related_memory": [],
                "context_related": [],
                "probe_signals": probe_signals,
            },
            "resource_status": {},
            "security_passed": True,
            "elapsed_ms": elapsed_ms,
            "trace_id": trace_id,
        }

    # ------------------------------------------------------------------
    # 1. 安全验证
    # ------------------------------------------------------------------

    async def _validate_security(self, user_input: str):
        return self._get_security().validate_input(user_input)

    @staticmethod
    def _build_security_error(error: str, start_time: float) -> dict:
        return {
            "response": f"[安全拦截] {error}",
            "focus": "security_blocked",
            "active_modules": [],
            "sleep_modules": [],
            "degraded": False,
            "module_results": [],
            "decisions": {"priority_weights": {}, "related_memory": [], "context_related": []},
            "resource_status": {},
            "security_passed": False,
            "elapsed_ms": (time.time() - start_time) * 1000,
        }

    # ------------------------------------------------------------------
    # 3. 专家引导
    # ------------------------------------------------------------------

    async def _run_expert_pipeline(self, user_input: str) -> dict:
        return await self._get_guidance_service().run(user_input)

    # ------------------------------------------------------------------
    # 3.5 技能匹配
    # ------------------------------------------------------------------

    def _match_skill(self, user_input: str) -> str:
        """根据用户输入自动匹配技能，返回 skill_id 或空字符串"""
        try:
            from modules.thinking.skills import skill_manager
            skill = skill_manager.match_skill(user_input)
            if skill:
                logger.info(f"[编排器] 技能匹配: {skill.id} ({skill.name})")
                return skill.id
        except Exception as e:
            logger.debug(f"[编排器] 技能匹配失败 (非致命): {e}")
        return ""

    # ------------------------------------------------------------------
    # 5. 多模型思考 (核心)
    # ------------------------------------------------------------------

    async def _execute_multi_model_thinking(
        self,
        user_input: str,
        session_id: str,
        expert_guidance: dict,
        event_callback,
        skill_id: str = "",
        context: List[Dict] = None,
    ) -> Dict:
        """执行多模型思考 — 统一探针驱动流程

        所有模型（large/supervisor/expert/tool）通过同一流程激活：
        CognitiveBlackboard 写入 → probe_start → ModelRunnerManager

        大模型由编排器在用户输入后直接发送 probe_start 激活。
        """
        import time
        timings = {}
        start = time.time()
        timings['开始'] = (0, '多模型思考启动')

        try:
            # ---- SessionLifecycle: 会话生命周期 + CognitiveBlackboard ----
            runner_manager = None
            turn_context = None
            blackboard = None

            timings = {}
            start = time.time()
            timings['开始'] = (0, '多模型思考启动')

            try:
                from modules.thinking.context.pool import TurnContext
                from modules.thinking.cognition.blackboard import CognitiveBlackboard
                turn_context = TurnContext(session_id=session_id or "", user_input=user_input)
                blackboard = CognitiveBlackboard(
                    session_id=session_id or "",
                    turn_id=turn_context.turn_id,
                )
                blackboard.set_goal(user_input)
                # 注册到全局会话表（供管理 API）
                with _session_registry_lock:
                    _session_registry[session_id or ""] = {
                        "session_id": session_id or "",
                        "state": "planning",
                        "is_active": True,
                        "turn_id": turn_context.turn_id,
                        "blackboard": blackboard,
                        "turn_context": turn_context,
                        "started_at": time.time(),
                    }
                t1 = time.time() - start
                timings['SessionLifecycle'] = (t1, f'会话初始化完成')
                logger.info(
                    f"[编排器] 会话就绪: session={session_id[:12]}, turn={turn_context.turn_id[:8]} (+{t1:.2f}s)"
                )
            except Exception as e:
                logger.debug(f"[编排器] 初始化失败 (非致命): {e}")
            except Exception as e:
                logger.debug(f"[SessionLifecycle] 初始化失败 (非致命): {e}")
                blackboard = None
                turn_context = None

            # 记录上一次用户说话时间（不是当前时间）
            # 从 context 中找倒数第二条 user 消息的时间戳
            if context:
                prev_user_time = 0.0
                for msg in reversed(context[:-1]):  # 排除当前消息
                    if msg.get("role") == "user" and msg.get("timestamp"):
                        prev_user_time = msg["timestamp"]
                        break
                if prev_user_time > 0:
                    if turn_context:
                        turn_context.last_user_message_time = prev_user_time
                    if blackboard:
                        blackboard.runtime_state["last_user_message_time"] = prev_user_time

            # 注入 Blackboard 到 ToolSecurityGate（用于安全拦截检查）
            if blackboard:
                try:
                    from modules.security_system.tool_security_gate import get_tool_security_gate
                    get_tool_security_gate().set_active_blackboard(blackboard)
                except Exception:
                    pass

            # ---- ModelRunnerManager: 监听 probe_start/probe_stop 命令 ----
            try:
                from modules.thinking.core.model_runner import get_runner_manager
                t_before = time.time()
                runner_manager = get_runner_manager(
                    session_id or "",
                    blackboard=blackboard,
                    turn_context=turn_context,
                )
                await runner_manager.start_listening()
                # 注册到全局会话表（供轮询检查委托状态）
                with _session_registry_lock:
                    if (session_id or "") in _session_registry:
                        _session_registry[session_id or ""]["runner_manager"] = runner_manager
                t3 = time.time() - start
                timings['ModelRunnerManager'] = (t3 - timings.get('SessionLifecycle', (0,))[0], f'模型运行管理器启动')
                logger.info(
                    f"[ModelRunnerManager] 已启动: "
                    f"session={str(session_id)[:8] if session_id else '?'} (+{time.time() - t_before:.3f}s)"
                )
            except Exception as e:
                logger.warning(f"[ModelRunnerManager] 启动失败 (非致命): {e}")

            # ---- 写入上下文到 CognitiveBlackboard ----

            # 1. 委托引导（系统级，持久上下文）
            from config.prompts.composer import PromptComposer, PromptRequest
            composer = PromptComposer()
            supervisor_list = composer._build_supervisor_table()
            expert_list = composer._build_expert_table()
            delegation_guidance = (
                "【多模型协作 — 使用内部控制工具委托，不要滥用委托】\n"
                "搜索、读文件、写代码等需要外部执行的操作，应通过 delegate_task 委托给合适主管或专家。\n"
                "寒暄、需求澄清、等待用户补充、普通对话、业务判断，不要委托专家。\n"
                "用户只是打招呼或没有提出具体任务时，直接友好回复并请用户说明需求。\n"
                "\n"
                f"可用主管：\n{supervisor_list}\n"
                f"\n可用专家：\n{expert_list}\n"
                "\n"
                "专家只用于明确的工具执行任务：web_search(联网搜索) / search_files(搜文件) / read_file(读文件) / write_file(写文件) / 执行命令等。\n"
                "用户请求最新数据、网页信息、玩家数量、文件/桌面/系统状态时，应委托专家执行明确工具任务，不要直接回答不知道。\n"
                "反例：用户说你好 → 不要委托专家，直接回复问候。\n"
                "反例：不知道用户要做什么 → 不要委托专家，直接请用户补充需求。\n"
            )
            if blackboard:
                blackboard.add_observation(
                    tier="system",
                    content=delegation_guidance,
                    metadata={"context_type": "delegation_guidance"},
                )


            # 1b. 当前会话对话历史（短期记忆）
            if blackboard and context:
                history_lines = []
                for msg in context[-12:]:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if content and isinstance(content, str):
                        history_lines.append(f"[{role}]: {content[:500]}")
                if history_lines:
                    history_text = "【对话历史】\n" + "\n".join(history_lines)
                    blackboard.add_observation(
                        tier="system",
                        content=history_text,
                        metadata={"context_type": "conversation_history"},
                    )
                    logger.info(f"[编排器] 注入对话历史到黑板: {len(history_lines)} 条, {len(history_text)} 字符")
            else:
                logger.info(f"[编排器] 无对话历史 (blackboard={bool(blackboard)}, context={len(context) if context else 0})")

            # 2. 良知引导（注入内心独白）
            inner_thoughts = expert_guidance.get("inner_thoughts", "") if expert_guidance else ""
            if inner_thoughts:
                if blackboard:
                    blackboard.add_observation(
                        tier="system",
                        content=inner_thoughts,
                        metadata={"context_type": "conscience_guidance"},
                    )
                try:
                    from modules.thinking.probes.probe_tools import set_session_guidance
                    set_session_guidance(session_id or "", {"inner_thoughts": inner_thoughts})
                except Exception as e:
                    logger.debug(f"[编排器] 会话引导注入失败 (非致命): {e}")

            # ---- 直接激活大模型（替代 SessionMonitor）----
            # 用户输入后立即发送 probe_start，通知 ModelRunnerManager 启动大模型
            try:
                if runner_manager and blackboard:
                    from modules.thinking.communication.message_bus import Message, MessageType, get_message_bus
                    bus = get_message_bus()
                    msg = Message(
                        msg_type=MessageType.SYSTEM,
                        sender="orchestrator",
                        recipient=f"model_runner_manager_{str(session_id)[:8]}",
                        content={
                            "action": "probe_started",
                            "probe_id": "probe_user_input",
                            "target_tier": "large",
                            "identity_key": "orchestrator",
                            "task_description": user_input,
                            "return_to_model_id": "",
                            "return_to_session_id": session_id or "",
                            "priority": 10,
                            "ttl_seconds": 3600,
                            "caller_tier": "system",
                            "skill_id": skill_id,
                        },
                    )
                    await bus.send(msg)
                    logger.info(f"[编排器] 直接激活大模型: session={str(session_id)[:8]}")
                else:
                    logger.warning(f"[编排器] runner_manager 或 blackboard 不可用，跳过直接激活")
            except Exception as e:
                logger.warning(f"[编排器] 直接激活大模型失败 (非致命): {e}")

            # 4. 用户输入（触发 probe_start → 大模型激活）
            turn_start_ts = time.time()
            if blackboard:
                user_entry = blackboard.write_user_input(user_input)
                turn_start_ts = getattr(user_entry, "timestamp", turn_start_ts)
            else:
                turn_start_ts = time.time()

            # ---- 等待大模型完成 (轮询 + 活动感知超时) ----
            t_wait_start = time.time()
            final_response = ""
            POLL_INTERVAL = 15  # 每 15s 检查一次
            bus = get_message_bus()
            done_event = asyncio.Event()
            orch_channel = f"orchestrator_{session_id[:12]}"
            logger.info(f"[编排器] 等待 thinking_complete: channel={orch_channel} session={session_id}")

            async def _on_orchestrator_msg(_msg):
                try:
                    msgs = await bus.peek(orch_channel, limit=5)
                    for m in msgs:
                        content = m.content if hasattr(m, 'content') else {}
                        if (
                            isinstance(content, dict)
                            and content.get("action") == "thinking_complete"
                            and content.get("tier") == "large"
                            and content.get("session_id") == session_id
                        ):
                            done_event.set()
                            return
                except Exception as e:
                    logger.warning(f"[编排器] 消息回调异常: {e}")

            def _has_pending_delegations() -> bool:
                """检查大模型是否有未完成委托（说明仍在工作中）"""
                try:
                    with _session_registry_lock:
                        info = _session_registry.get(session_id or "")
                    if info:
                        mgr = info.get("runner_manager")
                        if mgr:
                            runners = mgr.get_active_runners()
                            for r in runners.values():
                                if r.tier == "large" and r._thinker:
                                    return bool(getattr(r._thinker, '_pending_delegations', None))
                except Exception:
                    pass
                return False

            completed = False
            consecutive_idle = 0
            MAX_IDLE_CHECKS = 4  # 连续 4 次（60s）无进展则超时
            MAX_WALL_TIME = 600  # 硬上限 10 分钟

            try:
                await bus.subscribe(orch_channel, _on_orchestrator_msg)
                stale = await bus.receive(orch_channel)
                while stale:
                    content = stale.content if hasattr(stale, "content") else {}
                    if (
                        isinstance(content, dict)
                        and content.get("action") == "thinking_complete"
                        and content.get("tier") == "large"
                        and content.get("session_id") == session_id
                    ):
                        done_event.set()
                        break
                    stale = await bus.receive(orch_channel)

                while not completed and (time.time() - t_wait_start) < MAX_WALL_TIME:
                    try:
                        await asyncio.wait_for(asyncio.shield(done_event.wait()), timeout=POLL_INTERVAL)
                        completed = True
                        break
                    except asyncio.TimeoutError:
                        pass

                    elapsed = time.time() - t_wait_start
                    if blackboard and blackboard.final_response:
                        final_response = blackboard.final_response
                        completed = True
                        logger.info(f"[编排器] 从黑板获取到 final_response (+{elapsed:.0f}s)")
                        break

                    if _has_pending_delegations():
                        consecutive_idle = 0
                        logger.info(f"[编排器] 大模型等待委托中，延长等待 (+{elapsed:.0f}s)")
                    else:
                        consecutive_idle += 1
                        logger.debug(f"[编排器] 无进展 ({consecutive_idle}/{MAX_IDLE_CHECKS}) +{elapsed:.0f}s")

                    if consecutive_idle >= MAX_IDLE_CHECKS:
                        logger.warning(f"[编排器] 连续 {consecutive_idle} 次无进展，判定超时 (+{elapsed:.0f}s)")
                        break

                t_wait_elapsed = time.time() - t_wait_start
                timings['WaitLargeModel'] = (t_wait_elapsed, f'等待大模型完成' + (f' (完成)' if completed else f' (超时或中止)'))
                logger.info(f"[等待大模型] {('完成信号已收到' if completed else '等待超时')} (+{t_wait_elapsed:.2f}s)")

                if not final_response and blackboard and blackboard.final_response:
                    final_response = blackboard.final_response
            finally:
                try:
                    await bus.unsubscribe(orch_channel, _on_orchestrator_msg)
                except Exception as e:
                    logger.debug(f"[编排器] 取消订阅失败 (非致命): {e}")

            if not final_response:
                if completed:
                    logger.warning("[编排器] 大模型已发送完成信号但无 final_response")
                else:
                    logger.warning(f"[编排器] 大模型超时，尝试恢复最后可用的内容")
                if blackboard and blackboard.final_response:
                    final_response = blackboard.final_response

            if not final_response:
                if completed:
                    final_response = "[系统通知] 思考已完成，但没有生成可见回复。请补充更具体的需求后重试。"
                else:
                    # 即使超时，也记录一条信息而不是直接拒绝
                    logger.warning(
                        f"[编排器] 警告：无法获取到任何回复 "
                        f"(completed={completed}, 已等待{t_wait_elapsed:.0f}s)"
                    )
                    final_response = "[系统通知] 思考超时，请重试。"

            t_total = time.time() - start
            timings['总耗时'] = (t_total, f'完整流程耗时')

            # 打印性能统计
            logger.info(f"\n【性能统计】会话 {session_id[:12]}:")
            for key, (elapsed, desc) in sorted(timings.items(), key=lambda x: x[1][0]):
                pct = (elapsed / t_total * 100) if t_total > 0 else 0
                logger.info(f"  • {desc:20s} {elapsed:7.3f}s ({pct:5.1f}%)")
            logger.info(f"  最慢步骤: {max(timings.items(), key=lambda x: x[1][0])[0]}")

            return {
                "response": final_response,
                "thinking_history": [],
                "thinking_turns": 0,
                "probe_signals": [],
                "blackboard": blackboard,
            }

        except Exception as e:
            logger.error(f"多模型思考失败: {e}")
            return {
                "response": f"[思考失败] {e}",
                "thinking_history": [],
                "thinking_turns": 0,
                "probe_signals": [],
                "blackboard": None,
            }
        finally:
            try:
                from modules.thinking.core.model_runner import remove_runner_manager
                asyncio.create_task(remove_runner_manager(session_id or ""))
            except Exception as e:
                logger.debug(f"[编排器] runner_manager 清理失败 (非致命): {e}")

    @staticmethod
    def _is_user_visible_response(entry: Dict[str, Any]) -> bool:
        """判断 CognitiveBlackboard 条目是否适合作为最终用户回复。"""
        if not entry:
            return False
        metadata = entry.get("metadata") or {}
        if metadata.get("internal_protocol") or metadata.get("final_visible") is False:
            return False
        content = str(entry.get("content", "")).strip()
        if not content:
            return False
        blocked_markers = (
            "delegate_task",
            "continue_thinking",
            "probe_start",
            "probe_started",
        )
        return not any(marker in content for marker in blocked_markers)

    # ------------------------------------------------------------------
    # 6. 输出审查
    # ------------------------------------------------------------------

    async def _review_output(self, raw_response: str, user_input: str = "", expert_guidance: dict = None, blackboard=None) -> str:
        """输出清洗 + 安全信号检查"""
        # 先做输出清洗
        cleaned = await self._get_output_reviewer().review(raw_response, user_input)

        # 检查 Blackboard 中的安全拦截信号
        try:
            if blackboard and blackboard.has_security_block():
                block = blackboard.get_security_block()
                if block:
                    logger.warning(
                        f"[安全拦截] {block.get('category', '')}: {block.get('description', '')[:100]}"
                    )
                    return (
                        f"[安全审查拦截] {block.get('description', '检测到安全风险')}\n"
                        f"风险级别: {block.get('risk_level', 'high')}\n"
                        f"如需继续，请检查操作是否安全后重试。"
                    )
        except Exception:
            pass

        return cleaned

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    async def _maybe_evolve_values(user_input: str, response: str):
        """价值观演化（fire-and-forget）：高风险对话后自动反思"""
        try:
            from modules.thinking.conscience import get_conscience
            from infra.model.small_model_client import SmallModelClient
            from config.settings import settings

            if len(response) < 20:
                return

            risk_keywords = ["删除", "rm ", "DROP", "格式化", "密码", "sudo ", "生产", "prod"]
            if not any(kw in user_input + response for kw in risk_keywords):
                return

            cons = get_conscience()
            client = SmallModelClient(
                api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
                api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
            )
            cons._model_client = client
            await cons.review_and_evolve(
                full_dialog=f"用户: {user_input}\n助手: {response}",
                trigger_reason="检测到高风险关键词",
            )
        except Exception as e:
            logger.debug(f"[价值观演化] 非致命错误: {e}")

