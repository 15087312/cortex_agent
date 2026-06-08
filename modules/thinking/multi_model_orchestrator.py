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

from modules.thinking.evolution.reflection_sm import (
    ReflectionStateMachine, StepContext, TriggerType,
)
from modules.thinking.ports import (
    ActivityNotifierPort,
    ContextPort,
    GuidancePort,
    OutputReviewPort,
    SecurityPort,
)

from utils.logger import setup_logger

logger = setup_logger("multi_model_orchestrator")


class MultiModelOrchestrator:
    """多模型编排器"""

    def __init__(
        self,
        gcm_pool=None,
        activity_notifier: Optional[ActivityNotifierPort] = None,
        security: Optional[SecurityPort] = None,
        context_service: Optional[ContextPort] = None,
        guidance_service: Optional[GuidancePort] = None,
        output_reviewer: Optional[OutputReviewPort] = None,
    ):
        self._gcm_pool = gcm_pool
        self._activity_notifier = activity_notifier
        self._security = security
        self._context_service = context_service
        self._guidance_service = guidance_service
        self._output_reviewer = output_reviewer
        # 反思状态机（首次访问时惰性初始化，避免 import 顺序问题）
        self._reflection_sm = None

        # S7: WebSocket 消息队列（per-session 串行处理）
        self._request_queues: Dict[str, asyncio.Queue] = {}
        self._queue_consumers: Dict[str, asyncio.Task] = {}

    def _get_reflection_sm(self):
        if self._reflection_sm is None:
            self._reflection_sm = ReflectionStateMachine()
        return self._reflection_sm

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

    def _get_context_service(self) -> ContextPort:
        if self._context_service is None:
            from modules.thinking.adapters import ContextManagerAdapter

            self._context_service = ContextManagerAdapter()
        return self._context_service

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
            调度结果 dict，兼容旧 api_stream.py 的字段:
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
        # 透传事件回调到反思状态机（后续被 ContinuousThinker 使用）
        if event_callback:
            self._get_reflection_sm().set_event_callback(event_callback)
        logger.info(f"[编排器] 接收输入: {user_input}...")

        # 通知差异检测器有活动
        try:
            self._get_activity_notifier().notify_activity()
        except Exception as e:
            logger.debug(f"[活动通知] 非致命错误: {e}")

        # 重置主动搭话冷却（用户正在说话，不需要搭话）
        try:
            from modules.thinking.proactive_outreach import get_proactive_outreach_handler
            get_proactive_outreach_handler().reset_cooldown()
        except Exception as e:
            logger.debug(f"[主动搭话] 重置冷却失败 (非致命): {e}")

        # ---- 1. 安全验证 ----
        security_passed, security_error = await self._validate_security(user_input)
        if not security_passed:
            logger.warning(f"[安全拦截] {security_error}")
            return self._build_security_error(security_error, start_time)

        # ---- 1.5 记录用户说话时间（仅用户消息，非感知系统触发） ----
        try:
            from modules.thinking.session.session_manager import get_session_manager
            _sm = get_session_manager()
            _session = _sm.get_session(session_id)
            if _session:
                _session.last_user_message_time = time.time()
        except Exception as e:
            logger.debug(f"[会话管理] 记录用户说话时间失败 (非致命): {e}")

        # ---- 2. 记忆上下文 ----
        memory_context_text, mm = await self._load_memory_context_via_api(
            user_input, context, session_id
        )

        # ---- 3. 专家引导 (情绪 + 价值观) ----
        from config.settings import settings as _settings
        if _settings.is_expert_pipeline_enabled:
            expert_guidance = await self._run_expert_pipeline(
                user_input, memory_context_text
            )
        else:
            expert_guidance = {}

        # ---- 3.5 技能匹配 ----
        skill_id = self._match_skill(user_input)

        # ---- 4. 多模型思考 (核心) ----
        thinking_result = await self._execute_multi_model_thinking(
            user_input=user_input,
            session_id=session_id,
            memory_context_text=memory_context_text,
            expert_guidance=expert_guidance,
            memory_manager=mm,
            event_callback=event_callback,
            skill_id=skill_id,
        )

        raw_response = thinking_result.get("response", "")
        thinking_history = thinking_result.get("thinking_history", [])
        thinking_turns = thinking_result.get("thinking_turns", 0)
        probe_signals = thinking_result.get("probe_signals", [])
        blackboard = thinking_result.get("blackboard")

        # ---- 5. 输出审查 (专家系统) ----
        final_response = await self._review_output(raw_response, user_input, expert_guidance, blackboard)

        # ---- 6. 记忆存储 + GCM 同步 ----
        await self._save_memory(mm, session_id, user_input, final_response, self._gcm_pool, thinking_turns)

        # ---- 7. 记忆晋升 (fire-and-forget, 不阻塞主流程) ----
        asyncio.create_task(self._promote_memories(mm))

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
    # 2. 记忆上下文
    # ------------------------------------------------------------------

    async def _load_memory_context_via_api(self, user_input: str, context: List[Dict], session_id: str):
        return await self._get_context_service().load_context(user_input, context, session_id)

    # ------------------------------------------------------------------
    # 3. 专家引导
    # ------------------------------------------------------------------

    async def _run_expert_pipeline(self, user_input: str, memory_context_text: str) -> dict:
        return await self._get_guidance_service().run(user_input, memory_context_text)

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
        memory_context_text: str,
        expert_guidance: dict,
        memory_manager,
        event_callback,
        skill_id: str = "",
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
            lifecycle = None

            timings = {}
            start = time.time()
            timings['开始'] = (0, '多模型思考启动')

            try:
                from modules.thinking.cognition.session_lifecycle import SessionLifecycle
                lifecycle = SessionLifecycle(session_id or "")
                turn_context = lifecycle.start_turn(user_input)
                blackboard = lifecycle.blackboard
                t1 = time.time() - start
                timings['SessionLifecycle'] = (t1, f'会话初始化完成')
                logger.info(
                    f"[SessionLifecycle] 会话就绪: session={session_id[:12]}, turn={turn_context.turn_id[:8]}, state={lifecycle.state.value} (+{t1:.2f}s)"
                )
            except Exception as e:
                logger.debug(f"[SessionLifecycle] 初始化失败 (非致命): {e}")
                blackboard = None
                turn_context = None

            # 注册 SecurityMonitor model_id 到 Blackboard（用于触发安全审查）
            if blackboard:
                try:
                    from modules.thinking.identity import get_identities
                    identities = get_identities()
                    sm_identity = identities.get("expert_security_monitor", {})
                    sm_model_id = sm_identity.get("model_id", "expert_security_monitor_001")
                    blackboard.set_security_monitor_id(sm_model_id)
                except Exception:
                    blackboard.set_security_monitor_id("expert_security_monitor_001")

                # 注入 Blackboard 到 ToolSecurityGate（用于安全拦截检查）
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
            from modules.thinking.identity import (
                build_expert_capability_list,
                build_supervisor_capability_list,
            )
            supervisor_list = build_supervisor_capability_list()
            expert_list = build_expert_capability_list()
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


            # 2. 专家引导（情绪 + 价值观 + 安全）
            if expert_guidance:
                guidance_text = self._format_expert_guidance(expert_guidance)
                if guidance_text:
                    if blackboard:
                        blackboard.add_observation(
                            tier="system",
                            content=guidance_text,
                            metadata={"context_type": "expert_guidance"},
                        )

                try:
                    from modules.thinking.probes.probe_tools import set_session_guidance
                    set_session_guidance(session_id or "", {
                        "principle": expert_guidance.get("principle", ""),
                        "reflection": expert_guidance.get("reflection", ""),
                        "risk": expert_guidance.get("risk_level", "none"),
                        "safety": expert_guidance.get("safety_guidance", ""),
                        "emotion": expert_guidance.get("emotion", "neutral"),
                        "emotion_intensity": expert_guidance.get("emotion_intensity", 0.3),
                        "emotion_guidance": expert_guidance.get("emotion_guidance", ""),
                        "ai_mood": expert_guidance.get("ai_mood", "平和"),
                        "emotion_strategy": expert_guidance.get("emotion_strategy", ""),
                    })
                except Exception as e:
                    logger.debug(f"[编排器] 会话引导注入失败 (非致命): {e}")

            # 3. 记忆上下文注入 CognitiveBlackboard
            if blackboard:
                self._get_context_service().inject_to_dialog(blackboard, memory_context_text)

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
                            "identity_key": "large",
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

            # ---- 启动 SecurityMonitor（持久运行时专家）----
            try:
                if runner_manager:
                    from modules.thinking.communication.message_bus import Message, MessageType, get_message_bus
                    bus = get_message_bus()
                    sm_msg = Message(
                        msg_type=MessageType.SYSTEM,
                        sender="orchestrator",
                        recipient=f"model_runner_manager_{str(session_id)[:8]}",
                        content={
                            "action": "probe_started",
                            "probe_id": "probe_security_monitor",
                            "target_tier": "expert",
                            "identity_key": "expert_security_monitor",
                            "task_description": "安全监察常驻任务：实时审查 CognitiveBlackboard 上下文",
                            "return_to_model_id": "",
                            "return_to_session_id": session_id or "",
                            "priority": 10,
                            "ttl_seconds": 3600,
                            "caller_tier": "system",
                        },
                    )
                    await bus.send(sm_msg)
                    logger.info(f"[编排器] SecurityMonitor 已启动: session={str(session_id)[:8]}")
            except Exception as e:
                logger.warning(f"[编排器] SecurityMonitor 启动失败 (非致命): {e}")

            # 4. 用户输入（触发 probe_start → 大模型激活）
            turn_start_ts = time.time()
            if blackboard:
                user_entry = blackboard.write_user_input(user_input)
                turn_start_ts = getattr(user_entry, "timestamp", turn_start_ts)
            else:
                turn_start_ts = time.time()

            # ---- 等待大模型完成 (事件驱动) ----
            t_wait_start = time.time()
            final_response = ""
            LARGE_TIMEOUT = 300  # 5分钟，允许多轮思考和工具调用

            from modules.thinking.communication.message_bus import get_message_bus
            bus = get_message_bus()
            done_event = asyncio.Event()
            orch_channel = f"orchestrator_{session_id[:12]}"

            async def _on_orchestrator_msg(_msg):
                try:
                    msgs = await bus.receive(orch_channel)
                    for m in msgs:
                        content = (
                            m.content if hasattr(m, 'content')
                            else m.get("content", {})
                        )
                        if (
                            isinstance(content, dict)
                            and content.get("action") == "thinking_complete"
                            and content.get("tier") == "large"
                            and content.get("session_id") == session_id
                        ):
                            done_event.set()
                            return
                except Exception as e:
                    logger.debug(f"[编排器] 消息处理回调异常 (非致命): {e}")

            completed = False
            try:
                await bus.subscribe(orch_channel, _on_orchestrator_msg)

                try:
                    await asyncio.wait_for(done_event.wait(), timeout=LARGE_TIMEOUT)
                    completed = True
                except asyncio.TimeoutError:
                    completed = False
                t_wait_elapsed = time.time() - t_wait_start
                timings['WaitLargeModel'] = (t_wait_elapsed, f'等待大模型完成' + (f' (完成)' if completed else f' (超时或中止)'))
                logger.info(f"[等待大模型] {('完成信号已收到' if completed else '等待超时')} (+{t_wait_elapsed:.2f}s)")

                # 1. 优先从 CognitiveBlackboard 读取
                if blackboard and blackboard.final_response:
                    final_response = blackboard.final_response
                    logger.info(
                        f"[编排器] 大模型已完成（来自 CognitiveBlackboard），回复长度 {len(final_response)} 字符"
                    )
            finally:
                try:
                    await bus.unsubscribe(orch_channel, _on_orchestrator_msg)
                except Exception as e:
                    logger.debug(f"[编排器] 取消订阅失败 (非致命): {e}")

            if not final_response:
                if completed:
                    logger.warning(
                        "[编排器] 大模型已发送完成信号，但没有可见 final_draft，"
                        "返回最后可用的内容"
                    )
                else:
                    logger.warning(
                        f"[编排器] 大模型超时 ({LARGE_TIMEOUT}s)，"
                        f"尝试恢复最后可用的内容"
                    )
                # 1. 优先从 CognitiveBlackboard 读取
                if blackboard and blackboard.final_response:
                    final_response = blackboard.final_response
                    logger.info(f"[编排器] 从 CognitiveBlackboard 恢复 final_response: {len(final_response)} 字符")

            if not final_response:
                if completed:
                    final_response = "[系统通知] 思考已完成，但没有生成可见回复。请补充更具体的需求后重试。"
                else:
                    # 即使超时，也记录一条信息而不是直接拒绝
                    logger.warning(
                        f"[编排器] 警告：无法获取到任何回复 "
                        f"(completed={completed}, 已等待{LARGE_TIMEOUT}s)"
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

            # ---- 协作回合反思（时机5） ----
            try:
                sd_text = blackboard.format_for_model(limit=5) if blackboard else ""
                col_ctx = StepContext(
                    node_id="multi_model_collab",
                    trigger=TriggerType.COLLAB_ROUND,
                    task_goal=user_input,
                    execution_log=sd_text if sd_text else final_response,
                    error_message=(
                        "超时" if (not completed and "超时" in final_response)
                        else "无可见回复" if (completed and "没有生成可见回复" in final_response)
                        else ""
                    ),
                )
                await self._get_reflection_sm().on_collaboration_round(col_ctx)
            except Exception as e:
                logger.debug(f"[协作反思] 非致命: {e}")

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
        cleaned = await self._get_output_reviewer().review(raw_response, user_input, expert_guidance)

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
    # 7. 记忆存储
    # ------------------------------------------------------------------

    async def _save_memory(self, mm, session_id: str, user_input: str, final_response: str, gcm_pool=None, turns=0):
        self._get_context_service().save_memory(
            mm,
            session_id,
            user_input,
            final_response,
            gcm_pool=gcm_pool,
            turns=turns,
        )

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _format_expert_guidance(guidance: Dict[str, Any]) -> str:
        from config.settings import settings as _cfg

        if _cfg.COMPANION_MODE:
            # 陪伴模式：第一人称心理活动，无内部实现细节
            parts = []
            principle = guidance.get("principle", "")
            reflection = guidance.get("reflection", "")
            if principle and reflection:
                parts.append(f"【准则】{principle}\n【反思】{reflection}")
            risk_level = guidance.get("risk_level", "")
            if risk_level and risk_level not in ("none", ""):
                safety_guidance = guidance.get("safety_guidance", "")
                if safety_guidance:
                    parts.append(f"【注意】{safety_guidance}")
            emotion = guidance.get("emotion", "")
            if emotion and emotion != "neutral":
                ai_mood = guidance.get("ai_mood", "")
                emotion_guidance = guidance.get("emotion_guidance", "")
                if ai_mood:
                    parts.append(f"【心理状态】{ai_mood}")
                if emotion_guidance:
                    parts.append(emotion_guidance)
            if not parts:
                return ""
            return "\n".join(parts)

        # 工作模式：保留完整信息
        parts = ["[专家系统引导]"]
        principle = guidance.get("principle", "")
        reflection = guidance.get("reflection", "")
        if principle:
            parts.append(f"价值观准则: {principle}")
        if reflection:
            parts.append(f"反思: {reflection}")
        risk_level = guidance.get("risk_level", "")
        if risk_level:
            parts.append(f"风险等级: {risk_level}")
        emotion = guidance.get("emotion", "")
        emotion_guidance = guidance.get("emotion_guidance", "")
        if emotion and emotion != "neutral":
            intensity = guidance.get("emotion_intensity", 0.3)
            ai_mood = guidance.get("ai_mood", "")
            parts.append(f"用户情绪: {emotion} (强度{intensity})")
            if ai_mood:
                parts.append(f"你的心情: {ai_mood}")
            if emotion_guidance:
                parts.append(f"行为指导: {emotion_guidance}")
        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    @staticmethod
    async def _promote_memories(mm) -> None:
        """
        记忆晋升决策 (fire-and-forget)

        在每轮处理结束后调用，将符合条件的 private 记忆晋升为 shared。
        条件：重要性 >= 0.7 的 private 记忆 → shared。
        失败仅记日志，绝不阻塞或抛异常。
        """
        if mm is None:
            return
        try:
            promoted = mm.promote_private_memories(
                memory_type=None,
                target_scope="shared",
                min_importance=0.7,
                max_promote=5,
            )
            if promoted:
                logger.info(f"[记忆晋升] 已晋升 {len(promoted)} 条: {promoted}")
        except Exception as e:
            logger.debug(f"[记忆晋升] 非致命错误: {e}")

