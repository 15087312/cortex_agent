from dataclasses import dataclass

@dataclass
class MemoryConfig:
    """记忆配置"""

    enable_short_term: bool = True
    short_term_capacity: int = 50
    short_term_retention_minutes: int = 30

    enable_long_term: bool = True
    enable_semantic_search: bool = True

    enable_classified_memory: bool = True

    enable_personality: bool = True
    enable_blackbox: bool = True
    enable_notebook: bool = True

    auto_compress_threshold: int = 1000
    compression_ratio: float = 0.7

    cleanup_interval_hours: int = 24
    retain_days: int = 30

    classification_categories: list = None

    def __post_init__(self):
        if self.classification_categories is None:
            self.classification_categories = [
                "skills",
                "communication",
                "knowledge",
                "experience",
                "preferences",
                "general"
            ]
