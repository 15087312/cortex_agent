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

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("chat_gateway")

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
    repo.save_message(session_id, "user", content)

    # 与 api_stream 一致：先发 received ack，避免 TUI 等待超时
    if not await _safe_ws_send(websocket, _envelope(
        session_id, "ack", "received", "已接收请求，开始处理", "system",
    )): return

    queue: asyncio.Queue = asyncio.Queue()
    think_task = asyncio.create_task(thinker.think(session_id, content, queue))
    full_text = []
    done = False
    errored = False
    turn_start = time.time()
    last_progress = turn_start
    last_event = turn_start      # 最后一次收到队列事件的时间（判断真超时）
    flush_buf: list = []         # token 聚合缓冲，避免逐 token 刷屏
    flush_deadline = 0.0

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

    repo = _get_chat_session_repo()
    repo.create_session(session_id)

    try:
        await websocket.send_json(
            _envelope(session_id, "ack", "session_ready", "WebSocket 会话已建立", "system")
        )
    except Exception:
        # 客户端已断开（如 voice 会话瞬时连接）→ 直接结束
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
                active_task = asyncio.create_task(
                    _consume_turn(websocket, session_id, repo, _get_chat_thinker(), content)
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
    repo.save_message(session_id, "user", question)

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
    if _resolve_mode() == "chatonly":
        thinker = _get_chat_thinker()
        messages = thinker.get_blackboard().get_messages(session_id)
        return {
            "success": True,
            "data": {
                "session_id": session_id,
                "messages": messages,
                "count": len(messages),
            },
        }
    from modules.thinking import api_stream
    return await api_stream.get_context(session_id)


@router.delete("/session/{session_id}")
async def close_session(session_id: str):
    if _resolve_mode() == "chatonly":
        _get_chat_session_repo().delete_session(session_id)
        return {"success": True, "data": {"message": "会话已关闭"}}
    from modules.thinking import api_stream
    return await api_stream.close_session(session_id)


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
        _get_chat_session_repo().delete_message(session_id, message_id)
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
        return {"success": True, "data": {"message": "已发送停止信号"}}
    from modules.thinking import api_stream
    return await api_stream.stop_thinking(body, session_id)
