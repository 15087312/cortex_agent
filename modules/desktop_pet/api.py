"""桌宠 HTTP API — 桌宠专属路由

原挂载于 modules/thinking/chat_gateway.py 的 /stream/pet/* 路由。
迁至桌面宠物模块自身，使 thinking 不再反向依赖 desktop_pet。

路径保持 /stream/pet/* 不变（兼容 Qt/前端既有调用）。
"""
import asyncio
import json
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

from utils.logger import setup_logger

logger = setup_logger("desktop_pet_api")


class PetChatRequest(BaseModel):
    action_id: str = ""
    text: str = ""


class PetMoveRequest(BaseModel):
    dx: float = 0
    dy: float = 0
    active: Optional[bool] = None


class PetConfigRequest(BaseModel):
    persona: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_model: Optional[str] = None
    api_format: Optional[str] = None
    api_provider: Optional[str] = None
    loop_type: Optional[str] = None
    memory_enabled: Optional[bool] = None
    # 桌宠专属循环（CustomLoop）参数
    min_think_interval: Optional[float] = None
    tools_enabled: Optional[bool] = None
    max_history: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_loop_rounds: Optional[int] = None
    idle_backoff_max: Optional[float] = None
    voice_enabled: Optional[bool] = None
    realtime_voice: Optional[bool] = None
    active_persona_id: Optional[int] = None


class PetPersonaRequest(BaseModel):
    name: str = ""
    prompt: str = ""


class PetMemoryRequest(BaseModel):
    kind: str = "fact"
    text: str = ""


_pet_move = {"dx": 0.0, "dy": 0.0, "active": False}
_pet_move_queues: set = set()  # SSE 推送连接（Qt 长连接收位移，替代轮询）

router = APIRouter(prefix="/stream/pet", tags=["桌宠"])


@router.get("/last-reply")
async def pet_last_reply():
    """桌宠最近一条回复（桌宠窗口轮询）"""
    try:
        from modules.desktop_pet.pet_engine import PetEngine
        return {"success": True, "data": PetEngine.get_instance().last_reply or None}
    except Exception:
        return {"success": True, "data": None}


@router.get("/actions")
async def pet_actions():
    """桌宠互动动作模板（圆环菜单 + Chat 图标检测统一数据源）"""
    from modules.desktop_pet.actions import CATEGORIES, public_actions
    return {"success": True, "data": {"categories": CATEGORIES, "actions": public_actions()}}


@router.post("/move")
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
            except Exception as _swallowed_err:
                logger.debug(f"[pet_move] ignored Exception: {_swallowed_err}")
    return {"success": True}


@router.get("/move/stream")
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
        except asyncio.CancelledError as _swallowed_err:
            logger.debug(f"[gen] ignored asyncio.CancelledError: {_swallowed_err}")
        finally:
            _pet_move_queues.discard(queue)

    return EventSourceResponse(gen())


