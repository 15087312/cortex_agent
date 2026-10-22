"""WebSocket 端点 — 前端麦克风音频流接入

前端通过 WebSocket 发送 PCM 16kHz 16bit mono 音频，
后端通过同一连接推送状态变化、partial/final 转写、TTS URL。
"""
import asyncio
import json
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from utils.logger import setup_logger

logger = setup_logger("pet_voice_ws")


class VoiceWebSocketHandler:
    """实时语音 WebSocket 处理器

    每个连接持有一个 RealtimeVoicePipeline 实例（或共享全局实例）。
    """

    def __init__(self, websocket: WebSocket):
        self._ws = websocket
        self._pipeline = None
        self._running = False

    async def handle(self):
        """处理 WebSocket 连接"""
        await self._ws.accept()
        self._running = True

        try:
            from modules.desktop_pet.voice.pipeline import RealtimeVoicePipeline
            from config.settings import settings

            pipeline = RealtimeVoicePipeline(
                sample_rate=16000,
                language=getattr(settings, "PERCEPTION_VOICE_LANGUAGE", "zh"),
                vad_threshold=getattr(settings, "PERCEPTION_VOICE_VAD_THRESHOLD", 0.5),
                silence_duration=getattr(settings, "PERCEPTION_VOICE_SILENCE_DURATION", 1.5),
            )
            self._pipeline = pipeline

            loop = asyncio.get_event_loop()

            def _sync_send(data: dict):
                asyncio.run_coroutine_threadsafe(
                    self._ws.send_text(json.dumps(data, ensure_ascii=False)),
                    loop,
                )

            pipeline.start(ws_send=_sync_send)

            while self._running:
                try:
                    message = await self._ws.receive()
                    if message["type"] == "websocket.disconnect":
                        break

                    if "bytes" in message and message["bytes"]:
                        pipeline.feed_audio(message["bytes"])
                    elif "text" in message and message["text"]:
                        await self._handle_text(message["text"])

                except WebSocketDisconnect:
                    break
                except Exception as e:
                    logger.debug(f"WS 接收异常: {e}")
                    break

        except Exception as e:
            logger.error(f"实时语音 WS 启动失败: {e}")
        finally:
            self._running = False
            if self._pipeline:
                self._pipeline.stop()

    async def _handle_text(self, text: str):
        """处理前端发来的文本命令"""
        try:
            data = json.loads(text)
            cmd = data.get("command")

            if cmd == "reset":
                if self._pipeline:
                    self._pipeline._asr.reset()
                    self._pipeline._turn_manager.reset()
            elif cmd == "stop":
                self._running = False

        except json.JSONDecodeError as _swallowed_err:
            logger.debug(f"[_handle_text] ignored json.JSONDecodeError: {_swallowed_err}")
        except Exception as e:
            logger.debug(f"WS 命令处理失败: {e}")
