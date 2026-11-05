"""
Chat Gateway — 统一对话入口（/stream/*）

在【每条对话开始时】读取 CORTEX_MODE 并分流（对话期间不重复判断）:
  CORTEX_MODE=chatonly → backend/ 简化单模型路线（懒加载，不触碰多模型编排代码）
  CORTEX_MODE=agent    → modules/thinking/api_stream 完整多模型路线（默认）

- 两套代码库保持独立、零修改；本模块只做路由分发 + 事件格式适配。
- agent 路线直接调用 api_stream 的既有处理函数，行为零变化。
- chatonly 路线的简单事件（message/done/error）被翻译成统一 envelope，
  前端（vue）与 CLI TUI 无需任何改动。
- 两套记忆系统共用同一 SQLite/FAISS 文件，首次请求时幂等对齐 schema。
"""
import asyncio
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel


from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("chat_gateway")

# chatonly 运行中任务注册表（session_id → active_task），供 REST /stream/stop 跨连接取消
_CHATONLY_TASKS: Dict[str, asyncio.Task] = {}

router = APIRouter(prefix="/stream", tags=["流式思考"])


# ---------------------------------------------------------------------------
# 模式解析 — 每条对话开始时判断一次
# ---------------------------------------------------------------------------

def _resolve_mode() -> str:
    """读取当前对话路线模式（每个会话 / 每次请求开始时调用）

    env 优先（测试/启动注入）；设置页通过 PUT /config/CORTEX_MODE
    修改时会在 update_config 里同步写回 os.environ，同样生效。
    """
    mode = (os.environ.get("CORTEX_MODE")
            or getattr(settings, "CORTEX_MODE", "agent")
            or "agent").strip().lower()
    return "chatonly" if mode in ("chatonly", "chat_only", "chat-only") else "agent"


# ---------------------------------------------------------------------------
# 共享记忆库 schema 对齐（幂等）
# ---------------------------------------------------------------------------

_schema_checked = False
_schema_lock = threading.Lock()


def ensure_shared_schema() -> None:
    """幂等对齐共享 SQLite 的 chat_sessions / chat_messages 表。

    两套 SQLAlchemy 模型（modules 全量 / backend 精简）共用同一份 DB：
    保证无论谁先建表，缺失的列都会被补上，互不踩踏。
    迁移成功后才置 _schema_checked，避免首次调用时表尚未创建导致永久跳过。
    """
    global _schema_checked
    if _schema_checked:
        return
    with _schema_lock:
        if _schema_checked:
            return

        try:
            import sqlite3
            # 与 chatonly (backend) 实际使用的路径保持一致：优先 MEMORY_DB_PATH，
            # 回退到主架构 SQLITE_PATH，再回退默认。默认两者都指向 data/memory.db。
            db_path = (
                os.environ.get("MEMORY_DB_PATH")
                or getattr(settings, "SQLITE_PATH", "")
                or "data/memory.db"
            )
            conn = sqlite3.connect(db_path)
            try:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                if "chat_sessions" not in tables or "chat_messages" not in tables:
                    return  # 表尚未创建，由各 DB 初始化负责（不置 flag，下次重试）

                def _columns(table):
                    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                    return {r[1] for r in rows}

                sessions = _columns("chat_sessions")
                for col, ddl in (
                    ("execution_mode", "VARCHAR(20) DEFAULT 'edit'"),
                    ("metadata_json", "TEXT DEFAULT '{}'"),
                ):
                    if col not in sessions:
                        conn.execute(f"ALTER TABLE chat_sessions ADD COLUMN {col} {ddl}")

                messages = _columns("chat_messages")
                for col, ddl in (
                    ("tier", "VARCHAR(20) DEFAULT ''"),
                    ("metadata_json", "TEXT DEFAULT '{}'"),
                ):
                    if col not in messages:
                        conn.execute(f"ALTER TABLE chat_messages ADD COLUMN {col} {ddl}")
                conn.commit()
                logger.info("[ChatGateway] 共享记忆库 schema 已对齐")
                _schema_checked = True  # 迁移成功后才标记完成
            except Exception as e:
                logger.debug(f"[ChatGateway] schema 对齐跳过: {e}")
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[ChatGateway] schema 对齐失败: {e}")


# ---------------------------------------------------------------------------
# chatonly 路线：chat_light 懒加载（agent 模式绝不 import chat_light.*）
# ---------------------------------------------------------------------------

_chat_thinker: Optional[Any] = None
_chat_thinker_lock = threading.Lock()


def _get_chat_thinker():
    """懒加载 chat_light 的 ContinuousThinker 单例"""
    global _chat_thinker
    if _chat_thinker is None:
        with _chat_thinker_lock:
            if _chat_thinker is None:
                from modules.thinking.chat_light.continuous_thinker import ContinuousThinker
                _chat_thinker = ContinuousThinker()
    return _chat_thinker


def _get_chat_session_repo():
    from modules.database.session_repo import get_session_repo
    return get_session_repo()


