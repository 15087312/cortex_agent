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


class PetChatRequest(BaseModel):
    action_id: str = ""
    text: str = ""


class PetMoveRequest(BaseModel):
    dx: float = 0
    dy: float = 0
    active: Optional[bool] = None


_pet_move = {"dx": 0.0, "dy": 0.0, "active": False}
_pet_move_queues: set = set()  # SSE 推送连接（Qt 长连接收位移，替代轮询）

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

async def _consume_turn(
    websocket: WebSocket,
    session_id: str,
    repo,
    thinker,
    content: str,
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
        session_id, content, queue,
    ))
    full_text = []
    done = False
    errored = False
    turn_start = time.time()
    last_progress = turn_start
    last_event = turn_start      # 最后一次收到队列事件的时间（判断真超时）
    flush_buf: list = []         # token 聚合缓冲，避免逐 token 刷屏

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
            elif tok.get("type") == "thinking":
                # deepseek 思考过程（reasoning）单独推送，与回复区分。
                # 持久化 role="thought"（与 agent 模式 _persist_thought 一致），
                # 切换会话后思考历史可恢复；上下文恢复按 user/assistant 过滤不污染模型。
                _thought = tok.get("content", "")
                if _thought:
                    try:
                        repo.save_message(session_id, "thought", _thought)
                    except Exception:
                        pass
                if not await _safe_ws_send(websocket, _envelope(
                    session_id, "thinking", "thinking_step",
                    _thought, "thinking",
                    data={"source": "reasoning",
                          "identity_name": tok.get("identity_name", "总指挥"),
                          "tier": tok.get("tier", "large")},
                )): pass
            elif tok.get("type") == "mental":
                # 心理活动（conscience 内心独白）：持久化 role="mental"（与对话同款），
                # 前端切换会话后历史可恢复；上下文恢复时按 role 过滤不污染模型输入
                _mental = tok.get("content", "")
                if _mental:
                    try:
                        repo.save_message(session_id, "mental", _mental)
                    except Exception:
                        pass
                if not await _safe_ws_send(websocket, _envelope(
                    session_id, "mental", "mental",
                    _mental, "system",
                    data={"label": "心理活动"},
                )): pass
            elif tok.get("type") == "done":
                # 思考过程一次性完整发送（不逐段流式，避免前端一小句一小句冒出来）
                if flush_buf:
                    if not await _safe_ws_send(websocket, _envelope(
                        session_id, "thinking", "thinking_step",
                        "".join(flush_buf), "thinking",
                    )): pass
                    flush_buf.clear()
                done = True
                break
            elif tok.get("type") == "error":
                if flush_buf:
                    if not await _safe_ws_send(websocket, _envelope(
                        session_id, "thinking", "thinking_step",
                        "".join(flush_buf), "thinking",
                    )): pass
                    flush_buf.clear()
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
        repo.save_message(session_id, "assistant", text)
        if not await _safe_ws_send(websocket, _envelope(
            session_id, "message", "assistant_message", text, "main",
        )): pass
    # 失败/取消路径：只发过 error，不再发「处理完成」，避免语义矛盾
    if not errored:
        if not await _safe_ws_send(websocket, 
            _envelope(session_id, "done", "done", "处理完成", "system")
        ): pass


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
    except Exception:
        pass

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
            except Exception:
                pass
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
                        if str(getattr(settings, "CHAT_IMAGE_MODE", "describe") or "describe").lower() == "direct":
                            att_images = extract_images(attachments)
                            att_text = summarize_attachments(attachments)
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
                    except asyncio.CancelledError:
                        pass
                # 当前回合图片（直连多模态）：透传给本轮大模型请求
                from modules.thinking.turn_images import set_turn_images
                set_turn_images(att_images or None)
                active_task = asyncio.create_task(
                    _consume_turn(websocket, session_id, repo, _get_chat_thinker(), content)
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
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass


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
    turn_start = time.time()
    last_progress = turn_start
    last_event = turn_start      # 最近一次收到事件的时间（判断真超时，与 WS 语义一致）

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
                full_text.append(tok.get("content", ""))
            elif tok.get("type") == "done":
                break
            elif tok.get("type") == "error":
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
            repo.save_message(session_id, "assistant", text)
            yield {
                "event": "assistant_message",
                "data": json.dumps(
                    _envelope(session_id, "message", "assistant_message", text, "main"),
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
            from api.errors import AppError, ErrorCode
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
        # 同步到 api_stream 会话缓存
        if session_id in _api.sessions:
            _api.sessions[session_id]["messages"] = ctx_messages
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
    pet_session = getattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main") or "pet_main"
    if session_id == pet_session:
        return JSONResponse(status_code=400, content={"success": False,
                            "error": {"code": "PET_SESSION_PROTECTED",
                                      "message": "桌宠主会话永不删除"}})
    # 1️⃣ 统一从 DB 删除会话及消息
    _get_chat_session_repo().delete_session(session_id)
    # 2️⃣ 关键修复：清理跨模块的进程内存缓存，防止重新打开/请求时仍加载旧数据
    try:
        from modules.thinking.model_runner import _session_memory_context, _resume_context
        _session_memory_context.pop(session_id, None)
        _resume_context = getattr(_resume_context, None, None)  # 清除引用（实际以 session 为键的 dict 在 model_runner 同名变量中）
        # 若 _resume_context 是 dict，也按 session_id 删键
        if isinstance(_resume_context, dict):
            _resume_context.pop(session_id, None)
    except Exception:
        pass
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
    pet_session = getattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main") or "pet_main"
    deleted = []
    for sid in ids:
        if not isinstance(sid, str) or not sid:
            continue
        if sid == pet_session:
            continue
        try:
            repo.delete_session(sid)
        except Exception:
            pass
        deleted.append(sid)
    return {"success": True, "data": {"deleted": deleted, "count": len(deleted)}}


@router.get("/pet/last-reply")
async def pet_last_reply():
    """桌宠最近一条回复（桌宠窗口轮询）"""
    try:
        from modules.desktop_pet.pet_engine import PetEngine
        return {"success": True, "data": PetEngine.get_instance().last_reply or None}
    except Exception:
        return {"success": True, "data": None}


@router.get("/pet/state")
async def pet_state():
    """桌宠当前状态（状态栏轮询）"""
    try:
        from modules.desktop_pet.pet_state import PetState
        st = PetState.get_instance()
        values = st.read()
        return {"success": True, "data": {"values": values, "text": st.describe(values)}}
    except Exception as e:
        return {"success": True, "data": None}


@router.get("/pet/actions")
async def pet_actions():
    """桌宠互动动作模板（圆环菜单 + Chat 图标检测统一数据源）"""
    from modules.desktop_pet.actions import CATEGORIES, public_actions
    return {"success": True, "data": {"categories": CATEGORIES, "actions": public_actions()}}


@router.post("/pet/move")
async def pet_move(body: PetMoveRequest):
    """桌宠拖动位移累积，并实时推送 SSE 给 Qt（规避 QWebChannel 段错误，无需轮询）"""
    _pet_move["dx"] += float(body.dx or 0)
    _pet_move["dy"] += float(body.dy or 0)
    if body.active is not None:
        _pet_move["active"] = bool(body.active)
    if _pet_move["dx"] or _pet_move["dy"]:
        m = {"dx": _pet_move["dx"], "dy": _pet_move["dy"], "active": _pet_move["active"]}
        _pet_move["dx"] = 0.0
        _pet_move["dy"] = 0.0
        for q in list(_pet_move_queues):
            try:
                q.put_nowait(m)
            except Exception:
                pass
    return {"success": True}


@router.get("/pet/move/stream")
async def pet_move_stream():
    """SSE 长连接：Qt 收桌宠拖动位移推送（替代 GET /pet/move 轮询）"""
    from sse_starlette.sse import EventSourceResponse
    queue: "asyncio.Queue" = asyncio.Queue()
    _pet_move_queues.add(queue)

    async def gen():
        try:
            while True:
                m = await queue.get()
                yield {"event": "move", "data": json.dumps(m, ensure_ascii=False)}
        except asyncio.CancelledError:
            pass
        finally:
            _pet_move_queues.discard(queue)

    return EventSourceResponse(gen())


@router.post("/pet/state/reset")
async def pet_state_reset():
    """重置桌宠状态为默认（设置页用）"""
    from modules.desktop_pet.pet_state import PetState, DEFAULTS
    st = PetState.get_instance()
    st._values = dict(DEFAULTS)
    st._updated_at = time.time()
    st._save()
    return {"success": True, "data": {"values": st.read()}}


@router.post("/pet/chat")
async def pet_chat_stream(body: PetChatRequest):
    """互动对话（SSE 流式）：应用状态效果 → 状态注入提示词 → 流式 LLM → 保存会话"""
    from sse_starlette.sse import EventSourceResponse

    async def gen():
        try:
            from modules.desktop_pet.pet_state import PetState
            from modules.desktop_pet.actions import get_action
            from modules.desktop_pet.pet_engine import PetEngine

            state = PetState.get_instance()
            action_id = (body.action_id or "").strip()
            if action_id and get_action(action_id):
                before = state.read()
                state.apply(action_id)
                prompt = get_action(action_id)["prompt"]
                text = prompt
                extra_system = state.describe(before)
            else:
                text = (body.text or "").strip()
                extra_system = state.describe(state.read())
            if not text:
                yield {"event": "error", "data": json.dumps({"error": "empty text"}, ensure_ascii=False)}
                return

            engine = PetEngine.get_instance()
            async for token in engine.stream_chat(text, extra_system=extra_system):
                yield {"event": "token", "data": json.dumps({"token": token}, ensure_ascii=False)}
            new_state = state.read()
            yield {"event": "done", "data": json.dumps(
                {"state": new_state, "state_text": state.describe(new_state)},
                ensure_ascii=False,
            )}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(gen())


@router.delete("/session/{session_id}/messages")
async def clear_session_messages(session_id: str):
    """清空会话全部消息（保留会话本身），前端「清空对话」真实生效"""
    _get_chat_session_repo().clear_messages(session_id)
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
        except Exception:
            pass
    # 返回前持久化：前端每次读图谱都确保 metadata 最新，
    # 切换会话/重启后端后能恢复一致（不只是思考结束 finally 才写）
    if g.get("nodes"):
        try:
            from modules.database.session_repo import get_session_repo
            get_session_repo().set_session_metadata(
                session_id, {"session_graph": store.snapshot(session_id)})
        except Exception:
            pass
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


@router.get("/session/{session_id}/outreach-config")
async def get_outreach_config(session_id: str):
    """读取会话的主动搭话配置（agent/chatonly 同一套，存 chat_sessions.metadata_json）"""
    cfg = _get_chat_session_repo().get_outreach_config(session_id)
    return {"success": True, "data": {"session_id": session_id, "outreach": cfg}}


@router.get("/proactive-log")
async def get_proactive_logs(limit: int = 50, session_id: str = ""):
    """主动搭话触发记录（时间/会话/触发原因/内容）"""
    from modules.database.proactive_repo import query_proactive_logs, count_proactive_logs
    logs = query_proactive_logs(limit=limit, session_id=session_id)
    total = count_proactive_logs()
    return {"success": True, "data": {"logs": logs, "total": total}}


@router.put("/session/{session_id}/outreach-config")
async def set_outreach_config(session_id: str, body: dict = None):
    """写入会话的主动搭话配置（会话级独立规则）

    {
      enabled: bool,
      cooldown_minutes: int,                    # 综合冷却（距上次任意主动搭话）
      schedule: {time: "HH:MM", jitter_minutes: int},          # 定点发送（误差随机）
      screen: {change_ratio, probability, check_interval_seconds, cooldown_minutes},  # 屏幕触发
      idle: {idle_minutes, probability, check_interval_seconds},                      # 空闲触发
      time_windows: [{start, end, probability, check_interval_seconds}, ...]          # 时段触发
    }
    """
    cfg = (body or {}).get("outreach")
    if not isinstance(cfg, dict):
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR", "message": "outreach 需为对象"}})

    repo = _get_chat_session_repo()

    # 最多 5 个 enabled 会话
    if cfg.get("enabled"):
        current = repo.get_outreach_config(session_id)
        if not current.get("enabled"):
            enabled_count = sum(
                1 for s in repo.get_all_sessions(limit=100)
                if (s.get("metadata") or {}).get("outreach", {}).get("enabled")
            )
            if enabled_count >= 5:
                return JSONResponse(status_code=422, content={"success": False,
                                    "error": {"code": "LIMIT_EXCEEDED",
                                              "message": "最多 5 个会话可开启主动搭话，请先关闭其他会话"}})

    clean: Dict[str, Any] = {}
    if "enabled" in cfg:
        clean["enabled"] = bool(cfg["enabled"])
    if "cooldown_minutes" in cfg:
        try:
            clean["cooldown_minutes"] = max(0, int(cfg["cooldown_minutes"]))
        except Exception:
            pass
    # schedule
    sched = cfg.get("schedule")
    if isinstance(sched, dict):
        cs: dict = {}
        if "enabled" in sched:
            cs["enabled"] = bool(sched["enabled"])
        if sched.get("time"):
            cs["time"] = str(sched["time"]).strip()
        if "jitter_minutes" in sched:
            try:
                cs["jitter_minutes"] = max(0, int(sched["jitter_minutes"]))
            except Exception:
                pass
        clean["schedule"] = cs
    # screen
    scr = cfg.get("screen")
    if isinstance(scr, dict):
        cs = {}
        if "enabled" in scr:
            cs["enabled"] = bool(scr["enabled"])
        if "change_ratio" in scr:
            try:
                cs["change_ratio"] = max(0.0, min(1.0, float(scr["change_ratio"])))
            except Exception:
                pass
        if "probability" in scr:
            try:
                cs["probability"] = max(0.0, min(1.0, float(scr["probability"])))
            except Exception:
                pass
        if "check_interval_seconds" in scr:
            try:
                cs["check_interval_seconds"] = max(1, int(scr["check_interval_seconds"]))
            except Exception:
                pass
        if "cooldown_minutes" in scr:
            try:
                cs["cooldown_minutes"] = max(0, int(scr["cooldown_minutes"]))
            except Exception:
                pass
        clean["screen"] = cs
    # idle
    idle = cfg.get("idle")
    if isinstance(idle, dict):
        cs = {}
        if "enabled" in idle:
            cs["enabled"] = bool(idle["enabled"])
        if "idle_minutes" in idle:
            try:
                cs["idle_minutes"] = max(0, int(idle["idle_minutes"]))
            except Exception:
                pass
        if "probability" in idle:
            try:
                cs["probability"] = max(0.0, min(1.0, float(idle["probability"])))
            except Exception:
                pass
        if "check_interval_seconds" in idle:
            try:
                cs["check_interval_seconds"] = max(1, int(idle["check_interval_seconds"]))
            except Exception:
                pass
        clean["idle"] = cs
    if "time_windows_enabled" in cfg:
        clean["time_windows_enabled"] = bool(cfg["time_windows_enabled"])
    # time_windows
    if isinstance(cfg.get("time_windows"), list):
        windows = []
        for w in cfg["time_windows"]:
            if isinstance(w, dict) and w.get("start") and w.get("end"):
                cw: dict = {"start": str(w["start"]).strip(), "end": str(w["end"]).strip()}
                if "probability" in w:
                    try:
                        cw["probability"] = max(0.0, min(1.0, float(w["probability"])))
                    except Exception:
                        pass
                if "check_interval_seconds" in w:
                    try:
                        cw["check_interval_seconds"] = max(1, int(w["check_interval_seconds"]))
                    except Exception:
                        pass
                windows.append(cw)
        clean["time_windows"] = windows

    ok = repo.set_outreach_config(session_id, clean)
    if not ok:
        return JSONResponse(status_code=404, content={"success": False,
                            "error": {"code": "NOT_FOUND", "message": "会话不存在"}})
    return {"success": True, "data": {"session_id": session_id, "outreach": clean}}


@router.put("/session/{session_id}/title")
async def update_session_title(session_id: str, body: dict = None):
    title = ((body or {}).get("title") or "").strip()
    if not title:
        from api.errors import AppError, ErrorCode
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
        from api.errors import AppError, ErrorCode
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
                exclude_ids=list(connection_manager.active_connections.keys()),
                min_idle_minutes=10,
            )
        except Exception:
            pass
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
