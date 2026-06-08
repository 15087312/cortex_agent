"""
流式思考 API - WebSocket + SSE

能力：
- 统一事件 envelope（WS/SSE 同构）
- 会话创建与上下文查询
- 接入真实调度链（UnifiedScheduler）
- 自动记忆提取与个性化注入
"""
import asyncio
import json
import threading
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.errors import AppError, ErrorCode
from sse_starlette.sse import EventSourceResponse

from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
from utils.logger import setup_logger

router = APIRouter(prefix="/stream", tags=["流式思考"])
logger = setup_logger("stream_api")


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections[session_id] = websocket
            self._loop = asyncio.get_running_loop()

    async def disconnect(self, session_id: str):
        async with self._lock:
            if session_id in self.active_connections:
                del self.active_connections[session_id]

    async def send_json(self, session_id: str, data: dict):
        async with self._lock:
            websocket = self.active_connections.get(session_id)
            if websocket:
                await websocket.send_json(data)

    def send_json_from_thread(self, session_id: str, data: dict, timeout: float = 5.0) -> bool:
        """从非事件循环线程安全地发送 WebSocket 消息

        使用 run_coroutine_threadsafe 将发送调度到 uvicorn 事件循环。
        返回 True 表示发送成功，False 表示无连接或发送失败。
        """
        if not self._loop or self._loop.is_closed():
            logger.warning("[ConnectionManager] send_json_from_thread: 无可用事件循环")
            return False
        if session_id not in self.active_connections:
            logger.debug(f"[ConnectionManager] send_json_from_thread: session {session_id[:8]} 无活跃连接")
            return False

        async def _send():
            async with self._lock:
                ws = self.active_connections.get(session_id)
                if ws:
                    await ws.send_json(data)
                    return True
            return False

        try:
            future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
            return future.result(timeout=timeout)
        except Exception as e:
            logger.error(f"[ConnectionManager] send_json_from_thread 失败: {e}")
            return False

    async def broadcast(self, data: dict):
        for session_id in list(self.active_connections.keys()):
            await self.send_json(session_id, data)


connection_manager = ConnectionManager()