def _clear_session_events(session_id: str) -> None:
    """删除会话的事件日志（会话/消息被清空时同步清理，避免旧轨迹被回放）。"""
    try:
        from modules.database.session_events import get_session_event_repo
        get_session_event_repo().clear(session_id)
    except Exception as e:
        logger.debug(f"[EventLog] 清理会话事件失败 (非致命): {e}")


def _clear_session_projections(session_id: str) -> None:
    """删除会话的投影快照与待办请求（Phase 4/4.b）——与事件日志同生命周期。

    否则新建同名会话或清空后，前端一进来就会拿到上一轮的 todos/上下文占用，
    甚至弹出早已失效的审批横幅。
    """
    try:
        from modules.thinking import projections
        projections.clear_projections(session_id)
    except Exception as e:
        logger.debug(f"[Projection] 清理会话投影失败 (非致命): {e}")
    try:
        from modules.thinking import pending_requests
        pending_requests.clear(session_id)
    except Exception as e:
        logger.debug(f"[Pending] 清理会话待办失败 (非致命): {e}")


def _record_user_message(session_id: str, message_id: Optional[str]) -> None:
    """用户消息入事件表（durable，只存 id）——与 api_stream 同一语义。

    简单路线自身没有工具，但事件日志对整个会话保持同一套锚点规则：换到 agent
    路线继续聊时，回放仍能把工具卡片挂到正确的用户消息下。只落库、不推送
    （前端没有 user_message 处理器，实时帧没有意义）。
    """
    if not message_id:
        return
    try:
        from modules.database.session_events import get_session_event_repo
        # 直写仓储（不造 envelope、不取 time()）：事件时间以落库时的 created_at 为准，
        # 也不占用回合的超时/心跳计时。
        get_session_event_repo().append(
            session_id,
            type="user_message",
            event="user_message",
            role="user",
            content="",
            data={"message_id": message_id},
        )
    except Exception as e:
        logger.debug(f"[EventLog] 记录用户消息失败 (非致命): {e}")


