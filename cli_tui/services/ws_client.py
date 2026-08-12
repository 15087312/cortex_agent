"""WebSocket 客户端 — 与后端 stream API 通信

架构: Single Producer / Switchable Consumer
  _receive_loop (唯一调 _ws.receive()) → asyncio.Queue → receive_event()
                                                              ↑
                                                    consumer == "background" → _background_receive_loop
                                                    consumer == "processing" → process_input
"""

import asyncio
import json
import time
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional

import aiohttp

from utils.logger import get_logger


async def _safe_wait_for(coro, timeout):
    """替代 asyncio.wait_for，兜底 Python 3.13 任务上下文检查异常。"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except RuntimeError as e:
        if "Timeout context manager" in str(e):
            return await coro
        raise

logger = get_logger("ws_client")

EventCallback = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]

TIER_COLORS = {
    "large": "bold blue",
    "supervisor": "bold yellow",
    "expert": "bold green",
    "user": "bold cyan",
    "thinking": "italic magenta",
    "system": "dim",
}
TIER_ICONS = {"large": "🧠", "supervisor": "📊", "expert": "🔧", "user": "👤", "thinking": "💭", "system": "⚙️"}
TIER_LABELS = {"large": "总指挥", "supervisor": "主管", "expert": "专家", "user": "用户", "thinking": "思考", "system": "系统"}
REFLECTION_ICONS = {"retry": "🔄", "rollback": "↩️", "terminate": "🛑", "ask_user": "❓"}


class WSClient:
    """WebSocket 客户端，管理连接、发送、接收和事件分发"""

    def __init__(self, api_url: str = "http://localhost:8080", api_key: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.session_id: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._running = False
        self._cancel_flag: bool = False
        self._event_callbacks: List[EventCallback] = []
        self._bg_listener_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._incoming: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._consumer: str = "idle"  # "idle" | "background" | "processing"

        # 数据收集
        self.dialog_entries: List[Dict[str, Any]] = []
        self._max_entries = 100
        self.tool_calls: List[Dict[str, Any]] = []
        self.tool_stats = {"total": 0, "success": 0, "failed": 0, "total_latency_ms": 0.0}
        self.final_response = ""
        self.elapsed_ms = 0.0
        self.trace_id = ""
        self.reflection_events: List[Dict[str, Any]] = []

    def on_event(self, callback: EventCallback):
        """注册事件回调"""
        self._event_callbacks.append(callback)

    async def _emit(self, event: Dict[str, Any]):
        """调用所有注册的回调"""
        for cb in self._event_callbacks:
            try:
                await cb(event)
            except Exception as e:
                logger.debug("Event callback failed: %s", e)

    # ── HTTP ──

    def _make_headers(self) -> Dict[str, str]:
        """构建带认证的请求头"""
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _check_api(self) -> bool:
        try:
            async with aiohttp.ClientSession(headers=self._make_headers(), trust_env=True) as s:
                async with s.get(f"{self.api_url}/health", ssl=False) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.debug("API health check failed (degradation): %s", e)
            return False

    async def _create_session(self) -> Optional[str]:
        try:
            async with aiohttp.ClientSession(headers=self._make_headers(), trust_env=True) as s:
                async with s.post(f"{self.api_url}/stream/session", ssl=False) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("data", {}).get("session_id", str(uuid.uuid4()))
        except Exception as e:
            logger.debug("Session creation failed, falling back to local UUID: %s", e)
        return str(uuid.uuid4())

    # ── 接收循环（唯一对 _ws.receive() 的调用者）──

    async def _receive_loop(self):
        """持续读取 _ws.receive()，解析后入队 _incoming。
        连接生命周期内始终运行，异常退出后不再重启（由 connect / process_input 处理）。"""
        try:
            while self._running and self._ws and not self._ws.closed:
                try:
                    msg = await self._ws.receive()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    await self._incoming.put({"type": "error", "content": str(e)})
                    break

                event = None
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        event = json.loads(msg.data)
                    except Exception as e:
                        logger.debug("Failed to parse WS message as JSON: %s", e)
                        event = {"type": "error", "content": str(msg.data)[:200]}
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    event = {"type": "error", "content": "连接已关闭"}
                    break

                if event:
                    await self._incoming.put(event)
        except asyncio.CancelledError:
            pass
        finally:
            logger.debug("Receive loop ended")

    async def _start_receive_loop(self):
        """启动接收循环（使用新队列，旧 receive_task 先取消）。"""
        await self._stop_task(self._receive_task)
        self._incoming = asyncio.Queue(maxsize=1000)
        self._receive_task = asyncio.ensure_future(self._receive_loop())

    @staticmethod
    async def _stop_task(task: Optional[asyncio.Task], timeout: float = 3.0):
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    # ── Consumer 交接（不依赖取消协调）──

    async def _acquire_consumer(self, consumer: str):
        """切换 consumer。从 background 切换时取消背景循环，防止并发消费队列。"""
        old = self._consumer
        self._consumer = consumer
        if old == "background" and consumer != "background":
            await self._stop_task(self._bg_listener_task)
            self._bg_listener_task = None

    def _release_consumer(self):
        """释放 consumer。调用者（process_input 结束后）应视情况重启 background。"""
        self._consumer = "idle"

    # ── WebSocket ──

    async def connect(self, session_id: str = "") -> bool:
        """建立 WebSocket 连接

        Args:
            session_id: 已有会话 ID，为空则创建新会话
        """
        if not await self._check_api():
            return False

        if session_id:
            self.session_id = session_id
        else:
            self.session_id = await self._create_session()
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = aiohttp.ClientSession(
            headers=self._make_headers(),
            trust_env=True,
        )
        ws_url = f"{self.api_url.replace('http', 'ws')}/stream/ws/{self.session_id}"

        try:
            self._ws = await self._session.ws_connect(
                ws_url, heartbeat=15, receive_timeout=300,
                headers=self._make_headers(), ssl=False,
            )
        except Exception as e:
            logger.warning("WebSocket connect failed: %s", e)
            if self._session:
                await self._session.close()
                self._session = None
            return False

        self._running = True
        # 启动接收循环（以后所有消息都从队列读）
        await self._start_receive_loop()

        # 等待 session_ready（从队列消费，不直接调 _ws.receive）
        try:
            first_event = await _safe_wait_for(self._incoming.get(), timeout=5)
            if first_event:
                await self._emit(first_event)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        return True

    # ── 后台监听（空闲时接收主动消息）──

    def start_background_listener(self):
        """启动后台监听 — 仅当 consumer == 'processing' 时不生效"""
        if self._bg_listener_task and not self._bg_listener_task.done():
            return
        self._consumer = "background"
        self._bg_listener_task = asyncio.ensure_future(self._background_receive_loop())

    async def _background_receive_loop(self):
        """后台接收循环 — 仅在 consumer == 'background' 时消费队列并 emit 事件。"""
        try:
            while self._running and self._consumer == "background":
                if not self._ws or self._ws.closed:
                    await asyncio.sleep(5)
                    continue
                try:
                    event = await self.receive_event(timeout=30.0)
                    if event:
                        await self._emit(event)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug("Background receive error (will retry): %s", e)
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def send_input(self, content: str) -> bool:
        """发送用户输入。

        注意：不再携带 execution_mode —— 后端已禁止经 WS 注入执行模式
        （无鉴权旁路，安全修复）。模式切换走 PUT /config/EXECUTION_MODE
        （repl._set_execution_mode 已按此实现）。
        """
        if not self._ws or self._ws.closed:
            return False
        try:
            msg = {"type": "input", "content": content}
            await self._ws.send_json(msg)
            return True
        except Exception as e:
            logger.warning("send_input failed: %s", e)
            return False

    async def send_stop(self) -> bool:
        """发送停止信号"""
        if not self._ws or self._ws.closed:
            return False
        try:
            await self._ws.send_json({"type": "stop"})
            return True
        except Exception as e:
            logger.warning("send_stop failed: %s", e)
            return False

    async def receive_event(self, timeout: float = 45.0) -> Dict[str, Any]:
        """从队列读下一个事件（不直接调 _ws.receive）。"""
        try:
            return await _safe_wait_for(self._incoming.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_str = str(e)
            if "SSL" in err_str or "ssl" in err_str or "certificate" in err_str:
                hint = f"接收失败: {err_str} — 请检查代理(Clash/VPN)是否拦截了 localhost 连接"
            else:
                hint = f"接收失败: {err_str}"
            return {"type": "error", "content": hint}

    # ── 事件解析 ──

    def parse_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        解析原始 WebSocket 事件，返回标准化的条目。
        返回 None 表示不需要添加到对话框。

        兼容两种事件格式：
        1. 富事件 (旧): data.stage_event.payload.msg_type == "broadcast"
        2. 简单事件 (新): type=thinking|status + content 字符串
        """
        # 轻量验证：确保 event 是 dict
        if not isinstance(event, dict):
            logger.debug("parse_event: 非 dict 消息，忽略: %s", type(event).__name__)
            return None

        try:
            return self._parse_event_inner(event)
        except Exception as e:
            logger.debug("parse_event 解析异常: %s, event=%s", e, str(event)[:200])
            return None

    def _parse_event_inner(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """内部解析逻辑"""
        msg_type = event.get("type", "")
        event_name = event.get("event", "")
        content = event.get("content", "")
        role = event.get("role", "")
        data = event.get("data", {}) or {}
        stage_event = data.get("stage_event", {}) if isinstance(data, dict) else {}
        payload = stage_event.get("payload", {}) if isinstance(stage_event, dict) else {}

        # ── 富事件: broadcast 消息 (多模型通信) ──
        pld_msg_type = payload.get("msg_type", "")
        metadata = payload.get("metadata", {})
        if pld_msg_type == "broadcast" and metadata.get("dialog_id"):
            raw_content = payload.get("content", "")
            if isinstance(raw_content, dict):
                dialog_text = raw_content.get("content", "")
                entry_type = raw_content.get("entry_type", "")
                model_id = raw_content.get("model_id", "")
                tier = role  # 从事件信封获取 tier，不在 content 内部
                round_num = raw_content.get("round", 0)
            else:
                dialog_text = str(raw_content)
                entry_type = model_id = tier = ""
                round_num = 0

            return {
                "kind": "dialog",
                "tier": tier,
                "icon": TIER_ICONS.get(tier, ""),
                "label": TIER_LABELS.get(tier, tier),
                "model_id": model_id,
                "content": dialog_text,
                "entry_type": entry_type,
                "round_num": round_num,
                "timestamp": time.time(),
            }

        # ── 富事件: tool_call ──
        event_type = stage_event.get("type", data.get("event_type", ""))
        if event_type == "tool_call":
            tool_name = stage_event.get("target", data.get("target", "unknown"))
            action = stage_event.get("action", data.get("action", ""))
            success = stage_event.get("success", data.get("success", True))
            latency = stage_event.get("latency_ms", data.get("latency_ms", 0))

            return {
                "kind": "tool",
                "tool": tool_name,
                "action": action,
                "success": success,
                "latency_ms": latency,
                "params": payload.get("params", payload.get("tool_params", {})),
                "result": payload.get("result", payload.get("output", "")),
                "error": payload.get("error", ""),
                "timestamp": time.time(),
            }

        # ── 安全审查事件 ──
        if event_type == "security":
            action = stage_event.get("action", data.get("action", ""))
            tool_name = stage_event.get("target", data.get("target", ""))
            # payload 可能在 stage_event.payload 或 data.payload（格式化后）
            sec_payload = stage_event.get("payload", {}) if stage_event else {}
            if not sec_payload:
                sec_payload = data.get("payload", {}) if isinstance(data, dict) else {}
            detail = sec_payload.get("detail", "")
            request_id = sec_payload.get("request_id", "")
            caller = sec_payload.get("caller", "")

            # 需要用户审批的事件
            if "等待用户审批" in action and request_id:
                logger.info(f"[WS] 安全审批事件解析成功: tool={tool_name}, request_id={request_id}")
                return {
                    "kind": "security_review",
                    "request_id": request_id,
                    "tool": tool_name,
                    "caller": caller,
                    "detail": detail,
                    "timestamp": time.time(),
                }

            # 模式切换请求
            if action == "mode_change_request" and request_id:
                return {
                    "kind": "mode_change_request",
                    "request_id": request_id,
                    "reason": sec_payload.get("reason", detail),
                    "suggested_mode": sec_payload.get("suggested_mode", "edit"),
                    "timestamp": time.time(),
                }

            # 用户意图询问
            if action == "user_intent_request" and request_id:
                return {
                    "kind": "user_intent_request",
                    "request_id": request_id,
                    "question": sec_payload.get("question", detail),
                    "options": sec_payload.get("options", []),
                    "context": sec_payload.get("context", ""),
                    "timestamp": time.time(),
                }

            return {
                "kind": "security",
                "tool": tool_name,
                "action": action,
                "success": data.get("success", True),
                "detail": detail,
                "duration_ms": payload.get("duration_ms", 0),
                "timestamp": time.time(),
            }

        # ── 简单事件: thinking_step (直接显示内容) ──
        if msg_type == "thinking" and event_name == "thinking_step" and content:
            actual_tier = role if role in ("large", "supervisor", "expert", "user") else "thinking"
            return {
                "kind": "dialog",
                "tier": actual_tier,
                "icon": TIER_ICONS.get(actual_tier, "💭"),
                "label": TIER_LABELS.get(actual_tier, "思考"),
                "model_id": role,
                "content": content,
                "entry_type": "thought",
                "round_num": 0,
                "timestamp": time.time(),
            }

        # ── 简单事件: module_result ──
        if msg_type == "status" and event_name == "module_result" and content:
            return {
                "kind": "dialog",
                "tier": "system",
                "icon": "⚙️",
                "label": "系统",
                "model_id": "system",
                "content": content,
                "entry_type": "status",
                "round_num": 0,
                "timestamp": time.time(),
            }

        if msg_type == "status" and event_name == "thinking_progress":
            return {
                "kind": "status",
                "content": content,
                "phase": data.get("phase", "thinking"),
                "badge": data.get("badge", "思考中"),
                "progress": data.get("progress", 0.0),
                "elapsed_s": data.get("elapsed_s", 0),
                "queue_size": data.get("queue_size", 0),
                "running": data.get("running", False),
                "timestamp": time.time(),
            }

        # ── 反思事件: reflection_outcome ──
        if msg_type == "reflection" and event_name == "reflection_outcome":
            c = content if isinstance(content, dict) else {}
            decision = c.get("decision", "")
            return {
                "kind": "reflection",
                "icon": REFLECTION_ICONS.get(decision, "🔍"),
                "label": f"反思-{decision}",
                "decision": decision,
                "error_reason": c.get("error_reason", ""),
                "suggestion": c.get("suggestion", ""),
                "retry_count": c.get("retry_count", 0),
                "node": c.get("node", ""),
                "timestamp": time.time(),
            }

        # ── 主动搭话: proactive_outreach ──
        if msg_type == "proactive" and event_name == "proactive_outreach" and content:
            return {
                "kind": "dialog",
                "tier": "system",
                "icon": "🤖",
                "label": "助手",
                "model_id": "proactive",
                "content": content,
                "entry_type": "proactive",
                "round_num": 0,
                "timestamp": time.time(),
            }

        return None

    async def send_security_response(self, request_id: str, approved: bool, reason: str = ""):
        """发送安全审查响应到后端"""
        if not self._ws:
            return
        try:
            await self._ws.send_json({
                "type": "security_response",
                "request_id": request_id,
                "approved": approved,
                "reason": reason,
            })
        except Exception as e:
            logger.error(f"发送安全审查响应失败: {e}")

    async def send_interactive_response(self, request_id: str, response: dict):
        """发送交互式工具响应到后端（模式切换、用户意图等）"""
        if not self._ws:
            return
        try:
            await self._ws.send_json({
                "type": "interactive_response",
                "request_id": request_id,
                **response,
            })
        except Exception as e:
            logger.error(f"发送交互式响应失败: {e}")

    # ── 一轮完整的处理 ──

    async def process_input(
        self,
        user_input: str,
        state=None,
        warn_callback=None,
        per_event_timeout: float = 45.0,
        max_retries: int = 2,
        consecutive_limit: int = 2,
    ):
        """
        发送用户输入并等待完整响应。

        接管 consumer（停止背景监听），结束后释放。

        参数：
          state — AppState，用于跟踪进度和 cancel_requested 标志
          warn_callback — async callable(str)，向 TUI 注入进度提示
          per_event_timeout — 单次 receive 等待上限（秒）
          max_retries — 连续超时后整体重连最多次数
          consecutive_limit — 连续超时多少次后触发重连
        """
        await self._acquire_consumer("processing")
        try:
            await self._process_input_impl(
                user_input, state, warn_callback,
                per_event_timeout, max_retries, consecutive_limit,
            )
        finally:
            self._release_consumer()

    async def _process_input_impl(
        self,
        user_input: str,
        state=None,
        warn_callback=None,
        per_event_timeout: float = 45.0,
        max_retries: int = 2,
        consecutive_limit: int = 2,
    ):
        start = time.time()
        self._cancel_flag = False

        # 重置数据收集字段
        self.dialog_entries = []
        self.tool_calls = []
        self.tool_stats = {"total": 0, "success": 0, "failed": 0, "total_latency_ms": 0.0}
        self.final_response = ""
        self.trace_id = ""
        self.reflection_events = []

        # 用户输入条目
        user_entry = {
            "kind": "dialog",
            "tier": "user",
            "icon": "👤",
            "label": "用户",
            "model_id": "user",
            "content": user_input[:200],
            "entry_type": "user_input",
            "round_num": 0,
            "timestamp": time.time(),
        }
        self.dialog_entries.append(user_entry)
        await self._emit({"type": "user_input", "entry": user_entry})

        overall_attempt = 0

        while True:  # 外层重试循环
            # ── 发送 ──
            if not await self.send_input(user_input):
                if await self.connect():
                    if not await self.send_input(user_input):
                        await self._emit({"type": "error", "content": "发送失败"})
                        return
                else:
                    await self._emit({"type": "error", "content": "发送失败: 重连失败"})
                    return

            # ── 内层接收循环 ──
            consecutive_timeouts = 0

            while True:
                # 取消检测
                if self._cancel_flag or (state and state.cancel_requested):
                    return

                try:
                    event = await self.receive_event(timeout=per_event_timeout)
                    consecutive_timeouts = 0
                    if state:
                        state.last_event_time = time.time()
                        state.consecutive_timeouts = 0
                        state.thinking_hint = "思考中…"

                except asyncio.TimeoutError:
                    consecutive_timeouts += 1
                    if state:
                        state.consecutive_timeouts = consecutive_timeouts

                    if consecutive_timeouts < consecutive_limit:
                        # 首次或非阈值超时：发提示，继续等待
                        if warn_callback:
                            await warn_callback(
                                f"[dim yellow]⏳ 等待响应中… (已等 {per_event_timeout:.0f}s)[/dim yellow]"
                            )
                        continue

                    # 连续超时达阈值 → 尝试整体重连重试
                    if overall_attempt < max_retries:
                        overall_attempt += 1
                        if state:
                            state.retry_count = overall_attempt
                        await self._emit({
                            "type": "retrying",
                            "content": f"连续超时，正在重连重试 ({overall_attempt}/{max_retries})…"
                        })
                        # 关闭旧连接
                        if self._ws and not self._ws.closed:
                            await self._ws.close()
                        if self._session:
                            await self._session.close()
                            self._session = None
                        # 重新连接
                        if not await self.connect():
                            await self._emit({"type": "error", "content": "重连失败，放弃"})
                            if state:
                                state.thinking_hint = ""
                            return
                        break  # 跳出内层循环，外层重新 send_input
                    else:
                        await self._emit({
                            "type": "error",
                            "content": f"连续超时，已重试 {max_retries} 次，放弃"
                        })
                        if state:
                            state.thinking_hint = ""
                        return

                except asyncio.CancelledError:
                    return

                # ── 正常事件处理 ──
                if not event:
                    continue

                msg_type = event.get("type", "")
                event_name = event.get("event", "")
                content = event.get("content", "")
                data = event.get("data", {}) or {}

                # 解析并添加条目
                parsed = self.parse_event(event)
                if parsed:
                    if parsed["kind"] == "dialog":
                        # 流式更新：替换同 tier+round 的旧条目
                        if parsed.get("entry_type") == "streaming":
                            replaced = False
                            for i in range(len(self.dialog_entries) - 1, max(-1, len(self.dialog_entries) - 6), -1):
                                existing = self.dialog_entries[i]
                                if (existing.get("tier") == parsed.get("tier")
                                        and existing.get("round_num") == parsed.get("round_num")
                                        and existing.get("entry_type") == "streaming"):
                                    self.dialog_entries[i] = parsed
                                    replaced = True
                                    break
                            if not replaced:
                                self.dialog_entries.append(parsed)
                        else:
                            # 普通去重
                            prefix = parsed.get("content", "")[:40]
                            is_dup = False
                            for existing in reversed(self.dialog_entries[-5:]):
                                if (existing.get("tier") == parsed.get("tier")
                                        and existing.get("round_num") == parsed.get("round_num")
                                        and existing.get("content", "")[:40] == prefix):
                                    is_dup = True
                                    break
                            if not is_dup:
                                self.dialog_entries.append(parsed)
                        if len(self.dialog_entries) > self._max_entries:
                            self.dialog_entries = self.dialog_entries[-self._max_entries:]

                    elif parsed["kind"] == "tool":
                        self.tool_calls.append(parsed)
                        if len(self.tool_calls) > 100:
                            self.tool_calls = self.tool_calls[-100:]
                        self.tool_stats["total"] += 1
                        if parsed["success"]:
                            self.tool_stats["success"] += 1
                        else:
                            self.tool_stats["failed"] += 1
                        self.tool_stats["total_latency_ms"] += parsed["latency_ms"]

                    elif parsed["kind"] == "reflection":
                        self.reflection_events.append(parsed)
                        if len(self.reflection_events) > 50:
                            self.reflection_events = self.reflection_events[-50:]

                # 发送给外部回调
                await self._emit(event)

                # 处理完成事件
                if msg_type == "message" and event_name == "assistant_message":
                    self.final_response = content
                    self.trace_id = data.get("trace_id", "")

                elif msg_type == "done":
                    self.elapsed_ms = (time.time() - start) * 1000
                    self.trace_id = data.get("trace_id", self.trace_id)
                    if state:
                        state.thinking_hint = ""
                    # 收集副会话对话记录（供后续批量展示）
                    sub_sessions = data.get("sub_sessions", [])
                    if sub_sessions and state:
                        state.sub_sessions = sub_sessions
                    return

                elif msg_type == "error":
                    if state:
                        state.thinking_hint = ""
                    self.dialog_entries.append({
                        "kind": "dialog",
                        "tier": "unknown",
                        "icon": "❌",
                        "label": "错误",
                        "model_id": "system",
                        "content": content,
                        "entry_type": "error",
                        "round_num": 0,
                        "timestamp": time.time(),
                    })
                    return

    def request_cancel(self):
        """设置取消标志，让 process_input 的循环在下次迭代时退出"""
        self._cancel_flag = True

    async def close(self):
        """关闭连接"""
        self._running = False
        await self._stop_task(self._receive_task)
        await self._stop_task(self._bg_listener_task)
        self._bg_listener_task = None
        self._receive_task = None
        self._consumer = "idle"
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None and not self._ws.closed