def _build_event(
        *,
        session_id: str,
        msg_type: str,
        event: str,
        content: str = "",
        role: str = "system",
        data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一事件 envelope（WS/SSE 同构）"""
    return {
        "type": msg_type,
        "event": event,
        "session_id": session_id,
        "role": role,
        "content": content,
        "data": data or {},
        "timestamp": time.time(),
    }


class StreamThinkingSystem:
    """流式会话系统（接入真实调度链 + 自动记忆提取）"""

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._lock = asyncio.Lock()
        self._orchestrator = MultiModelOrchestrator()

    async def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        await self.start(session_id)
        return session_id

    async def start(self, session_id: str):
        async with self._lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = {
                    "created_at": datetime.now().isoformat(),
                    "messages": [],
                    "running": True,
                    "processing": False,
                }
            else:
                self.sessions[session_id]["running"] = True
            self.sessions[session_id]["started_at"] = time.time()
        self._running = True

        # 确保 session_manager 中有此 session（Blackboard 共享给编排器）
        try:
            from modules.thinking.session.session_manager import get_session_manager
            get_session_manager().create_main_session(session_id)
        except Exception as e:
            logger.debug(f"[SessionManager] 创建主会话失败 (非致命): {e}")

        # T1: 会话启动预加载核心记忆 (fire-and-forget, 不阻塞)
        asyncio.create_task(self._preload_session_memories(session_id))

    async def _preload_session_memories(self, session_id: str):
        """T1: 预加载用户偏好、最近任务、全局经验"""
        try:
            from modules.memory.core.session_memory_preloader import SessionMemoryPreloader
            from modules.memory.core.memory_manager import MemoryManager

            mm = MemoryManager()
            mm.set_session_id(session_id)

            gcm_pool = None
            try:
                from modules.thinking.context import gcm_pool
            except Exception as e:
                logger.debug(f"[T1] gcm_pool 导入失败，跳过 GCM 集成: {e}")

            preloader = SessionMemoryPreloader(mm, gcm_pool)
            await preloader.preload(session_id)
        except Exception as e:
            logger.debug(f"[T1] 会话预加载失败 (非致命): {e}")

    async def stop(self, session_id: str = ""):
        async with self._lock:
            if session_id:
                if session_id in self.sessions:
                    self.sessions[session_id]["running"] = False
                    # 取消正在运行的调度任务
                    task = self.sessions[session_id].get("scheduler_task")
                    if task and not task.done():
                        task.cancel()
            else:
                self._running = False

    async def _append_message(self, session_id: str, role: str, content: str):
        async with self._lock:
            if session_id not in self.sessions:
                return
            self.sessions[session_id]["messages"].append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": time.time(),
                }
            )
            self.sessions[session_id]["messages"] = self.sessions[session_id]["messages"][-200:]

    async def _emit(
            self,
            session_id: str,
            envelope: Dict[str, Any],
            callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        if callback:
            await callback(envelope)
        # WebSocket 路径：通过 connection_manager 发送
        try:
            await connection_manager.send_json(session_id, envelope)
        except Exception as e:
            logger.debug(f"[WebSocket] 发送失败 (非致命): {e}")

    async def _set_processing(self, session_id: str, processing: bool):
        async with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id]["processing"] = processing

    async def _is_processing(self, session_id: str) -> bool:
        async with self._lock:
            session = self.sessions.get(session_id)
            return bool(session and session.get("processing"))

    def _format_scheduler_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event_type = event.get("type") or event.get("event_type", "event")
        action = event.get("action", "")
        target = event.get("target", "")
        source = event.get("source", "")
        success = bool(event.get("success", True))
        dialog_tier = ""

        if event_type == "tool_call":
            content = f"工具 {target} {action} {'成功' if success else '失败'}"
        elif event_type == "model_comm":
            payload = event.get("payload", {})
            metadata = payload.get("metadata", {})
            msg_type = payload.get("msg_type", "")
            tier = metadata.get("tier") or payload.get("tier", payload.get("sender_tier", "unknown"))
            phase = payload.get("phase", "comm")
            detail = payload.get("detail", "")
            sender = payload.get("sender", source)
            recipient = payload.get("recipient", target)

            if msg_type == "broadcast" and metadata.get("dialog_id"):
                raw_content = payload.get("content", "")
                dialog_tier = tier
                if isinstance(raw_content, dict):
                    dialog_text = raw_content.get("content", "")
                    entry_type = raw_content.get("entry_type", "")
                    round_num = raw_content.get("round", 0)
                else:
                    dialog_text = str(raw_content)
                    entry_type = ""
                    round_num = 0
                # 跳过空内容的轮次（模型只调 continue_thinking 不输出文字）
                if not dialog_text.strip():
                    return None
                tier_labels = {"large": "[总指挥]", "supervisor": "[主管]", "expert": "[专家]", "user": "[用户]"}
                tier_icons = {"large": "🧠", "supervisor": "📊", "expert": "🔧", "user": "👤"}
                label = tier_labels.get(dialog_tier, f"[{dialog_tier}]")
                icon = tier_icons.get(dialog_tier, "")
                # 专家输出截断：保留足够上下文供 TUI 展示
                if dialog_tier == "expert" and len(dialog_text) > 2000:
                    dialog_text = dialog_text[:2000] + "…"
                type_tag = {"thought": f"R{round_num}", "response": "回复"}.get(entry_type, "")
                if type_tag:
                    content = f"{icon} {label} [{type_tag}] {dialog_text}"
                else:
                    content = f"{icon} {label} {dialog_text}"
                dialog_tier = dialog_tier
            elif metadata.get("event") == "preliminary_response":
                raw_content = payload.get("content", "")
                if isinstance(raw_content, dict):
                    prelim_text = raw_content.get("content", str(raw_content))
                else:
                    prelim_text = str(raw_content)
                content = f"[preliminary] {prelim_text}"
            else:
                content = f"[{tier}] {sender} → {recipient}: {action}"
                if detail:
                    content = f"[{tier}] {detail[:120]}"
        elif event_type == "model_stage":
            content = f"模型阶段 {action} ({target})"
        elif event_type == "module":
            content = f"模块 {target} {action} {'成功' if success else '失败'}"
        elif event_type == "security":
            payload = event.get("payload", {})
            detail = payload.get("detail", "")
            duration = payload.get("duration_ms", 0)
            duration_str = f" ({duration}ms)" if duration else ""
            if detail:
                content = f"[安全审查] {target} {action}{duration_str} — {detail}"
            else:
                content = f"[安全审查] {target} {action}{duration_str}"
            if "等待用户审批" in action:
                logger.info(f"[API] 安全审批事件格式化: target={target}, request_id={payload.get('request_id', '')}")
        elif event_type == "scheduler":
            content = f"调度 {action}"
        else:
            content = f"{source or event_type} {action} {target}".strip()

        result_data = {
            "stage_event": event,
            "source": source,
            "event_type": event_type,
            "action": action,
            "target": target,
            "success": success,
            "latency_ms": event.get("latency_ms", 0),
            "payload": event.get("payload", {}),
            "trace_id": event.get("trace_id", ""),
        }
        if dialog_tier:
            result_data["dialog_tier"] = dialog_tier
        return {
            "content": content,
            "data": result_data,
        }

    async def think(
            self,
            session_id: str,
            user_input: str,
            callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> str:
        """执行真实调度链并按事件流输出"""
        if session_id not in self.sessions:
            await self.start(session_id)

        if await self._is_processing(session_id):
            await self._emit(
                session_id,
                _build_event(
                    session_id=session_id,
                    msg_type="ack",
                    event="busy",
                    content="会话正在处理中，请稍后",
                    role="system",
                ),
                callback,
            )
            return ""

        await self._set_processing(session_id, True)
        await self._append_message(session_id, "user", user_input)

        try:
            await self._emit(
                session_id,
                _build_event(
                    session_id=session_id,
                    msg_type="ack",
                    event="received",
                    content="已接收请求，开始处理",
                    role="system",
                ),
                callback,
            )

            context_messages = self.get_context(session_id)
            short_term_memory = [m.get("content", "") for m in context_messages[-6:]]
            scheduler_context = [
                {"role": m.get("role", ""), "content": m.get("content", "")}
                for m in context_messages[-8:]
            ]

            loop = asyncio.get_running_loop()
            stage_queue: asyncio.Queue = asyncio.Queue()
            streamed_stage_count = 0

            def scheduler_event_callback(event: Dict[str, Any]):
                try:
                    loop.call_soon_threadsafe(stage_queue.put_nowait, event)
                except Exception as e:
                    logger.debug(f"[调度回调] 事件投递失败 (非致命): {e}")

            try:
                from modules.thinking.communication import get_message_bus
                get_message_bus().set_event_emitter(scheduler_event_callback)
            except Exception as e:
                logger.debug(f"[消息总线] 设置事件发射器失败 (非致命): {e}")

            # 注入安全门控事件回调 — 安全审查结果推送到 CLI 流
            try:
                from modules.security_system.tool_security_gate import set_security_event_callback
                set_security_event_callback(scheduler_event_callback)
            except Exception as e:
                logger.debug(f"[安全门控] 设置事件回调失败 (非致命): {e}")

            scheduler_task = asyncio.create_task(
                self._orchestrator.process(
                    user_input,
                    scheduler_context,
                    short_term_memory,
                    scheduler_event_callback,
                    session_id,
                )
            )
            # 存储任务引用以便 stop() 可以取消
            self.sessions[session_id]["scheduler_task"] = scheduler_task

            last_progress_emit = 0.0
            while True:
                if scheduler_task.done() and stage_queue.empty():
                    break
                now = time.time()
                if now - last_progress_emit >= 1.0:
                    last_progress_emit = now
                    current_phase = "scheduler_running" if not scheduler_task.done() else "finalizing"

                    # 收集活跃专家名称 + 上下文窗口占用
                    active_experts = []
                    context_tokens = 0
                    context_window_size = 128000
                    try:
                        from modules.thinking.core.model_runner import _runner_managers, _runner_managers_lock
                        with _runner_managers_lock:
                            rm = _runner_managers.get(session_id)
                        if rm:
                            for runner_info in rm.list_runners():
                                if runner_info.get("running") and runner_info.get("tier") != "large":
                                    name = runner_info.get("name") or runner_info.get("role", "")
                                    if name:
                                        active_experts.append(name)
                                # 取大模型的上下文窗口信息
                                if runner_info.get("tier") == "large" and runner_info.get("running"):
                                    context_tokens = runner_info.get("context_tokens", 0)
                                    context_window_size = runner_info.get("context_window_size", 128000)
                    except Exception as e:
                        logger.debug(f"[活跃专家] 收集状态失败 (非致命): {e}")

                    await self._emit(
                        session_id,
                        _build_event(
                            session_id=session_id,
                            msg_type="status",
                            event="thinking_progress",
                            content=f"思考中 {int(now - self.sessions.get(session_id, {}).get('started_at', now))}s",
                            role="system",
                            data={
                                "phase": current_phase,
                                "badge": {
                                    "scheduler_running": "调度中",
                                    "finalizing": "收尾中",
                                }.get(current_phase, "思考中"),
                                "elapsed_s": int(now - self.sessions.get(session_id, {}).get('started_at', now)),
                                "queue_size": stage_queue.qsize(),
                                "running": not scheduler_task.done(),
                                "active_experts": active_experts,
                                "context_tokens": context_tokens,
                                "context_window_size": context_window_size,
                            },
                        ),
                        callback,
                    )
                try:
                    stage_event = await asyncio.wait_for(stage_queue.get(), timeout=0.2)
                    formatted = self._format_scheduler_event(stage_event)
                    if formatted is None:
                        continue  # 空内容轮次，跳过展示
                    event_role = formatted["data"].get("dialog_tier", "thinking")
                    await self._emit(
                        session_id,
                        _build_event(
                            session_id=session_id,
                            msg_type="thinking",
                            event="thinking_step",
                            content=formatted["content"],
                            role=event_role,
                            data=formatted["data"],
                        ),
                        callback,
                    )
                    streamed_stage_count += 1
                except asyncio.TimeoutError:
                    continue

            try:
                result = await scheduler_task
            except asyncio.CancelledError:
                # 用户通过 stop 取消了任务 — 读取已保存的部分输出
                partial_response = ""
                try:
                    from modules.thinking.core.model_runner import _runner_managers, _runner_managers_lock
                    with _runner_managers_lock:
                        rm = _runner_managers.get(session_id)
                    if rm and rm.blackboard:
                        partial_response = rm.blackboard.final_response or ""
                except Exception:
                    pass

                if partial_response:
                    await self._emit(
                        session_id,
                        _build_event(
                            session_id=session_id,
                            msg_type="message",
                            event="assistant_message",
                            content=partial_response,
                            role="large",
                        ),
                        callback,
                    )
                await self._emit(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="done",
                        event="stopped",
                        content="思考已停止（已保存部分输出）",
                        role="system",
                    ),
                    callback,
                )
                return "stopped"

            module_results = result.get("module_results", [])
            emitted_thinking = streamed_stage_count > 0

            if not emitted_thinking:
                for mr in module_results:
                    module_name = mr.get("module", "unknown")
                    success = bool(mr.get("success", False))

                    await self._emit(
                        session_id,
                        _build_event(
                            session_id=session_id,
                            msg_type="status",
                            event="module_result",
                            content=f"模块 {module_name} {'成功' if success else '失败'}",
                            role="system",
                            data={
                                "module": module_name,
                                "success": success,
                                "latency_ms": mr.get("latency_ms", 0),
                                "error": mr.get("error", ""),
                            },
                        ),
                        callback,
                    )

                    out = mr.get("output")
                    if module_name == "thinking" and success and isinstance(out, dict):
                        for step in out.get("thinking_history", []):
                            step_type = step.get("type", "thinking")
                            step_content = step.get("content", "")
                            model = step.get("model", "")
                            if not step_content:
                                continue

                            emitted_thinking = True
                            await self._emit(
                                session_id,
                                _build_event(
                                    session_id=session_id,
                                    msg_type="thinking",
                                    event="thinking_step",
                                    content=step_content,
                                    role="thinking",
                                    data={
                                        "step_type": step_type,
                                        "model": model,
                                        "tool_name": step.get("tool_name", ""),
                                        "tool_params": step.get("tool_params", {}),
                                        "tool_result": str(step.get("tool_result", ""))[:400],
                                    },
                                ),
                                callback,
                            )

            probe_signals = result.get("decisions", {}).get("probe_signals", [])
            for signal in probe_signals:
                await self._emit(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="thinking",
                        event="probe_signal",
                        content=str(signal.get("signal", "")),
                        role="probe",
                        data=signal,
                    ),
                    callback,
                )

            final_response = result.get("response", "")

            import re
            output_mode_match = re.search(r'【输出模式】(\w+)', final_response)
            output_mode = output_mode_match.group(1) if output_mode_match else "output"

            if output_mode_match:
                final_response = final_response[:output_mode_match.start()].strip()

            if not emitted_thinking:
                await self._emit(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="thinking",
                        event="thinking_step",
                        content="调度链执行完成，准备输出最终结果",
                        role="thinking",
                    ),
                    callback,
                )

            await self._append_message(session_id, "assistant", final_response)

            if output_mode == "output":
                await self._emit(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="message",
                        event="assistant_message",
                        content=final_response,
                        role="main",
                        data={"trace_id": result.get("trace_id", ""), "output_mode": output_mode},
                    ),
                    callback,
                )
            else:
                await self._emit(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="thinking",
                        event="silent_thinking",
                        content="模型选择静默思考，不输出给用户",
                        role="thinking",
                        data={"output_mode": output_mode},
                    ),
                    callback,
                )

            # 提取副会话数据（供前端和批量输出使用）
            sub_sessions = []
            for mr in result.get("module_results", []):
                output = mr.get("output", {})
                if isinstance(output, dict) and "sub_sessions" in output:
                    sub_sessions = output["sub_sessions"]
                    break

            await self._emit(
                session_id,
                _build_event(
                    session_id=session_id,
                    msg_type="done",
                    event="done",
                    content="处理完成",
                    role="system",
                    data={
                        "elapsed_ms": result.get("elapsed_ms", 0),
                        "active_modules": result.get("active_modules", []),
                        "focus": result.get("focus", ""),
                        "trace_id": result.get("trace_id", ""),
                        "phase": "done",
                        "sub_sessions": sub_sessions,
                    },
                ),
                callback,
            )

            # T5: 任务完成后30秒异步提取偏好/教训/状态变更
            asyncio.create_task(
                self._post_task_extraction(session_id, user_input, final_response)
            )

            # ===== 新增：自动提取用户记忆 =====
            try:
                from modules.memory.core.memory_extractor import get_memory_extractor
                from modules.memory.core.memory_manager import MemoryManager

                mm = MemoryManager()
                mm.set_session_id(session_id)
                extractor = get_memory_extractor(mm)
                extractor.extract_from_dialog(user_input, final_response)
            except Exception as e:
                logger.debug(f"自动记忆提取失败: {e}")
            # ==================================

            return final_response

        except Exception as e:
            logger.error(f"思考流程失败: {e}")
            await self._emit(
                session_id,
                _build_event(
                    session_id=session_id,
                    msg_type="error",
                    event="error",
                    content="思考处理过程中出现内部错误",
                    role="system",
                    data={
                        "phase": "error",
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    },
                ),
                callback,
            )
            return ""
        finally:
            try:
                from modules.thinking.communication import get_message_bus
                get_message_bus().set_event_emitter(None)
            except Exception as e:
                logger.debug(f"[消息总线] 清理事件发射器失败 (非致命): {e}")
            await self._set_processing(session_id, False)

    async def _post_task_extraction(
            self, session_id: str, user_input: str, final_response: str
    ):
        """T5: 任务完成后提取偏好/教训/状态变更并写入记忆 (不阻塞)"""
        await asyncio.sleep(30)
        try:
            from modules.memory.core.post_task_memory_extractor import PostTaskMemoryExtractor
            from modules.memory.core.memory_manager import MemoryManager

            mm = MemoryManager()
            mm.set_session_id(session_id)
            extractor = PostTaskMemoryExtractor(mm)

            conversation = self.get_context(session_id)
            if not conversation:
                return

            written = await extractor.extract_and_write(
                conversation=conversation,
                user_input=user_input,
                final_response=final_response,
                session_id=session_id,
            )
            if written:
                logger.info(f"[T5] 任务后记忆已写入: {written}")
        except Exception as e:
            logger.debug(f"[T5] 任务后记忆提取失败 (非致命): {e}")

    def get_context(self, session_id: str) -> List[Dict[str, Any]]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        return session.get("messages", [])

    def get_status(self) -> Dict[str, Any]:
        running_sessions = sum(1 for s in self.sessions.values() if s.get("running"))
        return {
            "running": self._running,
            "sessions": len(self.sessions),
            "running_sessions": running_sessions,
        }


_thinking_system: Optional[StreamThinkingSystem] = None
_thinking_system_lock = threading.Lock()


def get_thinking_system() -> StreamThinkingSystem:
    global _thinking_system
    if _thinking_system is None:
        with _thinking_system_lock:
            if _thinking_system is None:
                _thinking_system = StreamThinkingSystem()
    return _thinking_system


async def initialize_system():
    """初始化流式思考系统"""
    return get_thinking_system()


@router.post("/session")
async def create_session():
    """创建流式会话"""
    system = get_thinking_system()
    session_id = await system.create_session()
    return {"success": True, "data": {"session_id": session_id}}


@router.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    """
    WebSocket 实时聊天

    客户端发送：{"type":"input","content":"..."}
    服务端返回：统一 envelope
    """
    await connection_manager.connect(session_id, websocket)

    system = get_thinking_system()
    await system.start(session_id)

    await connection_manager.send_json(
        session_id,
        _build_event(
            session_id=session_id,
            msg_type="ack",
            event="session_ready",
            content="WebSocket 会话已建立",
            role="system",
        ),
    )

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg_data = json.loads(data)
            except json.JSONDecodeError:
                msg_data = {"type": "input", "content": data}

            msg_type = msg_data.get("type", "input")

            if msg_type == "input":
                user_content = msg_data.get("content", "")
                if user_content:
                    asyncio.create_task(system.think(session_id, user_content))

            elif msg_type == "stop":
                await system.stop(session_id)
                await connection_manager.send_json(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="done",
                        event="stopped",
                        content="会话已停止",
                        role="system",
                    ),
                )
                # 不 break — 保持 WebSocket 连接，允许后续发送新消息

            elif msg_type == "ping":
                await connection_manager.send_json(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="ack",
                        event="pong",
                        content="pong",
                        role="system",
                    ),
                )

            elif msg_type == "security_response":
                request_id = msg_data.get("request_id", "")
                approved = msg_data.get("approved", False)
                reason = msg_data.get("reason", "")
                if request_id:
                    try:
                        from modules.security_system.tool_security_gate import ToolSecurityGate
                        ToolSecurityGate.resolve_review(request_id, approved, reason)
                    except Exception as e:
                        logger.warning(f"[安全审查] 响应处理失败: {e}")

            elif msg_type == "interactive_response":
                request_id = msg_data.get("request_id", "")
                if request_id:
                    try:
                        from modules.thinking.core.model_runner import _runner_managers, _runner_managers_lock
                        resolved = False
                        with _runner_managers_lock:
                            managers = list(_runner_managers.values())
                        for mgr in managers:
                            response_data = {k: v for k, v in msg_data.items() if k != "type"}
                            if mgr.resolve_user_response(request_id, response_data):
                                resolved = True
                                logger.info(f"[交互响应] request_id={request_id} 已路由")
                                break
                        if not resolved:
                            logger.warning(f"[交互响应] 未找到等待中的 request_id={request_id}")
                    except Exception as e:
                        logger.warning(f"[交互响应] 处理失败: {e}", exc_info=True)
            else:
                await connection_manager.send_json(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="error",
                        event="unsupported_type",
                        content=f"不支持的消息类型: {msg_type}",
                        role="system",
                    ),
                )

    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: {session_id}")
    finally:
        await connection_manager.disconnect(session_id)


async def _stream_sse(session_id: str, question: str):
    queue: asyncio.Queue = asyncio.Queue()

    async def callback(event: Dict[str, Any]):
        await queue.put(event)

    system = get_thinking_system()
    await system.start(session_id)

    task = asyncio.create_task(system.think(session_id, question, callback=callback))

    try:
        while True:
            if task.done() and queue.empty():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
                yield {
                    "event": event.get("event", event.get("type", "message")),
                    "data": json.dumps(event, ensure_ascii=False),
                }
            except asyncio.TimeoutError:
                continue

        await task
    except Exception as e:
        logger.error(f"SSE流失败: {e}")
        err = _build_event(
            session_id=session_id,
            msg_type="error",
            event="error",
            content="流式处理过程中出现内部错误",
            role="system",
        )
        yield {"event": "error", "data": json.dumps(err, ensure_ascii=False)}


@router.get("/sse/{session_id}")
async def sse_session_get(session_id: str, question: str = ""):
    """SSE 流式响应（GET）"""
    if not question:
        raise AppError(ErrorCode.BAD_REQUEST, "question 不能为空")
    return EventSourceResponse(_stream_sse(session_id, question))


@router.post("/sse/{session_id}")
async def sse_session_post(session_id: str, question: str = ""):
    """SSE 流式响应（POST，兼容旧客户端）"""
    if not question:
        raise AppError(ErrorCode.BAD_REQUEST, "question 不能为空")
    return EventSourceResponse(_stream_sse(session_id, question))


@router.get("/context/{session_id}")
async def get_context(session_id: str):
    """获取会话上下文（兼容 CLI: /stream/context/{session_id}）"""
    system = get_thinking_system()
    messages = system.get_context(session_id)
    return {
        "success": True,
        "data": {
            "session_id": session_id,
            "messages": messages,
            "count": len(messages),
        }
    }


@router.delete("/session/{session_id}")
async def close_session(session_id: str):
    """关闭会话"""
    system = get_thinking_system()
    await system.stop(session_id)
    return {"success": True, "data": {"message": "会话已关闭"}}


@router.get("/status")
async def get_status():
    """获取系统状态"""
    system = get_thinking_system()
    return {"success": True, "data": system.get_status()}
