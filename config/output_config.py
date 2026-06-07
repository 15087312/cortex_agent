"""
输出配置 - 语音、键鼠的优先级、抢占规则
"""
from pydantic import BaseModel
from typing import List, Dict


class OutputPriorityConfig(BaseModel):
    """输出优先级配置"""
    
    # 输出类型优先级（数字越小优先级越高）
    priority_order: Dict[str, int] = {
        "emergency": 0,      # 紧急输出（最高优先级）
        "interrupt": 1,      # 中断输出
        "response": 2,       # 主动响应
        "feedback": 3,       # 反馈输出
        "background": 4      # 背景输出（最低优先级）
    }
    
    # 语音输出配置
    speech_enabled: bool = True
    speech_language: str = "zh-CN"
    speech_rate: float = 1.0
    speech_volume: float = 1.0
    
    # 文字输出配置
    text_enabled: bool = True
    text_max_length: int = 500
    
    # 键鼠输出配置
    km_enabled: bool = True
    km_sensitivity: float = 0.8
    km_max_speed: int = 100


class PreemptionRule(BaseModel):
    """抢占规则"""
    
    # 允许抢占的优先级阈值
    preemption_threshold: int = 1  # emergency 和 interrupt 可以抢占
    
    # 抢占延迟（毫秒）
    preemption_delay_ms: int = 100
    
    # 被抢占后的处理方式：cancel, pause, queue
    on_preempted: str = "pause"
    
    # 抢占冷却时间（秒）
    preemption_cooldown: int = 3


class TTSConfig(BaseModel):
    """文本转语音配置"""
    
    # TTS 引擎：gtts, azure, google
    tts_engine: str = "gtts"
    
    # 音频格式：mp3, wav
    audio_format: str = "mp3"
    
    # 音频质量：low, medium, high
    audio_quality: str = "medium"
    
    # 缓存配置
    cache_enabled: bool = True
    cache_ttl: int = 3600


def get_output_priority_config() -> OutputPriorityConfig:
    """获取输出优先级配置"""
    return OutputPriorityConfig(
        priority_order={
            "emergency": 0,
            "interrupt": 1,
            "response": 2,
            "feedback": 3,
            "background": 4
        },
        speech_enabled=True,
        speech_language="zh-CN",
        speech_rate=1.0,
        speech_volume=1.0,
        text_enabled=True,
        text_max_length=500,
        km_enabled=True,
        km_sensitivity=0.8,
        km_max_speed=100
    )


def get_preemption_rule() -> PreemptionRule:
    """获取抢占规则"""
    return PreemptionRule(
        preemption_threshold=1,
        preemption_delay_ms=100,
        on_preempted="pause",
        preemption_cooldown=3
    )
