"""语音链路 Mock 冒烟测试

覆盖（不依赖真实麦克风/Whisper）:
- SpeechRecognizer mock 模式（_recognize_mock）
- Event Bus 的 SPEECH_DETECTED 事件分发
- VoiceLLMHandler 收到语音事件 → 路由到思考系统
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from infra.data_process.core.speech_recognizer import SpeechRecognizer
from modules.perception.events.bus import PerceptionEventBus
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from modules.perception.voice_llm_handler import VoiceLLMHandler


class TestSpeechRecognizerMock:
    @pytest.mark.asyncio
    async def test_mock_recognize(self):
        """mock 模式：跳过 Whisper 加载，直接返回固定文本"""
        rec = SpeechRecognizer()
        rec._initialized = True
        rec.model = None
        result = await rec.recognize(b"fake audio bytes")
        assert "模拟识别结果" in result["text"]
        assert result["confidence"] == 0.95


class TestEventBusSpeech:
    def test_speech_detected_event_delivery(self):
        """SPEECH_DETECTED 事件能被订阅者收到"""
        bus = PerceptionEventBus()
        received = []
        bus.subscribe(PerceptionEventType.SPEECH_DETECTED, lambda e: received.append(e))
        bus.publish(PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            source="voice",
            payload={"text": "今天天气怎么样"},
        ))
        assert len(received) == 1
        assert received[0].payload["text"] == "今天天气怎么样"


class TestVoiceLLMHandler:
    @pytest.mark.asyncio
    async def test_speech_event_routes_to_thinking(self, monkeypatch):
        """语音文本 → 思考系统.think(session, text) 链路"""
        # 伪造 api_stream 模块，避免加载真实思考系统
        fake_api_stream = types.ModuleType("modules.thinking.api_stream")
        fake_conn = MagicMock()
        fake_conn._loop = None  # 触发兜底路径：在当前循环直接执行
        fake_conn._lock = __import__("asyncio").Lock()
        fake_conn.active_connections = {}
        fake_api_stream.connection_manager = fake_conn
        monkeypatch.setitem(sys.modules, "modules.thinking.api_stream", fake_api_stream)

        from config.settings import settings
        monkeypatch.setattr(settings, "PERCEPTION_VOICE_LLM_TRIGGER_ENABLED", True)

        thinking = AsyncMock()
        thinking.think.return_value = "你好，我是科特"

        handler = VoiceLLMHandler()
        handler._running = True
        handler._voice_session_id = "voice_test_001"
        handler._thinking_system = thinking

        event = PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            source="voice",
            payload={"text": "今天天气怎么样"},
        )
        await handler._on_speech_detected(event)

        thinking.think.assert_awaited_once()
        args = thinking.think.await_args.args
        assert args[0] == "voice_test_001"
        assert args[1] == "今天天气怎么样"