def _envelope(
    session_id: str,
    msg_type: str,
    event: str,
    content: str = "",
    role: str = "system",
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一事件 envelope（与 api_stream._build_event 同构）"""
    return {
        "type": msg_type,
        "event": event,
        "session_id": session_id,
        "role": role,
        "content": content,
        "data": data or {},
        "timestamp": time.time(),
    }


async def _safe_ws_send(websocket: WebSocket, data: dict) -> bool:
    """发送 WS 消息；连接已断开（如 voice 瞬时连接）返回 False，不抛异常"""
    try:
        await websocket.send_json(data)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# chatonly WebSocket — 简单路线
# ---------------------------------------------------------------------------
# @link:block ChatOnly_Turn = Retrieve · Guard · Think · Loop(Retrieve · Guard · Write · Emit)
async def _consume_turn(
    websocket: WebSocket,
    session_id: str,
    repo,
    thinker,
    content: str,
    web_search: bool = False,
) -> None:
    """消费一次思考回合：把 backend 简单事件翻译成统一 envelope 流式输出。

    独立任务运行，保证外层 receive_text() 不被阻塞 —— stop/新消息可随时打断。
    """
    user_msg_id = repo.save_message(session_id, "user", content)

    # 与 api_stream 一致：先发 received ack（带 message_id 供前端删除/编辑），避免 TUI 等待超时
    if not await _safe_ws_send(websocket, _envelope(
        session_id, "ack", "received", "已接收请求，开始处理", "system",
        data={"message_id": user_msg_id} if user_msg_id else None,
    )): return

    queue: asyncio.Queue = asyncio.Queue()
    # 统一握手：LLM 前确认前端可达（用户主动对话，仅断线瞬间会失败）
    try:
        from modules.thinking.frontend_channel import confirm_frontend_connection
        if not confirm_frontend_connection(session_id):
            await _safe_ws_send(websocket, _envelope(
                session_id, "error", "connection_lost", "前端连接已断开，本轮请求已跳过", "system",
            ))
            return
    except Exception as e:
        logger.debug(f"[chat_gateway] 对话握手失败 (非致命): {e}")
    think_task = asyncio.create_task(thinker.think(
        session_id, content, queue, enable_web_search=web_search,
    ))
    full_text = []
    done = False
    errored = False
    turn_start = time.time()
    last_progress = turn_start
    last_event = turn_start      # 最后一次收到队列事件的时间（判断真超时）
    flush_buf: list = []         # token 聚合缓冲，避免逐 token 刷屏

    async def _flush_reply_chunk() -> None:
        """把回复缓冲推成一帧 `chunk`（流式正文预览，前端边收边渲染）。

        `chunk` 是瞬时帧：不落库、不占 seq；刷新后以 `assistant_message` 为准。
        """
        if not flush_buf:
            return
        await _safe_ws_send(websocket, _envelope(
            session_id, "chunk", "chunk_delta", "".join(flush_buf), "main",
            data={"tier": "large"},
        ))
        flush_buf.clear()

    # 回放锚点：用户消息入事件表，必须早于本轮任何工具事件（seq 更小）。
    # 放在计时基线之后：它不取 time()，也不影响心跳/超时判定。
    _record_user_message(session_id, user_msg_id)

    try:
        while True:
            try:
                # 1s 轮询：即便无 token 也能定期发心跳，避免 TUI 误判断线
                tok = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                now = time.time()
                # 静默期进度心跳（思考中/无输出）
                if now - last_progress >= 1.0:
                    last_progress = now
                    if not await _safe_ws_send(websocket, _envelope(
                        session_id, "status", "thinking_progress",
                        f"思考中 {int(now - turn_start)}s",
                        "system",
                    )): break
                # 先看任务是否已收尾（无终态 token 的异常/取消场景）
                if think_task.done() and queue.empty():
                    if not await _safe_ws_send(websocket, _envelope(
                        session_id, "error", "error", "任务异常终止", "system",
                    )): pass
                    errored = True
                    break
                # 真正的思考超时：累计静默超过 300s
                if now - last_event >= 300:
                    if not await _safe_ws_send(websocket, _envelope(
                        session_id, "error", "error", "思考超时", "system",
                    )): pass
                    errored = True
                    break
                continue

            last_event = time.time()
            if tok.get("type") == "message":
                chunk = tok.get("content", "")
                full_text.append(chunk)
                flush_buf.append(chunk)
                # 满 40 字符或遇到换行/句末就推一帧，避免逐 token 刷屏
                if len("".join(flush_buf)) >= 40 or chunk.endswith(("\n", "。", "！", "？", ".", "!", "?")):
                    await _flush_reply_chunk()
            elif tok.get("type") == "thinking":
                # deepseek 思考过程（reasoning）单独推送，与回复区分。
                # 持久化 role="thought"（与 agent 模式 _persist_thought 一致），
                # 切换会话后思考历史可恢复；上下文恢复按 user/assistant 过滤不污染模型。
                # 同时写入会话事件日志（session_events），供仪表盘"会话轨迹"页按时间线回放。
                _thought = tok.get("content", "")
                if _thought:
                    try:
                        repo.save_message(session_id, "thought", _thought)
                    except Exception as _swallowed_err:
                        logger.debug(f"[_consume_turn] ignored Exception: {_swallowed_err}")
                    try:
                        from modules.database.session_events import get_session_event_repo
                        get_session_event_repo().append(
                            session_id,
                            type="thinking",
                            event="thinking_step",
                            role="thinking",
                            content=_thought,
                            data={"source": "reasoning",
                                  "identity_name": tok.get("identity_name", "总指挥"),
                                  "tier": tok.get("tier", "large")},
                        )
                    except Exception as _swallowed_err:
                        logger.debug(f"[_consume_turn] ignored Exception: {_swallowed_err}")
                if not await _safe_ws_send(websocket, _envelope(
                    session_id, "thinking", "thinking_step",
                    _thought, "thinking",
                    data={"source": "reasoning",
                          "identity_name": tok.get("identity_name", "总指挥"),
                          "tier": tok.get("tier", "large")},
                )): pass
            elif tok.get("type") == "mental":
                # 心理活动（conscience 内心独白）：持久化 role="mental"（与对话同款），
                # 前端切换会话后历史可恢复；上下文恢复时按 role 过滤不污染模型输入。
                # 同样写入会话事件日志，供仪表盘轨迹回放。
                _mental = tok.get("content", "")
                if _mental:
                    try:
                        repo.save_message(session_id, "mental", _mental)
                    except Exception as _swallowed_err:
                        logger.debug(f"[_consume_turn] ignored Exception: {_swallowed_err}")
                    try:
                        from modules.database.session_events import get_session_event_repo
                        get_session_event_repo().append(
                            session_id,
                            type="mental",
                            event="mental",
                            role="system",
                            content=_mental,
                            data={"label": "心理活动"},
                        )
                    except Exception as _swallowed_err:
                        logger.debug(f"[_consume_turn] ignored Exception: {_swallowed_err}")
                if not await _safe_ws_send(websocket, _envelope(
                    session_id, "mental", "mental",
                    _mental, "system",
                    data={"label": "心理活动"},
                )): pass
            elif tok.get("type") == "done":
                # 剩余尾巴补一帧 chunk（正文已流式推过，不再当思考发一遍）
                await _flush_reply_chunk()
                done = True
                break
            elif tok.get("type") == "error":
                await _flush_reply_chunk()
                if not await _safe_ws_send(websocket, _envelope(
                    session_id, "error", "error",
                    tok.get("content", "内部错误"), "system",
                )): pass
                errored = True
                break

            # token 流中的进度心跳（token 间隔 > 1s 时保持活跃）
            now = time.time()
            if now - last_progress >= 1.0:
                last_progress = now
                if not await _safe_ws_send(websocket, _envelope(
                    session_id, "status", "thinking_progress",
                    f"思考中 {int(now - turn_start)}s",
                    "system",
                )): pass
    finally:
        # 正常结束 / 被 stop / 新消息取消：一律确保底层 think 任务收尾，
        # 取消场景下 CancelledError 由 finally 后的传播路径继续上抛
        if not think_task.done():
            think_task.cancel()

    if done and full_text:
        text = "".join(full_text)
        # DB 为唯一真源：assistant 入库即可，读取时直连 DB，无需同步内存黑板
        assistant_msg_id = repo.save_message(session_id, "assistant", text)
        if not await _safe_ws_send(websocket, _envelope(
            session_id, "message", "assistant_message", text, "main",
            data={"message_id": assistant_msg_id},
        )): pass
    # 失败/取消路径：只发过 error，不再发「处理完成」，避免语义矛盾
    if not errored:
        if not await _safe_ws_send(websocket,
            _envelope(session_id, "done", "done", "处理完成", "system")
        ): pass
        try:
            from modules.database.session_events import get_session_event_repo
            get_session_event_repo().append(
                session_id,
                type="done",
                event="done",
                role="system",
                content="处理完成",
                data={},
            )
        except Exception as _swallowed_err:
            logger.debug(f"[_consume_turn] ignored Exception: {_swallowed_err}")


# @link:block ChatOnly_WS = Loop(Retrieve · Guard · Emit)
async def _chatonly_ws(websocket: WebSocket, session_id: str) -> None:
    ensure_shared_schema()
    await websocket.accept()

    # 注册到统一投送系统（主动搭话等可实时推送；协议与 api_stream 同构，前端统一解析）
    _ws_registered = False
    try:
        from modules.thinking.api_stream import connection_manager
        async with connection_manager._lock:
            connection_manager.active_connections[session_id] = websocket
            connection_manager._loop = asyncio.get_running_loop()
        _ws_registered = True
    except Exception as _swallowed_err:
        logger.debug(f"[_chatonly_ws] ignored Exception: {_swallowed_err}")

    repo = _get_chat_session_repo()
    repo.create_session(session_id)

    try:
        await websocket.send_json(
            _envelope(session_id, "ack", "session_ready", "WebSocket 会话已建立", "system")
        )
    except Exception:
        # 客户端已断开（如 voice 会话瞬时连接）→ 注销注册后结束，避免 connection_manager 残留僵尸连接
        if _ws_registered:
            try:
                from modules.thinking.api_stream import connection_manager
                async with connection_manager._lock:
                    connection_manager.active_connections.pop(session_id, None)
            except Exception as _swallowed_err:
                logger.debug(f"[_chatonly_ws] ignored Exception: {_swallowed_err}")
        return

    active_task: Optional[asyncio.Task] = None

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                msg = {"type": "input", "content": data}

            msg_type = msg.get("type", "input")

            if msg_type == "input":
                content = msg.get("content", "")
                attachments = msg.get("attachments") or []
                if not content and not attachments:
                    continue
                if attachments:
                    from modules.thinking.attachment_handler import validate_attachments
                    att_err = validate_attachments(attachments)
                    if att_err:
                        await _safe_ws_send(
                            websocket,
                            _envelope(
                                session_id,
                                "error",
                                "error",
                                f"附件格式错误: {att_err}",
                                "system",
                            ),
                        )
                        continue
                    try:
                        from modules.thinking.attachment_handler import (
                            parse_attachments, extract_images, summarize_attachments,
                        )
                        from config.settings import settings
                        # 直连模式：图片直接附给大模型（不调独立视觉模型）
                        # 同时检查模型层的 ENABLE_IMAGE 开关
                        image_mode = str(getattr(settings, "CHAT_IMAGE_MODE", "describe") or "describe").lower()
                        enable_image = getattr(settings, "LARGE_MODEL_ENABLE_IMAGE", True)
                        if image_mode == "direct" and enable_image:
                            att_images = extract_images(attachments)
                            att_text = await summarize_attachments(attachments)
                        elif not enable_image:
                            # 图片功能被关闭，跳过所有图片处理
                            att_images = []
                            att_text = await summarize_attachments([a for a in attachments if not getattr(a, "is_image", False)]) if attachments else ""
                        else:
                            att_images = []
                            att_text = await parse_attachments(attachments)
                        if att_text:
                            content = (content + "\n\n" + att_text) if content else att_text
                    except Exception:
                        att_images = []
                else:
                    att_images = []
                if not content:
                    continue
                # 新消息打断上一轮（简单路线同一时刻只处理一条）：
                # 等待旧任务清理完毕再开新任务，避免两条任务并发向同一 WS 发送导致乱序
                if active_task and not active_task.done():
                    active_task.cancel()
                    try:
                        await active_task
                    except asyncio.CancelledError as _swallowed_err:
                        logger.debug(f"[_chatonly_ws] ignored asyncio.CancelledError: {_swallowed_err}")
                # 当前回合图片（直连多模态）：透传给本轮大模型请求
                from modules.thinking.turn_images import set_turn_images
                set_turn_images(att_images or None)
                active_task = asyncio.create_task(
                    _consume_turn(websocket, session_id, repo, _get_chat_thinker(), content, web_search=bool(msg.get("web_search", False)))
                )
                # 注册到全局表，供 REST /stream/stop 跨连接取消
                _CHATONLY_TASKS[session_id] = active_task
                active_task.add_done_callback(
                    lambda _t, _sid=session_id: _CHATONLY_TASKS.pop(_sid, None)  # type: ignore[misc]
                )

            elif msg_type == "ping":
                try:
                    await websocket.send_json(
                        _envelope(session_id, "ack", "pong", "pong", "system")
                    )
                except Exception:
                    break

            elif msg_type == "stop":
                # 与 api_stream 一致：取消运行中的思考任务
                if active_task and not active_task.done():
                    active_task.cancel()
                active_task = None
                try:
                    await websocket.send_json(
                        _envelope(session_id, "done", "stopped", "会话已停止", "system")
                    )
                except Exception:
                    break
                # 与 api_stream 一致：不 break，保持连接允许后续新消息

    except WebSocketDisconnect:
        logger.info(f"[ChatGateway] WebSocket 断开: {session_id}")
    finally:
        if active_task and not active_task.done():
            active_task.cancel()
        # 断开清理：消息已落 DB，重连由 /context DB 兜底恢复，无需同步内存黑板
        # 注销统一投送注册
        if _ws_registered:
            try:
                from modules.thinking.api_stream import connection_manager
                async with connection_manager._lock:
                    connection_manager.active_connections.pop(session_id, None)
            except Exception as _swallowed_err:
                logger.debug(f"[_chatonly_ws] ignored Exception: {_swallowed_err}")
        try:
            await websocket.close()
        except Exception as _swallowed_err:
            logger.debug(f"[_chatonly_ws] ignored Exception: {_swallowed_err}")


# ---------------------------------------------------------------------------
# chatonly SSE — 简单路线
# ---------------------------------------------------------------------------

async def _chatonly_sse(session_id: str, question: str):
    ensure_shared_schema()
    repo = _get_chat_session_repo()
    repo.create_session(session_id)
    user_msg_id = repo.save_message(session_id, "user", question)

    thinker = _get_chat_thinker()

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(thinker.think(session_id, question, queue))
    full_text = []
    flush_buf: list = []          # token 聚合缓冲（与 WS 分支同一节流策略）
    turn_start = time.time()
    last_progress = turn_start
    last_event = turn_start      # 最近一次收到事件的时间（判断真超时，与 WS 语义一致）

    # 回放锚点：与 WS 分支一致，在计时基线之后、事件循环之前记录
    _record_user_message(session_id, user_msg_id)

    def _take_chunk() -> Optional[Dict[str, Any]]:
        """聚合缓冲 → 一帧 `chunk`（流式正文预览）。

        与 WS 分支（`_consume_turn._flush_reply_chunk`）同语义、同节流：
        `chunk` 是瞬时帧，不落库、不占 seq，刷新后以 `assistant_message` 为准。
        缓冲为空时返回 None（调用方跳过，不 yield 空帧）。
        """
        if not flush_buf:
            return None
        text = "".join(flush_buf)
        flush_buf.clear()
        return {
            "event": "chunk",
            "data": json.dumps(
                _envelope(session_id, "chunk", "chunk_delta", text, "main",
                          data={"tier": "large"}),
                ensure_ascii=False,
            ),
        }

    errored = False

    try:
        while True:
            if task.done() and queue.empty():
                # 异常收尾（无终态 token）：与 WS 分支一致发 error
                errored = True
                break
            try:
                tok = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # 静默期进度心跳（与 WS 分支一致，避免客户端超时）
                now = time.time()
                if now - last_progress >= 1.0:
                    last_progress = now
                    yield {
                        "event": "thinking_progress",
                        "data": json.dumps(
                            _envelope(session_id, "status", "thinking_progress",
                                      f"思考中 {int(now - turn_start)}s", "system"),
                            ensure_ascii=False,
                        ),
                    }
                # 与 WS 分支一致：累计静默超过 300s 判真超时（用最后事件时间，而非回合开始）
                if now - last_event >= 300:
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            _envelope(session_id, "error", "error", "思考超时", "system"),
                            ensure_ascii=False,
                        ),
                    }
                    return
                continue

            last_event = time.time()
            if tok.get("type") == "message":
                chunk = tok.get("content", "")
                full_text.append(chunk)
                flush_buf.append(chunk)
                # 与 WS 分支一致：满 40 字符或遇到换行/句末才推一帧，避免逐 token 刷屏
                if len("".join(flush_buf)) >= 40 or chunk.endswith(("\n", "。", "！", "？", ".", "!", "?")):
                    frame = _take_chunk()
                    if frame:
                        yield frame
            elif tok.get("type") == "done":
                frame = _take_chunk()      # 尾巴补一帧
                if frame:
                    yield frame
                break
            elif tok.get("type") == "error":
                frame = _take_chunk()
                if frame:
                    yield frame
                yield {
                    "event": "error",
                    "data": json.dumps(
                        _envelope(session_id, "error", "error",
                                  tok.get("content", "内部错误"), "system"),
                        ensure_ascii=False,
                    ),
                }
                return

        if errored:
            yield {
                "event": "error",
                "data": json.dumps(
                    _envelope(session_id, "error", "error", "任务异常终止", "system"),
                    ensure_ascii=False,
                ),
            }
            return

        text = "".join(full_text)
        if text:
            assistant_msg_id = repo.save_message(session_id, "assistant", text)
            yield {
                "event": "assistant_message",
                "data": json.dumps(
                    _envelope(session_id, "message", "assistant_message", text, "main",
                              data={"message_id": assistant_msg_id}),
                    ensure_ascii=False,
                ),
            }
        yield {
            "event": "done",
            "data": json.dumps(
                _envelope(session_id, "done", "done", "处理完成", "system"),
                ensure_ascii=False,
            ),
        }
    finally:
        if not task.done():
            task.cancel()


# ---------------------------------------------------------------------------
# /stream/* 路由 — 每条请求判断模式，分流
# ---------------------------------------------------------------------------

@router.post("/session")
async def create_session():
    if _resolve_mode() == "chatonly":
        ensure_shared_schema()
        session_id = f"ses_{uuid.uuid4().hex[:12]}"
        _get_chat_session_repo().create_session(session_id)
        return {"success": True, "data": {"session_id": session_id}}
    from modules.thinking import api_stream
    return await api_stream.create_session()


@router.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    # WS 鉴权（与 HTTP 中间件一致）：X-API-Key header 或 ?api_key= 查询参数。
    # HTTP 中间件不覆盖 WebSocket，必须在此显式校验（未配置 SIMPLE_API_KEY 的开发模式放行）。
    from modules.thinking.api_stream import _ws_auth_ok
    if not _ws_auth_ok(websocket):
        await websocket.close(code=4401, reason="未授权访问：缺少或无效的 API Key")
        return
    if _resolve_mode() == "chatonly":
        await _chatonly_ws(websocket, session_id)
        return
    from modules.thinking import api_stream
    await api_stream.websocket_chat(websocket, session_id)


@router.get("/sse/{session_id}")
async def sse_session_get(session_id: str, question: str = ""):
    if _resolve_mode() == "chatonly":
        if not question:
            from infra.errors import AppError, ErrorCode
            raise AppError(ErrorCode.BAD_REQUEST, "question 不能为空")
        from sse_starlette.sse import EventSourceResponse
        return EventSourceResponse(_chatonly_sse(session_id, question))
    from modules.thinking import api_stream
    return await api_stream.sse_session_get(session_id, question)


@router.get("/context/{session_id}")
async def get_context(session_id: str):
    # ── chatonly 模式：DB 为唯一真源直读（与 agent 模式共用公共层） ───────
    if _resolve_mode() == "chatonly":
        from modules.thinking.context.dialog_memory import load_dialog_from_db
        repo = _get_chat_session_repo()
        messages = load_dialog_from_db(session_id, limit=100, repo=repo)
        return {
            "success": True,
            "data": {
                "session_id": session_id,
                "messages": messages,
                "count": len(messages),
            },
        }

    # ── agent 模式：优先从 api_stream 会话上下文，为空则从 DB 恢复 ───────
    from modules.thinking import api_stream as _api
    ctx_messages = await _api.get_context(session_id)
    if not ctx_messages:
        # 从 DB 恢复
        repo = _get_chat_session_repo()
        if repo:
            try:
                ctx_messages = repo.get_recent_messages(session_id, limit=20)
            except Exception:
                ctx_messages = []
        # 同步到 api_stream 会话缓存。模块本身没有 sessions 属性：
        # get_context() 已经调用过 get_thinking_system()，这里复用同一单例。
        _thinking = _api.get_thinking_system()
        if session_id in _thinking.sessions:
            _thinking.sessions[session_id]["messages"] = ctx_messages
    return {
        "success": True,
        "data": {
            "session_id": session_id,
            "messages": ctx_messages,
            "count": len(ctx_messages),
        },
    }


@router.delete("/session/{session_id}")
async def close_session(session_id: str):
    # 1️⃣ 统一从 DB 删除会话及消息（事件日志一并清理）
    _get_chat_session_repo().delete_session(session_id)
    _clear_session_events(session_id)
    _clear_session_projections(session_id)
    # 2️⃣ 关键修复：清理跨模块的进程内存缓存，防止重新打开/请求时仍加载旧数据
    try:
        from modules.thinking.model_runner import _session_memory_context, _resume_context
        _session_memory_context.pop(session_id, None)
        # _resume_context 是 model_runner 里的 session->上下文 dict；
        # 之前的 getattr(_resume_context, None, None) 会抛 TypeError，导致清理整段跳过。
        if isinstance(_resume_context, dict):
            _resume_context.pop(session_id, None)
    except Exception as _swallowed_err:
        logger.debug(f"[close_session] ignored Exception: {_swallowed_err}")
    if _resolve_mode() == "chatonly":
        return {"success": True, "data": {"message": "会话已关闭"}}
    from modules.thinking import api_stream
    return await api_stream.close_session(session_id)


@router.post("/sessions/batch-delete")
async def batch_delete_sessions(body: dict = None):
    """批量删除会话（agent/chatonly 同一套，同步清理会话记忆内存）"""
    ids = (body or {}).get("session_ids") or []
    if not isinstance(ids, list) or not ids:
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR", "message": "session_ids 需为非空数组"}})
    repo = _get_chat_session_repo()
    deleted = []
    for sid in ids:
        if not isinstance(sid, str) or not sid:
            continue
        try:
            repo.delete_session(sid)
        except Exception as _swallowed_err:
            logger.debug(f"[batch_delete_sessions] ignored Exception: {_swallowed_err}")
        _clear_session_events(sid)
        _clear_session_projections(sid)
        deleted.append(sid)
    return {"success": True, "data": {"deleted": deleted, "count": len(deleted)}}


@router.delete("/session/{session_id}/messages")
async def clear_session_messages(session_id: str):
    """清空会话全部消息（保留会话本身），前端「清空对话」真实生效"""
    _get_chat_session_repo().clear_messages(session_id)
    _clear_session_events(session_id)
    _clear_session_projections(session_id)
    # chatonly：DB 为唯一真源，清空 DB 即生效，无需同步内存缓存
    if _resolve_mode() != "chatonly":
        try:
            from modules.thinking import api_stream
            system = api_stream.get_thinking_system()
            async with system._lock:
                if session_id in system.sessions:
                    system.sessions[session_id]["messages"] = []
        except Exception as e:
            logger.debug(f"[Agent] 清空消息后同步 sessions 缓存失败: {e}")
    return {"success": True, "data": {"session_id": session_id}}


@router.get("/session/{session_id}/graph")
async def get_session_graph(session_id: str):
    """会话执行图谱：谁呼唤谁 / 谁回复谁（节点按 tier 标注总指挥/主管/专家，边标注呼唤/回复）"""
    from modules.thinking.session_graph import get_session_graph_store
    store = get_session_graph_store()
    g = store.get_graph(session_id)
    if not g.get("nodes"):
        try:
            meta = _get_chat_session_repo().get_session_metadata(session_id)
            snap = (meta or {}).get("session_graph") or {}
            if snap.get("nodes"):
                store.restore(session_id, snap)
                g = store.get_graph(session_id)
        except Exception as _swallowed_err:
            logger.debug(f"[get_session_graph] ignored Exception: {_swallowed_err}")
    # 返回前持久化：前端每次读图谱都确保 metadata 最新，
    # 切换会话/重启后端后能恢复一致（不只是思考结束 finally 才写）
    if g.get("nodes"):
        try:
            from modules.database.session_repo import get_session_repo
            get_session_repo().set_session_metadata(
                session_id, {"session_graph": store.snapshot(session_id)})
        except Exception as _swallowed_err:
            logger.debug(f"[get_session_graph] ignored Exception: {_swallowed_err}")
    return {"success": True, "data": {"session_id": session_id, "graph": g}}


@router.get("/session/{session_id}/tasks")
async def get_tasks(session_id: str):
    """读取会话的定时任务配置（每会话独立）"""
    cfg = _get_chat_session_repo().get_scheduled_tasks(session_id)
    return {"success": True, "data": {"session_id": session_id, "tasks": cfg}}


@router.put("/session/{session_id}/tasks")
async def set_tasks(session_id: str, body: dict = None):
    """写入会话的定时任务配置 {"tasks": [{"id","time","enabled","action","prompt"}]}"""
    cfg = (body or {}).get("tasks")
    if not isinstance(cfg, dict) or "tasks" not in cfg:
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR",
                                      "message": "tasks 需为对象 {tasks: [...]}"}})
    _get_chat_session_repo().set_scheduled_tasks(session_id, cfg)
    return {"success": True, "data": {"session_id": session_id, "tasks": cfg}}


@router.put("/session/{session_id}/title")
async def update_session_title(session_id: str, body: dict = None):
    title = ((body or {}).get("title") or "").strip()
    if not title:
        from infra.errors import AppError, ErrorCode
        raise AppError(ErrorCode.BAD_REQUEST, "标题不能为空")
    if _resolve_mode() == "chatonly":
        _get_chat_session_repo().set_session_title(session_id, title[:200])
        return {"success": True, "data": {"message": "标题已更新", "title": title[:200]}}
    from modules.thinking import api_stream
    return await api_stream.update_session_title(session_id, body)


@router.delete("/sessions/{session_id}/messages/{message_id}")
async def delete_message(session_id: str, message_id: str):
    if _resolve_mode() == "chatonly":
        # AI 消息联动删除同轮思考过程（与 agent 模式一致）
        # DB 为唯一真源：删除直接落库，读取时直连 DB，无需同步内存黑板
        _get_chat_session_repo().delete_message(session_id, message_id, include_thoughts=True)
        return {"success": True, "data": {"message": "消息已删除"}}
    from modules.thinking import api_stream
    return await api_stream.delete_message(session_id, message_id)


@router.put("/sessions/{session_id}/messages/{message_id}")
async def update_message(session_id: str, message_id: str, body: dict = None):
    content = ((body or {}).get("content") or "").strip()
    if not content:
        from infra.errors import AppError, ErrorCode
        raise AppError(ErrorCode.BAD_REQUEST, "内容不能为空")
    if _resolve_mode() == "chatonly":
        # DB 为唯一真源：更新直接落库，读取时直连 DB，无需同步内存黑板
        _get_chat_session_repo().update_message(session_id, message_id, content)
        return {"success": True, "data": {"message": "消息已更新", "content": content}}
    from modules.thinking import api_stream
    return await api_stream.update_message(session_id, message_id, body)


@router.get("/status")
async def get_status():
    if _resolve_mode() == "chatonly":
        return {"success": True, "data": {"running": True, "sessions": 0, "running_sessions": 0}}
    from modules.thinking import api_stream
    return await api_stream.get_status()


@router.get("/sessions")
async def get_sessions():
    if _resolve_mode() == "chatonly":
        ensure_shared_schema()
        # 清理空会话（voice_* 残留立即删，普通空会话按闲置时长）
        try:
            from modules.thinking.api_stream import connection_manager
            _get_chat_session_repo().delete_empty_sessions(
                # 排除所有活跃前端出口（WebSocket ∪ SSE），否则纯 SSE 会话可能被当成空会话删掉
                exclude_ids=connection_manager.push_sessions(),
                min_idle_minutes=10,
            )
        except Exception as _swallowed_err:
            logger.debug(f"[get_sessions] ignored Exception: {_swallowed_err}")
        sessions = _get_chat_session_repo().get_all_sessions(limit=50)
        return {"success": True, "data": sessions}
    from modules.thinking import api_stream
    return await api_stream.get_sessions()


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, limit: int = 100):
    if _resolve_mode() == "chatonly":
        ensure_shared_schema()
        messages = _get_chat_session_repo().get_messages(session_id, limit=limit)
        return {"success": True, "data": messages}
    from modules.thinking import api_stream
    return await api_stream.get_session_messages(session_id, limit)


@router.get("/sessions/{session_id}/events")
async def get_session_events(session_id: str, after_seq: int = 0, limit: int = 500):
    """会话事件日志（Phase 1）— 刷新/重开会话时按 seq 回放过程视图。

    与 WS 帧同构（`{type, event, content, data, _seq}`），agent/chatonly
    共用同一张表；chatonly 目前不写事件，因此返回空列表。
    """
    try:
        from modules.database.session_events import get_session_event_repo
        repo = get_session_event_repo()
        events = repo.list_events(session_id, after_seq=after_seq, limit=limit)
        last_seq = max(repo.last_seq(session_id), events[-1]["_seq"] if events else 0)
        return {"success": True, "data": {"events": events, "last_seq": last_seq}}
    except Exception as e:
        logger.debug(f"[EventLog] 读取会话事件失败: {e}")
        return {"success": True, "data": {"events": [], "last_seq": 0}}


@router.get("/sessions/{session_id}/projections")
async def get_session_projections(session_id: str):
    """会话投影（Phase 4）— 一次读回派生状态整值快照。

    返回 `{key, value, seq}` 列表（同 WS `session_projection` 帧的 data），
    前端按 key 覆盖、按 seq 丢弃旧值；agent/chatonly 共用。
    """
    try:
        from modules.thinking import projections
        return {"success": True, "data": {"projections": projections.current_projections(session_id)}}
    except Exception as e:
        logger.debug(f"[Projection] 读取会话投影失败: {e}")
        return {"success": True, "data": {"projections": []}}


@router.post("/stop")
async def stop_thinking(body: dict = None, session_id: str = ""):
    if _resolve_mode() == "chatonly":
        # 真正取消该会话运行中的思考任务（此前是空操作）
        task = _CHATONLY_TASKS.get(session_id)
        if task and not task.done():
            task.cancel()
        return {"success": True, "data": {"message": "已发送停止信号", "cancelled": bool(task and not task.done())}}
    from modules.thinking import api_stream
    return await api_stream.stop_thinking(body, session_id)
