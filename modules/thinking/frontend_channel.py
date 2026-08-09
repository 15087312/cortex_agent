"""统一前端推送出口 — 握手确认 + 连续持久化 + WS 推送

所有"大模型生成内容 → 推送到前端"的路径必须走本模块的统一出口，不再各自实现：

    1. confirm_frontend_connection()   — LLM 调用前握手，确认前端可达（不可达则跳过 LLM）
    2. push_content()                  — LLM 后统一推送：先无条件持久化到会话历史（连续持久化，
                                         防前端中途退出丢消息，重连可从历史恢复），再 WS 实时送达
    3. generate_and_push()             — 统一流程：握手 → LLM → 持久化 → 推送

调用方只需传入自己的 LLM 协程 + 事件参数，握手/持久化/推送细节全部在本模块内统一实现，
避免各推送点零散重复实现、遗漏握手或遗漏持久化。
"""
import asyncio
import threading
from typing import Any, Awaitable, Callable, Dict, Optional

from utils.logger import setup_logger

logger = setup_logger("frontend_channel")

# 通过模块引用访问 api_stream（避免 import 时绑定快照，便于测试替换 connection_manager 等）
import modules.thinking.api_stream as _api_stream  # noqa: E402


def _connections():
    return _api_stream.connection_manager


def _build_event(*args, **kwargs):
    return _api_stream._build_event(*args, **kwargs)


def _get_thinking_system():
    return _api_stream.get_thinking_system()


def _main_event_loop():
    return _api_stream._main_event_loop


def confirm_frontend_connection(session_id: str = None) -> bool:
    """LLM 调用前握手：确认前端 WS 推送链路可达。

    - session_id=None：向所有活跃连接发握手事件，任一送达 → True（用于广播场景）
    - session_id=指定：仅确认该 session 的连接（用于按会话推送场景）

    握手事件 content 为空，前端 `_onProactive` 等处理器会忽略，不污染消息流。
    返回 False 时调用方应跳过 LLM 调用——避免模型生成内容后因前端离线而丢失（白耗 API）。
    """
    try:
        cm = _connections()
        if not cm.active_connections:
            return False
        event = _build_event(
            session_id="",
            msg_type="proactive",
            event="proactive_handshake",
            content="",
            role="system",
            data={"handshake": True},
        )
        candidates = [session_id] if session_id else list(cm.active_connections.keys())
        for sid in candidates:
            if cm.send_json_from_thread(sid, event):
                return True
        return False
    except Exception as e:
        logger.debug(f"[连接确认] 失败: {e}")
        return False


def _run_async(coro):
    """在独立线程中同步执行异步协程（不阻塞主事件循环）"""
    async def _run_task_wrapped():
        return await asyncio.create_task(coro)
    return asyncio.run(_run_task_wrapped())


async def _persist_message(session_id: str, role: str, content: str) -> str:
    """连续持久化：无条件写入会话历史（agent 内存会话或 chatonly DB），返回消息 id。

    消息始终落库/落会话——前端任意时刻断线，重连后都能从历史恢复看到。
    """
    msg_id = ""
    try:
        system = _get_thinking_system()
        if session_id in system.sessions:
            # 必须提交到主事件循环：_append_message 用主 loop 的 asyncio.Lock，
            # 在 daemon 线程里 asyncio.run 新 loop 直接调用会跨 loop 报错。
            loop = _connections()._loop or _main_event_loop()
            if loop and not loop.is_closed():
                fut = asyncio.run_coroutine_threadsafe(
                    system._append_message(session_id, role, content), loop)
                msg_id = fut.result(timeout=10)
            else:
                msg_id = _run_async(system._append_message(session_id, role, content))
        else:
            # chatonly 等非 agent 内存会话：直接落 DB（会话记忆由 DB 恢复，前端可追溯）
            from modules.database.session_repo import get_session_repo
            msg_id = get_session_repo().save_message(session_id, role, content)
    except Exception as e:
        logger.error(f"消息持久化失败: {e}")
    return msg_id


async def push_content(
    session_id: str,
    *,
    msg_type: str,
    event: str,
    content: str,
    role: str = "assistant",
    data: Optional[Dict[str, Any]] = None,
    persist: bool = True,
) -> bool:
    """统一推送出口：连续持久化 + WS 推送。

    始终先持久化（防中途退出丢消息），再实时推送到活跃 WS 连接。
    返回是否实时送达（False 表示前端当前离线但消息已持久化，重连后可恢复）。
    """
    msg_id = ""
    if persist:
        msg_id = await _persist_message(session_id, role, content)

    event_obj = _build_event(
        session_id=session_id,
        msg_type=msg_type,
        event=event,
        content=content,
        role=role,
        data={**(data or {}), "message_id": msg_id},
    )

    sent_any = False
    try:
        cm = _connections()
        for sid in list(cm.active_connections.keys()):
            if cm.send_json_from_thread(sid, event_obj):
                sent_any = True
        if not sent_any and persist:
            logger.warning(
                f"[前端通道] 无活跃 WebSocket 连接，消息已持久化到会话历史 "
                f"(session={session_id[:8]}, event={event})"
            )
    except Exception as e:
        logger.error(f"消息推送失败: {e}")
    return sent_any


async def generate_and_push(
    session_id: str,
    llm_fn: Callable[[], Awaitable[str]],
    *,
    msg_type: str,
    event: str,
    role: str = "assistant",
    data: Optional[Dict[str, Any]] = None,
    persist: bool = True,
    handshake_session_id: Optional[str] = None,
) -> Optional[str]:
    """统一流程：握手确认 → LLM 生成 → 持久化 → 推送。

    握手失败（前端不可达）时跳过 LLM 调用并返回 None——省 token、不产生无处送达的内容。
    返回生成的文本；LLM 返回空或握手失败返回 None。

    - session_id：推送/持久化归属的会话
    - handshake_session_id：握手确认的会话。
      默认 None=广播（任一前端在线即可）——主动搭话/定时任务等"广播给所有连接"的
      场景目标 session 未必是前端当前连接，必须广播握手，否则会因该 session 无连接
      而错误跳过 LLM。按会话对话（如 api_stream.think）才显式传具体 session。
    """
    if not confirm_frontend_connection(handshake_session_id):
        logger.debug(
            f"[前端通道] 前端不可达，跳过 LLM 调用 (handshake={handshake_session_id or '(广播)'})"
        )
        return None
    text = await llm_fn()
    if not text or not str(text).strip():
        return None
    text = str(text).strip()
    await push_content(
        session_id,
        msg_type=msg_type,
        event=event,
        content=text,
        role=role,
        data=data,
        persist=persist,
    )
    return text


# 模块级锁（预留：若未来需要串行化握手）
_handshake_lock = threading.Lock()
