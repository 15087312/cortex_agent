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
            # 同 session 已有连接（重复连接/会话接管）：先关闭旧连接，
            # 避免"顶掉原连接"后旧连接仍能继续收发消息（会话劫持残留）
            old = self.active_connections.get(session_id)
            if old is not None and old is not websocket:
                # fire-and-forget 关闭：不在锁内 await（starlette close 会等待对端确认，
                # 旧客户端不响应会永久阻塞锁 → 整个连接管理器瘫痪）
                try:
                    asyncio.create_task(self._close_old_connection(old))
                except Exception:
                    pass
            self.active_connections[session_id] = websocket
            self._loop = asyncio.get_running_loop()

    async def _close_old_connection(self, old: WebSocket):
        """关闭被接管的旧连接（带超时，fire-and-forget）"""
        try:
            await asyncio.wait_for(
                old.close(code=4000, reason="session 已被新连接接管"),
                timeout=2.0,
            )
        except Exception:
            pass

    async def disconnect(self, session_id: str):
        async with self._lock:
            if session_id in self.active_connections:
                del self.active_connections[session_id]

    async def send_json(self, session_id: str, data: dict):
        async with self._lock:
            websocket = self.active_connections.get(session_id)
            if websocket:
                try:
                    await websocket.send_json(data)
                except Exception:
                    # 连接已关闭/客户端断开（如 voice 会话瞬时连接）→ 移除残留连接，避免重复报错
                    self.active_connections.pop(session_id, None)

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
                    try:
                        await ws.send_json(data)
                        return True
                    except Exception:
                        # 连接已关闭 → 移除残留连接
                        self.active_connections.pop(session_id, None)
                        return False
            return False

        # 已在事件循环线程上调用（如模型流式推理内 _push_reasoning）：不能再走
        # run_coroutine_threadsafe + future.result() 同步阻塞——否则会阻塞事件循环自身，
        # _send 永远无法被调度执行，5s 后超时（TimeoutError，空消息），对话即报错。
        # 改为调度到循环异步执行（fire-and-forget），不阻塞当前协程。
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is not None and current_loop is self._loop:
            async def _fire_and_forget():
                try:
                    await _send()
                except BaseException:
                    pass
            asyncio.create_task(_fire_and_forget())
            return True

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


def _ws_auth_ok(websocket: WebSocket) -> bool:
    """WebSocket 握手鉴权（与 HTTP 中间件 api_key_middleware 行为一致）。

    - SIMPLE_API_KEY 未配置（开发模式）→ 放行
    - 已配置 → 校验 X-API-Key header 或 ?api_key= 查询参数（hmac 常量时间比较）

    浏览器原生 WebSocket 无法设置自定义 header，故前端走 query 参数；
    CLI（aiohttp）带 X-API-Key header。
    """
    try:
        from config.settings import settings as _cfg
        expected = getattr(_cfg, "SIMPLE_API_KEY", "") or ""
    except Exception:
        expected = ""
    if not expected:
        return True
    import hmac
    header_key = websocket.headers.get("x-api-key") or ""
    query_key = websocket.query_params.get("api_key") or ""
    if header_key and hmac.compare_digest(header_key, expected):
        return True
    if query_key and hmac.compare_digest(query_key, expected):
        return True
    return False


# ── model_id 到显示名称的解析缓存 ──
_identity_name_cache: Dict[str, str] = {}