@router.post("/chat")
async def pet_chat_stream(body: PetChatRequest):
    """互动对话（SSE 流式）：互动提示词 → 独立 ReAct 循环 → 保存独立历史"""
    from sse_starlette.sse import EventSourceResponse
    from modules.desktop_pet.pet_engine import PetEngine

    # 总开关关闭：不回复（自动/手动都拒绝），前端也会禁用输入
    if not PetEngine.get_instance().enabled:
        raise HTTPException(status_code=409, detail="desktop pet disabled")

    async def gen():
        try:
            from modules.desktop_pet.actions import get_action
            from modules.desktop_pet.pet_engine import PetEngine
            from modules.desktop_pet.store import PetStore

            action_id = (body.action_id or "").strip()
            if action_id and get_action(action_id):
                text = get_action(action_id)["prompt"]
            else:
                text = (body.text or "").strip()
            extra_system = ""
            if not text:
                # 主动思考模式：空输入 = 桌宠自己决定说什么（页面加载自动触发）。
                # 注：这个系统提示必须重申「根据上文继续、不要每次都重复同一句」——
                # 模型熵太低时容易无限复述上一轮的话，断言「每次新内容/新方向」才能打破。
                extra_system = (
                    "【主动思考——自言自语 / 延续 / 反思 / 暂停】\n"
                    "目前没有用户的新输入。这是你自己的主动思考时间，你可以：\n"
                    "- 根据当前对话历史进行自言自语的思考，梳理自己的想法\n"
                    "- 对上一段对话进行自然延续或追问\n"
                    "- 对之前的对话或行为进行反思和总结\n"
                    "- 做一些你自己想做的事情，比如查一些用户可能会关心的新闻、"
                    "娱乐八卦、技术进展，或者看看本地有什么东西，用你的工具探索这个世界\n"
                    "- 暂停对话：如果你暂时没什么想说的，直接输出空文本，"
                    "系统会给你一段安静的休息时间，等你有新想法或新话题时再继续\n\n"
                    "提示：请不要每次都复述同一句话——这不是思考，是卡住了。"
                    "每一轮都应该有新的内容、新的方向，或者选择安静休息。\n"
                    "回复简短自然（一两句话，适合语音朗读），用中文。\n"
                    "请以你自己的语气、节奏、自然的犹豫/停顿来回答——像真人一样。"
                )

            engine = PetEngine.get_instance()
            # 思考过程实时转发（同步回调 → 队列 → SSE thinking 帧）
            reason_q: asyncio.Queue = asyncio.Queue()
            token_q: asyncio.Queue = asyncio.Queue()
            reply_parts: list = []  # 累积流式 token，供语音合成完整回复文本

            def _on_reasoning(chunk: str):
                try:
                    reason_q.put_nowait(chunk)
                except Exception as _swallowed_err:
                    logger.debug(f"[_on_reasoning] ignored Exception: {_swallowed_err}")

            stream_gen = engine.stream_chat(text, extra_system=extra_system, on_reasoning=_on_reasoning)
            # 语音播放开关（桌宠独立配置，设置页控制）：开启时流结束后合成音频，
            # done 帧回传 /audio/xxx.mp3，前端 <audio> 播放（gTTS/mp3 浏览器原生支持）
            voice_on = bool(PetStore.get_instance().get_config().get("voice_enabled"))

            # 独立 pump 任务消费流式生成器：避免 wait_for(anext) 取消注入
            # CancelledError 触发 stream_chat 的 task.cancel() 误杀正在跑模型的 run()
            async def _pump():
                try:
                    async for t in stream_gen:
                        await token_q.put(("token", t))
                except Exception as e:
                    await token_q.put(("error", str(e)))
                finally:
                    await token_q.put(("done", None))

            pump = asyncio.create_task(_pump())
            try:
                while True:
                    # 先泵出已到达的思考片段
                    while not reason_q.empty():
                        yield {"event": "thinking", "data": json.dumps({"chunk": reason_q.get_nowait()}, ensure_ascii=False)}
                    try:
                        kind, val = await asyncio.wait_for(token_q.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        continue
                    if kind == "token":
                        if val == "\x00ROUND_DONE\x00":
                            # 一轮完整回复已产出：前端结束"接收中"，准备下一轮思考
                            yield {"event": "round_complete", "data": json.dumps({"round_complete": True}, ensure_ascii=False)}
                        else:
                            reply_parts.append(val)
                            yield {"event": "token", "data": json.dumps({"token": val}, ensure_ascii=False)}
                    elif kind == "error":
                        yield {"event": "error", "data": json.dumps({"error": val}, ensure_ascii=False)}
                        break
                    else:
                        break
            finally:
                if not pump.done():
                    pump.cancel()
            # 收尾：补发剩余思考片段
            while not reason_q.empty():
                yield {"event": "thinking", "data": json.dumps({"chunk": reason_q.get_nowait()}, ensure_ascii=False)}
            reply_text = "".join(reply_parts).strip()
            if voice_on and reply_text:
                # 语音只读正文：清洗混入正文的 thinking 标签（与 react_loop 同规则），
                # 并过滤非发声符号，避免朗读出思维链/标记符号
                reply_text = re.sub(r"<think\b[^>]*>.*?</think>", "", reply_text, flags=re.DOTALL | re.IGNORECASE)
                reply_text = re.sub(r"</?think\b[^>]*>", "", reply_text, flags=re.IGNORECASE)
                reply_text = reply_text.replace("*", "").replace("#", "").strip()
            audio_url = ""
            if voice_on and reply_text:
                # 拼接完整回复文本，合成 MP3 → 静态目录（/audio 挂载）→ 相对 URL
                try:
                    from modules.output_system.tts import TTSEngine
                    tts = TTSEngine()
                    path = tts.synthesize_sync(reply_text)
                    if path:
                        audio_url = "/audio/" + os.path.basename(path)
                except Exception as e:
                    logger.debug(f"[Pet] 语音合成失败（不影响文本回复）: {e}")
            yield {"event": "done", "data": json.dumps({"audio_url": audio_url, "fallback": bool(getattr(engine, "last_fallback_reason", ""))}, ensure_ascii=False)}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(gen())


# ── 桌宠独立配置（人设 / API / 循环类型） ──

@router.get("/config")
async def pet_get_config():
    """桌宠独立配置（人设、API、循环类型、记忆开关）"""
    from modules.desktop_pet.store import PetStore
    cfg = PetStore.get_instance().get_config()
    # API Key 不回显明文，仅告知是否已配置
    out = dict(cfg)
    out["api_key_set"] = bool(cfg.get("api_key"))
    out.pop("api_key", None)
    return {"success": True, "data": out}


@router.put("/config")
async def pet_update_config(body: PetConfigRequest):
    """更新桌宠独立配置（仅更新传入字段）"""
    from modules.desktop_pet.store import PetStore
    store = PetStore.get_instance()
    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    pid = patch.pop("active_persona_id", None)
    if pid is not None and store.set_active_persona(int(pid)) is None:
        return {"success": False, "error": "persona not found"}
    cfg = store.update_config(patch) if patch else store.get_config()
    out = dict(cfg)
    out["api_key_set"] = bool(cfg.get("api_key"))
    out.pop("api_key", None)
    return {"success": True, "data": out}


# ── 桌宠人设（每个人设独立会话） ──

@router.get("/personas")
async def pet_personas():
    """人设列表 + 当前人设 id"""
    from modules.desktop_pet.store import PetStore
    store = PetStore.get_instance()
    return {
        "success": True,
        "data": {"items": store.list_personas(), "active_id": store.get_active_persona_id()},
    }


@router.post("/personas")
async def pet_persona_create(body: PetPersonaRequest):
    """新建人设（名称重复返回失败）"""
    from modules.desktop_pet.store import PetStore
    item = PetStore.get_instance().add_persona(body.name, body.prompt)
    if not item:
        return {"success": False, "error": "duplicate name"}
    return {"success": True, "data": item}


@router.put("/personas/{pid}")
async def pet_persona_update(pid: int, body: PetPersonaRequest):
    """更新人设名称/提示词"""
    from modules.desktop_pet.store import PetStore
    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    item = PetStore.get_instance().update_persona(pid, patch)
    if not item:
        return {"success": False, "error": "persona not found or duplicate name"}
    return {"success": True, "data": item}


@router.delete("/personas/{pid}")
async def pet_persona_delete(pid: int):
    """删除人设及其专属会话/记忆库/因果图（禁止删除最后一个人设）"""
    from modules.desktop_pet.store import PetStore
    ok = PetStore.get_instance().delete_persona(pid)
    if not ok:
        return {"success": False, "error": "cannot delete last persona or not found"}
    try:
        from modules.desktop_pet.memory_bank import PetMemoryBank
        PetMemoryBank.get_instance().delete_persona(pid)
    except Exception as e:
        logger.debug(f"[pet] 清理人设记忆文件失败: {e}")
    return {"success": True}


@router.post("/personas/{pid}/activate")
async def pet_persona_activate(pid: int):
    """切换当前人设；后续对话/历史都切到该人设"""
    from modules.desktop_pet.store import PetStore
    item = PetStore.get_instance().set_active_persona(pid)
    if not item:
        return {"success": False, "error": "persona not found"}
    return {"success": True, "data": item}


@router.get("/memory/events")
async def pet_memory_events(limit: int = 50):
    """当前人设的自动记忆事件（按人设隔离）"""
    from modules.desktop_pet.store import PetStore
    from modules.desktop_pet.memory_bank import PetMemoryBank
    pid = PetStore.get_instance().get_active_persona_id()
    try:
        events = PetMemoryBank.get_instance().store(pid).list_events(
            limit=max(1, min(200, limit))
        )
    except Exception as e:
        logger.debug(f"[pet] 读取自动记忆失败: {e}")
        events = []
    items = [
        {
            "id": ev.id,
            "fact": ev.fact,
            "thought": ev.thought,
            "lesson": ev.lesson,
            "keywords": ev.keywords,
            "importance": ev.importance,
            "type": ev.type,
            "time": ev.time,
            "causal_node_ids": ev.causal_node_ids,
        }
        for ev in events
    ]
    return {"success": True, "data": {"items": items, "persona_id": pid}}


@router.get("/memory/stats")
async def pet_memory_stats():
    """当前人设的自动记忆事件数 / 因果节点数 / 边数"""
    from modules.desktop_pet.store import PetStore
    from modules.desktop_pet.memory_bank import PetMemoryBank
    pid = PetStore.get_instance().get_active_persona_id()
    return {"success": True, "data": {"persona_id": pid, **PetMemoryBank.get_instance().stats(pid)}}


# ── 桌宠独立对话历史 ──

@router.get("/history")
async def pet_history(limit: int = 50, persona_id: Optional[int] = None):
    """桌宠独立对话历史（默认当前人设，按 persona_id 隔离）"""
    from modules.desktop_pet.store import PetStore
    rows = PetStore.get_instance().get_history(
        limit=max(1, min(200, limit)), persona_id=persona_id,
    )
    return {"success": True, "data": {"items": rows}}


@router.delete("/history/{mid}")
async def pet_history_delete(mid: int):
    """删除桌宠对话中的一条消息（pet_history.id）"""
    from modules.desktop_pet.store import PetStore
    ok = PetStore.get_instance().delete_history(mid)
    if not ok:
        return {"success": False, "error": "not found"}
    return {"success": True}


@router.delete("/history")
async def pet_history_clear(persona_id: Optional[int] = None):
    """清空指定人设（默认当前人设）的对话历史"""
    from modules.desktop_pet.store import PetStore
    PetStore.get_instance().clear_history(persona_id=persona_id)
    return {"success": True}


# ── 桌宠独立记忆库 ──

@router.get("/memory")
async def pet_memory_list(limit: int = 50):
    """桌宠独立记忆列表"""
    from modules.desktop_pet.store import PetStore
    rows = PetStore.get_instance().list_memory(limit=max(1, min(200, limit)))
    return {"success": True, "data": {"items": rows}}


@router.post("/memory")
async def pet_memory_add(body: PetMemoryRequest):
    """新增桌宠独立记忆"""
    from modules.desktop_pet.store import PetStore
    text = (body.text or "").strip()
    if not text:
        return {"success": False, "error": "empty text"}
    item = PetStore.get_instance().add_memory(body.kind, text)
    return {"success": True, "data": item}


@router.delete("/memory/{mid}")
async def pet_memory_delete(mid: int):
    """删除一条桌宠独立记忆"""
    from modules.desktop_pet.store import PetStore
    ok = PetStore.get_instance().delete_memory(mid)
    if not ok:
        return {"success": False, "error": "not found"}
    return {"success": True}


# ── 桌宠窗口进程（Qt 展示进程的拉起/停止/状态/崩溃看护）──

@router.get("/window")
async def pet_window_status():
    """桌宠窗口进程状态（运行中/pid/重启次数/退出码）"""
    from modules.desktop_pet.window_supervisor import get_pet_window_supervisor
    return {"success": True, "data": get_pet_window_supervisor().status()}


@router.post("/window/start")
async def pet_window_start():
    """拉起桌宠窗口进程（已在跑则直接返回；崩溃后自动重启看护同步生效）"""
    from modules.desktop_pet.window_supervisor import get_pet_window_supervisor
    r = get_pet_window_supervisor().start()
    if not r.get("ok"):
        return {"success": False, "error": r.get("reason", "start failed")}
    return {"success": True, "data": r}


@router.post("/window/stop")
async def pet_window_stop():
    """停止桌宠窗口进程（用户主动关闭，不再自动重启）"""
    from modules.desktop_pet.window_supervisor import get_pet_window_supervisor
    r = get_pet_window_supervisor().stop()
    return {"success": True, "data": r}


# ── 实时语音（全双工 WebSocket） ──

@router.websocket("/voice")
async def pet_voice_websocket(websocket: WebSocket):
    """实时语音 WebSocket — 前端麦克风 PCM 音频流 → VAD → ASR → PetEngine → TTS

    仅当 PERCEPTION_VOICE_REALTIME=true 时可用。
    """
    from config.settings import settings
    from modules.desktop_pet.store import PetStore
    pet_cfg = PetStore.get_instance().get_config()
    if not pet_cfg.get("realtime_voice") and not getattr(settings, "PERCEPTION_VOICE_REALTIME", False):
        await websocket.close(code=1008, reason="Realtime voice disabled")
        return

    from modules.desktop_pet.voice.ws_handler import VoiceWebSocketHandler
    handler = VoiceWebSocketHandler(websocket)
    await handler.handle()
