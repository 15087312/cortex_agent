"""
注意力配置 - 权重计算公式、紧急阈值、中断规则
"""
from pydantic import BaseModel
from typing import Dict, List, Optional


class AttentionWeightConfig(BaseModel):
    """注意力权重配置"""

    # 基础权重
    base_weight: float = 0.5

    # 声音权重因子
    sound_weight_factor: float = 1.2

    # 视觉权重因子
    visual_weight_factor: float = 1.1

    # 任务优先级权重
    task_priority_weight: float = 1.5

    # 情绪权重因子
    emotion_weight_factor: float = 1.3

    # 归一化方法：softmax, min_max, z_score
    normalization_method: str = "softmax"

    # 问题重要性识别开关
    importance_enabled: bool = True
    importance_model_enabled: bool = False

    # 强制静态注意力等级（None 表示动态）
    force_static_level: Optional[float] = None

    # 动态阈值配置：threshold = base - slope * attention_level
    threshold_base: float = 0.6
    threshold_slope: float = 0.5
    threshold_min: float = 0.1
    threshold_max: float = 0.6

    # 动态召回上限配置
    max_recall_low: int = 5
    max_recall_medium: int = 10
    max_recall_high: int = 20


class InterruptRule(BaseModel):
    """中断规则"""
    
    # 紧急阈值
    urgency_threshold: float = 0.9
    
    # 允许中断的任务类型
    interruptible_task_types: List[str] = ["background", "low_priority"]
    
    # 不允许中断的任务类型
    non_interruptible_task_types: List[str] = ["critical", "emergency"]
    
    # 最小中断间隔（秒）
    min_interrupt_interval: int = 2
    
    # 中断冷却时间（秒）
    interrupt_cooldown: int = 5


class TaskSchedulerConfig(BaseModel):
    """任务调度配置"""
    
    # 最大并发任务数
    max_concurrent_tasks: int = 5
    
    # 任务队列大小
    task_queue_size: int = 100
    
    # 调度算法：priority, fifo, shortest_job_first
    scheduling_algorithm: str = "priority"
    
    # 任务超时时间（秒）
    task_timeout: int = 300
    
    # 任务重试次数
    task_max_retries: int = 3


def get_attention_config() -> AttentionWeightConfig:
    """获取注意力权重配置"""
    from config.settings import settings
    return AttentionWeightConfig(
        base_weight=0.5,
        sound_weight_factor=1.2,
        visual_weight_factor=1.1,
        task_priority_weight=1.5,
        emotion_weight_factor=1.3,
        normalization_method="softmax",
        importance_enabled=settings.ATTENTION_IMPORTANCE_ENABLED,
        importance_model_enabled=settings.ATTENTION_IMPORTANCE_MODEL_ENABLED,
        force_static_level=settings.ATTENTION_FORCE_STATIC_LEVEL,
        threshold_base=settings.ATTENTION_THRESHOLD_BASE,
        threshold_slope=settings.ATTENTION_THRESHOLD_SLOPE,
        threshold_min=settings.ATTENTION_THRESHOLD_MIN,
        threshold_max=settings.ATTENTION_THRESHOLD_MAX,
        max_recall_low=settings.ATTENTION_MAX_RECALL_LOW,
        max_recall_medium=settings.ATTENTION_MAX_RECALL_MEDIUM,
        max_recall_high=settings.ATTENTION_MAX_RECALL_HIGH,
    )


def get_interrupt_rule() -> InterruptRule:
    """获取中断规则"""
    from config.settings import settings
    return InterruptRule(
        urgency_threshold=settings.INTERRUPT_URGENCY_THRESHOLD,
        interruptible_task_types=["background", "low_priority"],
        non_interruptible_task_types=["critical", "emergency"],
        min_interrupt_interval=2,
        interrupt_cooldown=5
    )