def _resolve_identity_name(model_id: str) -> str:
    """从 model_id（如 supervisor_code_001）解析显示名称（如 代码主管）"""
    import re
    if not model_id:
        return ""
    if model_id in _identity_name_cache:
        return _identity_name_cache[model_id]

    # 去除尾部 _number（如 _001、_002）
    base = re.sub(r'_\d+$', '', model_id)

    try:
        from modules.thinking.identity import get_identities
        identities = get_identities()
        # 先尝试直接用 key 查（model_id 即 key 或 base 即 key 的情况）
        identity = identities.get(base)
        if identity:
            name = identity.get("name", "")
            if name:
                _identity_name_cache[model_id] = name
                return name
        # 反向匹配：身份模板的 model_id 字段去掉尾部编号后与 base 相等
        # （roles.yaml 中 key=code_supervisor，但 model_id=supervisor_code_001）
        for key, ident in identities.items():
            tmid = ident.get("model_id", "")
            if not tmid:
                continue
            tbase = re.sub(r'_\d+$', '', tmid)
            if tbase == base:
                name = ident.get("name", "")
                _identity_name_cache[model_id] = name
                return name
    except Exception as e:
        logger.debug("获取模型身份名称失败: %s", e)
    _identity_name_cache[model_id] = ""
    return ""


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
        self._session_repo = None  # 延迟初始化

    def _get_session_repo(self):
        if self._session_repo is None:
            try:
                from modules.database.session_repo import get_session_repo
                self._session_repo = get_session_repo()
            except Exception as e:
                logger.debug(f"[SessionRepo] 初始化失败 (非致命): {e}")
        return self._session_repo

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
                    "_last_extracted": 0,  # 上次提取事件时处理到的消息数
                }
                # 从 SQLite 恢复历史消息（重连场景）
                repo = self._get_session_repo()
                if repo:
                    try:
                        recent = repo.get_recent_messages(session_id, limit=50)
                        if recent:
                            # 过滤思考步骤（thought）与心理活动（mental），只恢复真实对话，避免污染模型上下文
                            recent = [m for m in recent if m["role"] not in ("thought", "mental")]
                            self.sessions[session_id]["messages"] = [
                                {"role": m["role"], "content": m["content"], "timestamp": 0,
                                 "id": m.get("id", "")}
                                for m in recent
                            ]
                            logger.info(f"[SessionRepo] 恢复 {len(recent)} 条历史消息: session={session_id[:8]}")
                    except Exception as e:
                        logger.debug(f"[SessionRepo] 恢复消息失败: {e}")
            else:
                self.sessions[session_id]["running"] = True
            self.sessions[session_id]["started_at"] = time.time()
        self._running = True

        # 持久化会话到 SQLite
        repo = self._get_session_repo()
        if repo:
            try:
                repo.create_session(session_id)
            except Exception as e:
                logger.debug(f"[SessionRepo] 创建会话记录失败: {e}")

        # SessionLifecycle 由 orchestrator 内部创建，无需提前创建

        # T1: 会话启动预加载核心记忆 (fire-and-forget, 不阻塞)
        asyncio.create_task(self._preload_session_memories(session_id))

    async def _preload_session_memories(self, session_id: str):
        """T1: 会话记忆由 EventReducer 在结束后处理，无需预加载"""
        pass

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

    async def _append_message(self, session_id: str, role: str, content: str, tier: str = "") -> str:
        """追加消息到内存并持久化，返回消息 ID"""
        msg_id = ""
        msg_index = -1
        async with self._lock:
            if session_id not in self.sessions:
                return ""
            msgs = self.sessions[session_id]["messages"]
            # user 消息去重：busy 时已保存、retry 重发同内容 → 不重复入库（保证会话历史唯一）
            if role == "user" and msgs and msgs[-1].get("role") == "user" and msgs[-1].get("content") == content:
                return msgs[-1].get("id", "")
            msgs.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": time.time(),
                    "id": "",
                }
            )
            self.sessions[session_id]["messages"] = msgs[-200:]
            msg_index = len(self.sessions[session_id]["messages"]) - 1

        # 持久化到 SQLite
        repo = self._get_session_repo()
        if repo:
            try:
                msg_id = repo.save_message(session_id, role, content, tier=tier)
            except Exception as e:
                logger.debug(f"[SessionRepo] 保存消息失败: {e}")
        if msg_id and session_id in self.sessions and msg_index >= 0:
            # 按索引回填消息 ID（避免并发追加时误改最后一条）
            async with self._lock:
                msgs = self.sessions[session_id].get("messages", [])
                if msg_index < len(msgs):
                    msgs[msg_index]["id"] = msg_id
        return msg_id

    async def _persist_thought(self, session_id: str, content: str, tier: str = "") -> None:
        """持久化思考步骤到 DB（供前端切换会话后恢复展示）。

        只写 SQLite、不写入内存 messages —— 内存 messages 用于组装模型上下文
        （think() 的 context / 【对话历史】），思考步骤不应污染 AI 看到的对话。
        """
        repo = self._get_session_repo()
        if not repo:
            return
        try:
            repo.save_message(session_id, "thought", content, tier=tier)
        except Exception as e:
            logger.debug(f"[SessionRepo] 保存思考步骤失败: {e}")

    async def _proactive_context_trim(self, session_id: str):
        """水位线渐进裁剪 — 消息超窗口 80% 时丢弃最旧的 50%"""
        session = self.sessions.get(session_id)
        if not session:
            return
        messages = session.get("messages", [])
        if len(messages) < 4:
            return
        try:
            from config.settings import settings
            window_size = settings.CONTEXT_WINDOW_SIZE
        except Exception:
            window_size = 128000
        threshold = int(window_size * 0.8)

        total_content = "\n".join(
            m.get("content", "") for m in messages
        )
        from modules.thinking.context.compression import get_compression_engine
        engine = get_compression_engine()
        estimated = engine.estimate_tokens(total_content)

        if estimated <= threshold:
            return

        keep_count = max(len(messages) // 2, 2)
        kept = messages[-keep_count:]
        dropped = len(messages) - keep_count
        async with self._lock:
            session["messages"] = kept
        logger.info(
            f"[上下文] 消息水位 {estimated} tokens > {threshold} (80%)，"
            f"丢弃最旧 {dropped} 条，保留最新 {keep_count} 条"
        )

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

    def _format_scheduler_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event_type = event.get("type") or event.get("event_type", "event")
        action = event.get("action", "")
        target = event.get("target", "")
        source = event.get("source", "")
        success = bool(event.get("success", True))
        dialog_tier = ""
        model_id = ""
        identity_name = ""
        tier_labels = {"large": "[总指挥]", "supervisor": "[主管]", "expert": "[专家]", "user": "[用户]"}

        if event_type == "tool_call":
            content = f"工具 {target} {action} {'成功' if success else '失败'}"
        elif event_type == "model_comm":
            payload = event.get("payload", {})
            metadata = payload.get("metadata", {})
            msg_type = payload.get("msg_type", "")
            tier = metadata.get("tier") or payload.get("tier", payload.get("sender_tier", "unknown"))
            payload.get("phase", "comm")
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
                # model_id 和 metadata 在 raw_content（payload.content）里
                model_id = raw_content.get("model_id", "") if isinstance(raw_content, dict) else ""
                entry_meta = raw_content.get("metadata", {}) if isinstance(raw_content, dict) else {}
                identity_name = _resolve_identity_name(model_id)
                if identity_name:
                    label = f"[{identity_name}]"
                else:
                    label = tier_labels.get(dialog_tier, f"[{dialog_tier}]")
                # 如果有所属主管（expert 由 supervisor 委托），显示上级名称
                return_to_model_id = entry_meta.get("return_to_model_id", "")
                if return_to_model_id and dialog_tier == "expert":
                    parent_name = _resolve_identity_name(return_to_model_id)
                    if parent_name:
                        label = f"[{parent_name}→{identity_name}]"
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
        # 身份信息：供前端渲染不同的头像/名字（如 代码主管 / 总指挥）
        if event_type == "model_comm":
            result_data["model_id"] = model_id
            if identity_name:
                result_data["identity_name"] = identity_name
            else:
                result_data["identity_name"] = tier_labels.get(dialog_tier, dialog_tier) if dialog_tier else ""
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
            # busy：不再丢弃，保存用户消息保证会话历史完整（_append_message 对连续重复去重）
            busy_msg_id = ""
            try:
                busy_msg_id = await self._append_message(session_id, "user", user_input)
            except Exception:
                pass
            await self._emit(
                session_id,
                _build_event(
                    session_id=session_id,
                    msg_type="ack",
                    event="busy",
                    content="会话正在处理中，请稍后",
                    role="system",
                    data={"message_id": busy_msg_id},
                ),
                callback,
            )
            return ""

        await self._set_processing(session_id, True)
        user_msg_id = await self._append_message(session_id, "user", user_input)

        try:
            await self._emit(
                session_id,
                _build_event(
                    session_id=session_id,
                    msg_type="ack",
                    event="received",
                    content="已接收请求，开始处理",
                    role="system",
                    data={"message_id": user_msg_id},
                ),
                callback,
            )

            await self._proactive_context_trim(session_id)
            context_messages = self.get_context(session_id)
            short_term_memory = [m.get("content", "") for m in context_messages[-6:]]
            scheduler_context = [
                {"role": m.get("role", ""), "content": m.get("content", ""), "timestamp": m.get("timestamp", 0.0)}
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

            async with self._lock:
                session_data = self.sessions.get(session_id, {})
                model_id = session_data.get("model_id", "large_primary")

            # 统一握手：LLM 前确认前端可达（对话级）。前端断线瞬间跳过本轮调用，避免白耗 token。
            try:
                from modules.thinking.frontend_channel import confirm_frontend_connection
                if not confirm_frontend_connection(session_id):
                    await self._emit(
                        session_id,
                        _build_event(
                            session_id=session_id,
                            msg_type="error",
                            event="connection_lost",
                            content="前端连接已断开，本轮请求已跳过，可在重连后重试",
                            role="system",
                        ),
                        callback,
                    )
                    return ""
            except Exception as e:
                logger.debug(f"[api_stream] 对话握手失败 (非致命): {e}")

            scheduler_task = asyncio.create_task(
                self._orchestrator.process(
                    user_input,
                    scheduler_context,
                    short_term_memory,
                    scheduler_event_callback,
                    session_id,
                    model_id=model_id,
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
                    active_supervisors = []
                    large_model_info = {}
                    context_tokens = 0
                    context_window_size = 128000
                    try:
                        from modules.thinking.core.model_runner import _runner_managers, _runner_managers_lock
                        with _runner_managers_lock:
                            rm = _runner_managers.get(session_id)
                        if rm:
                            for runner_info in rm.list_runners():
                                tier = runner_info.get("tier", "")
                                running = runner_info.get("running", False)
                                name = runner_info.get("name") or runner_info.get("role", "")
                                role = runner_info.get("role", "")
                                model_id = runner_info.get("model_id", "")
                                # 大模型身份
                                if tier == "large":
                                    if running:
                                        context_tokens = runner_info.get("context_tokens", 0)
                                        context_window_size = runner_info.get("context_window_size", 128000)
                                    large_model_info = {
                                        "name": name,
                                        "role": role,
                                        "model_id": model_id,
                                        "active_skill": runner_info.get("active_skill", ""),
                                        "status": runner_info.get("status", ""),
                                        "status_detail": runner_info.get("status_detail", ""),
                                        "round": runner_info.get("round", 0),
                                        "max_turns": runner_info.get("max_turns", 0),
                                        "react_loop": runner_info.get("react_loop"),
                                        "think_loop": runner_info.get("think_loop"),
                                        "last_thought": runner_info.get("last_thought", ""),
                                    }
                                # 主管
                                elif tier == "supervisor" and running:
                                    active_supervisors.append({
                                        "name": name,
                                        "role": role,
                                        "model_id": model_id,
                                        "status": runner_info.get("status", ""),
                                        "status_detail": runner_info.get("status_detail", ""),
                                        "round": runner_info.get("round", 0),
                                        "max_turns": runner_info.get("max_turns", 0),
                                        "react_loop": runner_info.get("react_loop"),
                                        "think_loop": runner_info.get("think_loop"),
                                        "last_thought": runner_info.get("last_thought", ""),
                                    })
                                # 专家（带上所属主管）
                                elif tier == "expert" and running:
                                    # 从 runner 的 supervisor_chain 获取所属主管
                                    supervisor = runner_info.get("supervisor", "")
                                    active_experts.append({
                                        "name": name,
                                        "role": role,
                                        "model_id": model_id,
                                        "supervisor": supervisor,
                                        "status": runner_info.get("status", ""),
                                        "status_detail": runner_info.get("status_detail", ""),
                                        "round": runner_info.get("round", 0),
                                        "max_turns": runner_info.get("max_turns", 0),
                                        "react_loop": runner_info.get("react_loop"),
                                        "think_loop": runner_info.get("think_loop"),
                                        "last_thought": runner_info.get("last_thought", ""),
                                    })
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
                                "active_supervisors": active_supervisors,
                                "large_model": large_model_info,
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
                    # 会话执行图谱：记录"谁呼唤谁 / 谁回复谁"（model_comm 发言，用户输入不记录）
                    try:
                        _d = formatted.get("data") or {}
                        if _d.get("event_type") == "model_comm" and _d.get("dialog_tier") != "user":
                            import time as _time
                            _payload = _d.get("payload") or {}
                            _raw = _payload.get("content")
                            _meta = _raw.get("metadata", {}) if isinstance(_raw, dict) else {}
                            from modules.thinking.session_graph import get_session_graph_store
                            get_session_graph_store().record(
                                session_id,
                                model_id=_d.get("model_id", ""),
                                identity_name=_d.get("identity_name", ""),
                                tier=_d.get("dialog_tier", ""),
                                return_to_model_id=_meta.get("return_to_model_id", ""),
                                entry_type=_raw.get("entry_type", "") if isinstance(_raw, dict) else "",
                                content=formatted.get("content", ""),
                                ts=_time.time(),
                            )
                    except Exception:
                        pass
                    event_role = formatted["data"].get("dialog_tier", "thinking")
                    # 持久化思考/对话步骤（role=thought），切换会话后仍能恢复展示
                    # 只写 DB、不进内存 messages，避免污染 AI 上下文（见 _persist_thought）
                    try:
                        await self._persist_thought(session_id, formatted["content"], tier=event_role)
                    except Exception:
                        pass
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
                except Exception as e:
                    logger.debug("获取取消后的部分响应失败: %s", e)

                if partial_response:
                    try:
                        await self._append_message(session_id, "assistant", partial_response)
                    except Exception:
                        pass
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
            # 获取刚保存的 assistant 消息 ID（前端删除/修改需要）
            assistant_msg_id = ""
            async with self._lock:
                msgs = self.sessions.get(session_id, {}).get("messages", [])
                if msgs and msgs[-1].get("id"):
                    assistant_msg_id = msgs[-1]["id"]

            if output_mode == "output":
                # 附带本轮思考元数据（内心独白 + 事件记忆），供前端回复下方展开栏展示
                meta = {"inner_monologue": "", "event_memory": "", "conversation_history": ""}
                try:
                    from modules.thinking.probes.probe_tools import _session_guidance
                    g = _session_guidance.get((model_id, session_id), {})
                    meta["inner_monologue"] = g.get("inner_thoughts", "") or ""
                except Exception:
                    pass
                try:
                    from modules.thinking.core.model_runner import _session_memory_context
                    meta["event_memory"] = _session_memory_context.get(session_id, "") or ""
                except Exception:
                    pass
                try:
                    # 附加上一轮实际注入 AI 的【对话历史】原文（前端直接展示，保证一致）
                    from modules.thinking.multi_model_orchestrator import _session_dialog_history, _session_dialog_history_lock
                    with _session_dialog_history_lock:
                        meta["conversation_history"] = _session_dialog_history.get(session_id, "") or ""
                except Exception:
                    pass
                await self._emit(
                    session_id,
                    _build_event(
                        session_id=session_id,
                        msg_type="message",
                        event="assistant_message",
                        content=final_response,
                        role="main",
                        data={"trace_id": result.get("trace_id", ""), "output_mode": output_mode,
                              "message_id": assistant_msg_id, "meta": meta},
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

            # 事件记忆由 _post_task_extraction 在会话结束后处理
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
            # 会话执行图谱持久化到会话 metadata（重启后可恢复）
            try:
                from modules.thinking.session_graph import get_session_graph_store
                from modules.database.session_repo import get_session_repo
                snap = get_session_graph_store().snapshot(session_id)
                get_session_repo().set_session_metadata(session_id, {"session_graph": snap})
            except Exception:
                pass
            await self._set_processing(session_id, False)

    async def _post_task_extraction(
        self, session_id: str, user_input: str, final_response: str, owner_id: str = None
    ):
        """任务完成后提取记忆事件（hash 去重，重启安全）
        
        Args:
            session_id: 会话 ID
            user_input: 用户输入
            final_response: 模型回复
            owner_id: 记忆所属模型 ID（如 "large::large_primary", "supervisor::pm_001"）
                      None 时自动从会话消息角色推断
        """
        await asyncio.sleep(30 if owner_id is None else 0)
        try:
            session = self.sessions.get(session_id)
            if not session:
                return

            messages = session.get("messages", [])
            # 取最近 20 条消息构造文本
            parts = []
            for msg in messages[-20:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if content and isinstance(content, str):
                    parts.append(f"{role}: {content}")

            conversation_text = "\n".join(parts)
            if len(conversation_text.strip()) < 50:
                return

            # hash 去重：同一段对话只处理一次
            import hashlib
            text_hash = hashlib.md5(conversation_text.encode()).hexdigest()[:16]
            processed = session.setdefault("_processed_hashes", set())
            if text_hash in processed:
                logger.debug(f"[事件记忆] 会话 {session_id} 已处理过，跳过")
                return
            processed.add(text_hash)

            # 自动推断 owner_id（如果未提供）
            if owner_id is None:
                # 从消息角色中提取模型 ID
                for msg in messages[-10:]:
                    role = msg.get("role", "")
                    # 格式: "assistant::large_primary" 或 "supervisor_code_001"
                    if "::" in role:
                        parts = role.split("::", 1)
                        if len(parts) == 2:
                            owner_id = f"{parts[0]}::{parts[1]}"
                            break
                    elif role.startswith("supervisor_") or role.startswith("expert_"):
                        owner_id = role
                        break
                    elif role.startswith("assistant"):
                        # 尝试从 session 元数据获取 model_id
                        owner_id = session.get("model_id", "large::large_primary")
                        break
                if not owner_id:
                    owner_id = "large::large_primary"

            # 获取 EventReducer（依赖注入）
            from modules.memory.event_reducer import EventReducer
            from modules.memory.event_store import EventStore
            from modules.memory.embedding import EmbeddingEngine
            
            # 尝试创建模型客户端
            model_client = None
            try:
                from infra.model.small_model_client import SmallModelClient
                from config.settings import settings
                model_client = SmallModelClient(
                    api_key=settings.SMALL_MODEL_API_KEY or settings.LARGE_MODEL_API_KEY,
                    api_url=settings.SMALL_MODEL_API_URL or settings.LARGE_MODEL_API_URL,
                )
            except Exception as e:
                logger.debug(f"[事件记忆] 模型客户端创建失败: {e}")
            
            # 使用依赖注入创建 reducer
            reducer = EventReducer(
                model_client=model_client,
                store=EventStore.get_instance(),
                embedder=EmbeddingEngine.get_instance(),
            )
            # 同步到模块级单例，供 Conscience 反馈闭环复用
            if model_client:
                try:
                    from modules.memory.event_reducer import get_reducer
                    get_reducer()._model_client = model_client
                except Exception:
                    pass

            # 记忆总结开关：关闭则跳过自动提炼事件记忆（EventReducer）
            try:
                from config.settings import settings as _cfg
                if not getattr(_cfg, "MEMORY_SUMMARY_ENABLED", True):
                    return
            except Exception:
                pass

            events = await reducer.reduce(session_id, conversation_text, owner_id=owner_id)
            if events:
                logger.info(f"[事件记忆] 会话 {session_id} 提取 {len(events)} 个事件 (hash={text_hash})")
        except Exception as e:
            logger.debug(f"[事件记忆] 后处理失败: {e}")

    def get_context(self, session_id: str) -> List[Dict[str, Any]]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        # 过滤思考步骤（thought），确保模型上下文只含真实对话
        return [m for m in session.get("messages", []) if m.get("role") != "thought"]

    def get_status(self) -> Dict[str, Any]:
        running_sessions = sum(1 for s in self.sessions.values() if s.get("running"))
        return {
            "running": self._running,
            "sessions": len(self.sessions),
            "running_sessions": running_sessions,
        }


_thinking_system: Optional[StreamThinkingSystem] = None
_thinking_system_lock = threading.Lock()

# 主事件循环引用（lifespan 启动时记录）：供后台线程（如主动搭话）跨线程提交协程，
# 避免在无活跃 WS 连接时 connection_manager._loop 为 None 导致持久化走错路径
_main_event_loop: Optional[asyncio.AbstractEventLoop] = None


def get_thinking_system() -> StreamThinkingSystem:
    global _thinking_system
    if _thinking_system is None:
        with _thinking_system_lock:
            if _thinking_system is None:
                _thinking_system = StreamThinkingSystem()
    return _thinking_system


async def initialize_system():
    """初始化流式思考系统"""
    # 记录主事件循环，供后台线程（主动搭话等）跨线程安全提交协程
    global _main_event_loop
    try:
        _main_event_loop = asyncio.get_running_loop()
    except Exception:
        pass
    return get_thinking_system()


async def _post_task_extraction_helper(
    session_id: str, user_input: str, final_response: str, owner_id: str
):
    """供 ModelRunner 调用的记忆提取入口（fire-and-forget）"""
    system = get_thinking_system()
    await system._post_task_extraction(
        session_id=session_id,
        user_input=user_input,
        final_response=final_response,
        owner_id=owner_id,
    )


@router.post("/session")
async def create_session():
    """创建流式会话"""
    system = get_thinking_system()
    session_id = await system.create_session()
    return {"success": True, "data": {"session_id": session_id}}


async def _safe_think(system, session_id: str, user_input: str, callback=None) -> None:
    """包一层 think 调用：调度抛异常时也保存一条错误回复，保证会话历史有记录。

    think 任务通过 asyncio.create_task 独立于 WebSocket/SSE 连接运行，
    连接断开不影响处理；此处兜底异常，避免"思考失败但会话无任何记录"。
    """
    try:
        await system.think(session_id, user_input, callback=callback)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[think] 调度失败: {e}")
        error_text = f"[模型调用失败: {e}]"
        try:
            await system._append_message(session_id, "assistant", error_text)
        except Exception:
            pass
        try:
            await system._emit(
                session_id,
                _build_event(
                    session_id=session_id,
                    msg_type="error",
                    event="think_error",
                    content=error_text,
                    role="system",
                ),
                callback,
            )
        except Exception:
            pass


@router.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    """
    WebSocket 实时聊天

    客户端发送：{"type":"input","content":"..."}
    服务端返回：统一 envelope

    鉴权：与 HTTP 中间件一致（X-API-Key header 或 ?api_key= 查询参数，
    未配置 SIMPLE_API_KEY 的开发模式放行）。
    """
    if not _ws_auth_ok(websocket):
        await websocket.close(code=4401, reason="未授权访问：缺少或无效的 API Key")
        return
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
                attachments = msg_data.get("attachments") or []
                if user_content or attachments:
                    # 附件（图片→视觉描述 / 文件→内容）注入上下文
                    if attachments:
                        from modules.thinking.attachment_handler import validate_attachments
                        att_err = validate_attachments(attachments)
                        if att_err:
                            await connection_manager.send_json(
                                session_id,
                                _build_event(
                                    session_id=session_id,
                                    msg_type="error",
                                    event="attachment_error",
                                    content=f"附件格式错误: {att_err}",
                                    role="system",
                                ),
                            )
                            continue
                        try:
                            from modules.thinking.attachment_handler import (
                                parse_attachments, extract_images, summarize_attachments,
                            )
                            from config.settings import settings
                            # 直连模式：图片直接附给大模型（不调独立视觉模型）
                            if str(getattr(settings, "CHAT_IMAGE_MODE", "describe") or "describe").lower() == "direct":
                                att_images = extract_images(attachments)
                                att_text = summarize_attachments(attachments)
                            else:
                                att_images = []
                                att_text = await parse_attachments(attachments)
                            if att_text:
                                user_content = (user_content + "\n\n" + att_text) if user_content else att_text
                        except Exception as e:
                            logger.warning(f"附件解析失败: {e}")
                            att_images = []
                    else:
                        att_images = []
                    # 执行模式禁止经 WS 注入（无鉴权旁路会绕过 PUT /config 的认证）。
                    # 模式切换必须走 PUT /config/EXECUTION_MODE（CLI 的 _set_execution_mode 已按此实现）。

                    # 当前回合图片（直连多模态）：透传给本轮大模型请求
                    from modules.thinking.turn_images import set_turn_images
                    set_turn_images(att_images or None)

                    asyncio.create_task(_safe_think(system, session_id, user_content))

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
        # 前端断开 → 自动拒绝该会话所有待审批（工具审批/模式切换）与待交互
        # （ask_user_intent）：防止审批 future 永久挂起 + Suspension 全局计时冻结
        try:
            from modules.security_system.tool_security_gate import ToolSecurityGate
            ToolSecurityGate.reject_session_reviews(session_id)
        except Exception as e:
            logger.debug(f"[WS] 清理待审批失败 (非致命): {e}")
        try:
            from modules.thinking.core.model_runner import reject_session_user_responses
            reject_session_user_responses(session_id)
        except Exception as e:
            logger.debug(f"[WS] 清理待交互失败 (非致命): {e}")


async def _stream_sse(session_id: str, question: str):
    queue: asyncio.Queue = asyncio.Queue()

    async def callback(event: Dict[str, Any]):
        await queue.put(event)

    system = get_thinking_system()
    await system.start(session_id)

    task = asyncio.create_task(_safe_think(system, session_id, question, callback=callback))

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
    """关闭并删除会话（同时清理内存与数据库）"""
    system = get_thinking_system()
    await system.stop(session_id)
    async with system._lock:
        system.sessions.pop(session_id, None)
    repo = system._get_session_repo()
    if repo:
        try:
            repo.delete_session(session_id)
        except Exception as e:
            logger.debug(f"[SessionRepo] 删除会话失败: {e}")
    return {"success": True, "data": {"message": "会话已删除"}}


@router.put("/session/{session_id}/title")
async def update_session_title(session_id: str, body: dict = None):
    """重命名会话标题"""
    title = ((body or {}).get("title") or "").strip()
    if not title:
        raise AppError(ErrorCode.BAD_REQUEST, "标题不能为空")
    system = get_thinking_system()
    repo = system._get_session_repo()
    if repo:
        try:
            repo.set_session_title(session_id, title[:200])
        except Exception as e:
            logger.debug(f"[SessionRepo] 设置标题失败: {e}")
    return {"success": True, "data": {"message": "标题已更新", "title": title[:200]}}


@router.delete("/sessions/{session_id}/messages/{message_id}")
async def delete_message(session_id: str, message_id: str):
    """删除单条消息（同步更新数据库与 AI 上下文）"""
    system = get_thinking_system()
    # 先从内存（AI 可见上下文）移除
    async with system._lock:
        msgs = system.sessions.get(session_id, {}).get("messages", [])
        len(msgs)
        system.sessions[session_id]["messages"] = [
            m for m in msgs if m.get("id") != message_id
        ]
    # 再从数据库删除
    repo = system._get_session_repo()
    if repo:
        try:
            repo.delete_message(session_id, message_id)
        except Exception as e:
            logger.debug(f"[SessionRepo] 删除消息失败: {e}")
    return {
        "success": True,
        "data": {"message": "消息已删除", "removed": True},
    }


@router.put("/sessions/{session_id}/messages/{message_id}")
async def update_message(session_id: str, message_id: str, body: dict = None):
    """修改单条消息内容（同步更新数据库与 AI 上下文）"""
    content = ((body or {}).get("content") or "").strip()
    if not content:
        raise AppError(ErrorCode.BAD_REQUEST, "内容不能为空")
    system = get_thinking_system()
    # 更新内存（AI 可见上下文）
    async with system._lock:
        msgs = system.sessions.get(session_id, {}).get("messages", [])
        for m in msgs:
            if m.get("id") == message_id:
                m["content"] = content
    # 更新数据库
    repo = system._get_session_repo()
    if repo:
        try:
            repo.update_message(session_id, message_id, content)
        except Exception as e:
            logger.debug(f"[SessionRepo] 更新消息失败: {e}")
    return {
        "success": True,
        "data": {"message": "消息已更新", "content": content},
    }


@router.get("/status")
async def get_status():
    """获取系统状态"""
    system = get_thinking_system()
    return {"success": True, "data": system.get_status()}


@router.get("/sessions")
async def get_sessions():
    """列出所有会话（含历史）"""
    system = get_thinking_system()
    repo = system._get_session_repo()

    # 合并内存中的活跃会话状态
    async with system._lock:
        live_ids = set(system.sessions.keys())

    # 真正"正在使用"的会话 = 有活跃 WebSocket 连接的会话
    active_ws = set(connection_manager.active_connections.keys())

    # 从 DB 获取历史会话
    db_sessions = []
    if repo:
        try:
            # 自动清理无消息的空会话：
            # 仅保留有活跃 WS 连接或最近活跃的空会话，其余（含内存里挂着的旧空会话）删除
            repo.delete_empty_sessions(
                exclude_ids=list(active_ws),
                min_idle_minutes=10,
            )
        except Exception as e:
            logger.debug(f"[SessionRepo] 清理空会话失败: {e}")
        try:
            db_sessions = repo.get_all_sessions(limit=50)
        except Exception as e:
            logger.debug(f"[SessionRepo] 查询会话失败: {e}")

    for s in db_sessions:
        s["is_live"] = s["session_id"] in live_ids

    return {"success": True, "data": db_sessions}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, limit: int = 100):
    """获取会话历史消息（DB 优先，内存兜底保证切换会话不丢）"""
    repo = get_thinking_system()._get_session_repo()
    if not repo:
        return {"success": True, "data": []}
    try:
        messages = repo.get_messages(session_id, limit=limit)
        if messages:
            return {"success": True, "data": messages}
    except Exception as e:
        logger.debug(f"[SessionRepo] 读取消息失败，尝试内存兜底: {e}")
    # 兜底：DB 无记录（保存失败的极端场景）时用后端内存消息，避免切回会话内容丢失
    try:
        sys_inst = get_thinking_system()
        mem = sys_inst.sessions.get(session_id, {}).get("messages", [])
        data = [
            {
                "id": m.get("id", ""),
                "role": m.get("role", ""),
                "content": m.get("content", ""),
                "created_at": m.get("timestamp", 0),
            }
            for m in mem[-limit:]
        ]
        return {"success": True, "data": data}
    except Exception:
        return {"success": True, "data": []}


@router.post("/stop")
async def stop_thinking(body: dict = None, session_id: str = ""):
    """停止当前思考（HTTP 入口，内部转发到 WebSocket stop）"""
    session_id = session_id or (body or {}).get("session_id", "")
    system = get_thinking_system()
    if session_id:
        await system.stop(session_id)
    else:
        # 停止所有活跃会话
        async with system._lock:
            for sid in list(system.sessions.keys()):
                await system.stop(sid)
    return {"success": True, "data": {"message": "已发送停止信号"}}
