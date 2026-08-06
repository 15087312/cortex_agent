"""桌面宠物引擎 — 主会话对话 + 语音触发（借鉴 airi / codex 桌宠）

- 桌宠绑定固定「主会话」pet_main（永不删除，对话记忆延续，类似 Siri）
- 订阅 SPEECH_DETECTED 语音事件 → 识别文本 → 主会话对话（LLM）→ 保存历史
  → TTS 语音回复 + 广播 pet_reply（桌宠窗口/前端展示气泡）
"""
import asyncio
import threading
import time

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("pet_engine")


class PetEngine:
    """桌宠对话引擎（单例，感知系统启动时创建）"""

    _instance: "PetEngine" = None
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

    async def chat(self, text: str) -> str:
        """主会话对话：历史 + 桌宠人设 + LLM → 回复，并保存主会话历史"""
        try:
            from infra.model.large_model_client import LargeModelClient
            from infra.model.base_model import ChatMessage
            if self._client is None:
                self._client = LargeModelClient()

            # 主会话历史（桌宠记忆延续）
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

            # 桌宠人设
            system = (
                "你是桌面上的 AI 桌宠助手，像 Siri 一样用语音和用户交流。"
                "性格友善、简洁、贴心。回答要简短自然（通常一两句话），适合语音朗读。"
                "用中文回答。"
            )
            messages = [ChatMessage(role="system", content=system)] + history
            messages.append(ChatMessage(role="user", content=text))

            response = await self._client.chat(messages=messages)
            reply = (response.message.content or "").strip() if response and response.message else ""
            if not reply:
                return ""

            # 保存主会话历史
            try:
                from modules.database.session_repo import get_session_repo
                repo = get_session_repo()
                repo.save_message(self.pet_session_id, "user", text)
                repo.save_message(self.pet_session_id, "assistant", reply)
            except Exception:
                pass
            logger.info(f"[Pet] 对话: {text[:40]} → {reply[:40]}")
            return reply
        except Exception as e:
            logger.error(f"[Pet] 对话失败: {e}")
            return ""

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
        # 广播 pet_reply
        try:
            from modules.thinking.api_stream import connection_manager, _build_event
            event = _build_event(
                session_id=self.pet_session_id,
                msg_type="pet",
                event="pet_reply",
                content=reply,
                role="assistant",
                data={"session_id": self.pet_session_id},
            )
            for sid in list(connection_manager.active_connections.keys()):
                connection_manager.send_json_from_thread(sid, event)
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
            os.startfile(path)  # noqa: S606
        else:
            subprocess.run(["afplay", path], capture_output=True, timeout=60)
    except Exception:
        pass
