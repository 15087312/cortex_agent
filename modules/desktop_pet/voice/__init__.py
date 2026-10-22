"""桌宠实时语音模块 — 全双工语音管线

Silero VAD → 流式 ASR → 轮次管理 → PetEngine → TTS

当 PERCEPTION_VOICE_REALTIME=true 时替代半双工 VoiceDetector。
"""
from modules.desktop_pet.voice.pipeline import RealtimeVoicePipeline

__all__ = ["RealtimeVoicePipeline"]
