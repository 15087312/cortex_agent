"""桌面宠物引擎 — 主会话对话 + 语音触发（借鉴 airi / codex 桌宠）

- 桌宠绑定固定「主会话」pet_main（永不删除，对话记忆延续，类似 Siri）
- 订阅 SPEECH_DETECTED 语音事件 → 识别文本 → 主会话对话（LLM）→ 保存历史
  → TTS 语音回复 + 广播 pet_reply（桌宠窗口/前端展示气泡）
"""
import asyncio
import threading
import time

from typing import Optional

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("pet_engine")


class PetEngine:
    """桌宠对话引擎（单例，感知系统启动时创建）"""

    _instance: Optional["PetEngine"] = None
    _lock = threading.Lock()

    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._sub_id = ""
        self._running = False
        self._client = None
        self._reply_listeners = []  # (session_id, 回调) 前端/桌宠订阅回复
        self.last_reply = None       # {"time": epoch, "text": str} 桌宠窗口轮询取

    @classmethod
    def get_instance(cls, event_bus=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(event_bus)
        return cls._instance

    @property
    def pet_session_id(self) -> str:
        return getattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main") or "pet_main"

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "DESKTOP_PET_ENABLED", True))

    # ── 生命周期 ──

    def start(self) -> None:
        if self._running or not self.enabled:
            return
        self._running = True
        try:
            self._ensure_pet_session()
        except Exception as e:
            logger.debug(f"[Pet] 创建主会话失败: {e}")
        if self._event_bus:
            from modules.perception.events.types import PerceptionEventType
            self._sub_id = self._event_bus.subscribe(
                PerceptionEventType.SPEECH_DETECTED,
                async_handler=self._on_speech,
            )
            logger.info(f"[Pet] 桌宠已启动，主会话: {self.pet_session_id}")
        else:
            logger.warning("[Pet] 无事件总线，语音触发不可用")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._event_bus and self._sub_id:
            try:
                self._event_bus.unsubscribe(self._sub_id)
            except Exception:
                pass
            self._sub_id = ""
        logger.info("[Pet] 桌宠已停止")

    def _ensure_pet_session(self) -> None:
        """确保主会话存在（永不删除）"""
        try:
            from modules.database.session_repo import get_session_repo
            repo = get_session_repo()
            repo.create_session(self.pet_session_id)
            repo.set_session_title(self.pet_session_id, "桌宠")
        except Exception:
            pass

    # ── 语音触发 ──

    async def _on_speech(self, event) -> None:
        """语音事件 → 桌宠对话"""
        if not self.enabled:
            return
        try:
            text = (event.payload or {}).get("text", "") if hasattr(event, "payload") else ""
            if not text or not text.strip():
                return
            reply = await self.chat(text.strip())
            if not reply:
                return
            await self._after_reply(reply)
        except Exception as e:
            logger.error(f"[Pet] 语音对话失败: {e}")

    # ── 对话 ──

    async def _build_context(self, query: str = "") -> str:
        """主动搭话式上下文注入：时间/用户身份 + 感知环境 + 相关事件记忆"""
        extras = []
        try:
            from datetime import datetime
            from config.settings import settings as _cfg
            extras.append(f"【当前时间】{datetime.now().strftime('%Y-%m-%d %H:%M')}")
            extras.append(f"【对话对象】{getattr(_cfg, 'USER_NAME', '用户') or '用户'}")
        except Exception:
            pass
        try:
            from modules.thinking.context.sources.perception_source import PerceptionSource
            frag = await PerceptionSource().collect()
            if frag and getattr(frag, "content", ""):  # type: ignore[arg-type]
                extras.append(frag.content)
        except Exception:
            pass
        try:
            from modules.memory.event_retrieval import get_event_retrieval
            events = await get_event_retrieval().retrieve(
                query=query or "用户与桌宠互动", max_results=3, threshold=0.10
            )
            if events:
                lines = ["【相关过往记忆】（仅供参考，不要把过去任务当作当前任务执行）"]
                for i, ev in enumerate(events, 1):
                    date = str(getattr(ev, "time", "") or "")[:10] or "未知日期"
                    lines.append(f"  [{i}] (日期={date}) {str(getattr(ev, 'fact', ''))[:120]}")
                extras.append("\n".join(lines))
        except Exception:
            pass
        return "\n\n".join(extras)

    def _build_messages(self, text: str, extra_system: str = ""):
        """主会话历史 + 桌宠人设 + 用户消息"""
        from infra.model.large_model_client import LargeModelClient
        from infra.model.base_model import ChatMessage
        from infra.model.config_fingerprint import (
            model_config_fingerprint, close_client_session,
        )
        # 配置指纹：模型配置（URL/Key/名称）变更时自动重建，实时生效
        cfg = model_config_fingerprint("large")
        if self._client is None or getattr(self, "_client_cfg", None) != cfg:
            old = self._client
            self._client = LargeModelClient()
            self._client_cfg = cfg
            close_client_session(old)

        history = []
        try:
            from modules.database.session_repo import get_session_repo
            msgs = get_session_repo().get_recent_messages(self.pet_session_id, limit=20)
            for m in msgs:
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and content:
                    history.append(ChatMessage(role=role, content=content))
        except Exception:
            pass

        system = (
            "你是桌面上的 AI 桌宠助手，像 Siri 一样用语音和用户交流。"
            "性格友善、可爱、贴心。回答要简短自然（通常一两句话），适合语音朗读。"
            "用户可能会给你送礼物或和你互动，请配合演出、开心地回应。"
            "用中文回答。"
        )
        if extra_system:
            system = system + "\n\n" + extra_system
        return [ChatMessage(role="system", content=system)] + history + [
            ChatMessage(role="user", content=text)
        ]

    def _save_pair(self, text: str, reply: str) -> None:
        try:
            from modules.database.session_repo import get_session_repo
            repo = get_session_repo()
            repo.save_message(self.pet_session_id, "user", text)
            repo.save_message(self.pet_session_id, "assistant", reply)
        except Exception:
            pass

    async def chat(self, text: str) -> str:
        """主会话对话：主动搭话式上下文 + 历史 + 桌宠人设 + LLM → 回复

        走统一出口：LLM 前握手确认前端可达，不可达则跳过（省 token）。
        """
        try:
            # 握手：前端不可达时不调用 LLM
            from modules.thinking.frontend_channel import confirm_frontend_connection
            if not confirm_frontend_connection():
                logger.debug("[Pet] 前端不可达，跳过对话")
                return ""
            context = await self._build_context(text)
            messages = self._build_messages(text, extra_system=context)
            response = await self._client.chat(messages=messages)
            reply = (response.message.content or "").strip() if response and response.message else ""
            if not reply:
                return ""
            self._save_pair(text, reply)
            logger.info(f"[Pet] 对话: {text[:40]} → {reply[:40]}")
            return reply
        except Exception as e:
            logger.error(f"[Pet] 对话失败: {e}")
            return ""

    async def stream_chat(self, text: str, extra_system: str = ""):
        """流式对话：主会话历史 + 流式 LLM，逐 token 产出，结束后保存会话历史"""
        queue: "asyncio.Queue" = asyncio.Queue()
        collected: list = []

        # 注意：chat_stream 的 on_token 是同步回调（不 await），须用同步 put_nowait
        def on_token(t: str):
            try:
                queue.put_nowait(t)
            except Exception:
                pass

        task = asyncio.create_task(self._chat_stream_task(text, on_token, collected, extra_system))
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    token = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield token
                except asyncio.TimeoutError:
                    continue
            await task
        except Exception as e:
            logger.error(f"[Pet] 流式对话失败: {e}")
            if collected:
                yield "".join(collected)

    async def _chat_stream_task(self, text: str, on_token, collected: list, extra_system: str = "") -> None:
        try:
            # 握手：前端不可达时不调用 LLM
            from modules.thinking.frontend_channel import confirm_frontend_connection
            if not confirm_frontend_connection():
                logger.debug("[Pet] 前端不可达，跳过流式对话")
                return
            context = await self._build_context(text)
            combined = "\n\n".join(x for x in [context, extra_system] if x)
            messages = self._build_messages(text, extra_system=combined)
            response = await self._client.chat_stream(messages=messages, on_token=on_token)
            reply = (response.message.content or "").strip() if response and response.message else ""
            collected.append(reply or "")
            if reply:
                self._save_pair(text, reply)
                self.last_reply = {"time": time.time(), "text": reply}
                logger.info(f"[Pet] 流式对话完成: {text[:30]} → {reply[:40]}")
        except Exception as e:
            logger.error(f"[Pet] 流式任务失败: {e}")

    async def _after_reply(self, reply: str) -> None:
        """回复后：TTS 语音播放 + 广播 pet_reply（桌宠窗口/前端气泡）"""
        # 缓存最近回复（桌宠窗口轮询）
        self.last_reply = {"time": time.time(), "text": reply}
        # TTS
        try:
            from modules.output_system.tts import TTSEngine
            tts = TTSEngine()
            if tts.enabled or True:
                path = tts.synthesize_sync(reply)
                if path:
                    await asyncio.to_thread(_play_audio, path)
        except Exception as e:
            logger.debug(f"[Pet] TTS 失败: {e}")
        # 广播 pet_reply（统一推送出口；桌宠历史已由 _save_pair 持久化，此处不重复落库）
        try:
            from modules.thinking.frontend_channel import push_content
            await push_content(
                self.pet_session_id,
                msg_type="pet",
                event="pet_reply",
                content=reply,
                role="assistant",
                data={"session_id": self.pet_session_id},
                persist=False,
            )
        except Exception as e:
            logger.debug(f"[Pet] 广播回复失败: {e}")

    def is_active(self) -> bool:
        return self._running


def _play_audio(path: str) -> None:
    """播放音频（macOS afplay / 通用）"""
    import subprocess
    import os
    if not path or not os.path.exists(path):
        return
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        else:
            subprocess.run(["afplay", path], capture_output=True, timeout=60)
    except Exception:
        pass
