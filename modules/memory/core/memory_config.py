"""
记忆模块配置 — 每个模型独立的记忆能力配置

每个模型有独立的记忆库，通过 MemoryConfig 控制开启哪些记忆模块。
默认配置:
  - large:      全部开启（短期+长期+人格+情绪+黑匣子+记事本+语义搜索）
  - supervisor: 短期+长期+记事本
  - expert:     短期+长期
"""
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class MemoryConfig:
    """每个模型的记忆模块配置"""

    model_id: str = ""

    # —— 模块开关 ——
    enable_short_term: bool = True       # 短期记忆 (SQLite + diskcache)
    enable_long_term: bool = True        # 长期记忆 (JSONL)
    enable_personality: bool = False     # 人格记忆 (personality.json)
    enable_emotion: bool = False         # 情绪跟踪 (current_emotion cache)
    enable_blackbox: bool = False        # 黑匣子日志 (JSONL)
    enable_notebook: bool = False        # AI 记事本 (Markdown)
    enable_classified_memory: bool = True # 分类记忆 (按用户/模型画像类别存储)

    # —— 检索 ——
    enable_semantic_search: bool = True  # FAISS 语义搜索（失败自动降级关键词）

    # —— 存储路径 ——
    memory_dir: str = ""                 # 专属记忆目录（空则自动派生）

    @classmethod
    def for_large(cls, model_id: str = "large_primary") -> "MemoryConfig":
        """大模型：全部开启"""
        return cls(
            model_id=model_id,
            enable_short_term=True,
            enable_long_term=True,
            enable_personality=True,
            enable_emotion=True,
            enable_blackbox=True,
            enable_notebook=True,
            enable_classified_memory=True,
            enable_semantic_search=True,
        )

    @classmethod
    def for_supervisor(cls, model_id: str = "") -> "MemoryConfig":
        """主管模型：短期+长期+记事本"""
        return cls(
            model_id=model_id,
            enable_short_term=True,
            enable_long_term=True,
            enable_personality=False,
            enable_emotion=False,
            enable_blackbox=False,
            enable_notebook=True,
            enable_classified_memory=True,
            enable_semantic_search=True,
        )

    @classmethod
    def for_expert(cls, model_id: str = "") -> "MemoryConfig":
        """专家模型：仅短期+长期"""
        return cls(
            model_id=model_id,
            enable_short_term=True,
            enable_long_term=True,
            enable_personality=False,
            enable_emotion=False,
            enable_blackbox=False,
            enable_notebook=False,
            enable_classified_memory=True,
            enable_semantic_search=True,
        )


def get_default_config(tier: str, model_id: str = "") -> MemoryConfig:
    """根据模型层级获取默认记忆配置"""
    configs: Dict[str, MemoryConfig] = {
        "large": MemoryConfig.for_large(model_id),
        "supervisor": MemoryConfig.for_supervisor(model_id),
        "expert": MemoryConfig.for_expert(model_id),
    }
    if tier in configs:
        config = configs[tier]
        config.model_id = model_id
        return config
    # 未知层级：最小化（仅短期+长期）
    return MemoryConfig.for_expert(model_id)


def memory_config_from_dict(tier: str, model_id: str = "", overrides: dict = None) -> MemoryConfig:
    """从 tier 默认配置出发，用 overrides 字典覆盖指定字段。

    Args:
        tier: 模型层级 (large / supervisor / expert)
        model_id: 模型 ID
        overrides: 要覆盖的字段，如 {"enable_personality": True, "enable_notebook": True}

    Returns:
        合并后的 MemoryConfig
    """
    base = get_default_config(tier, model_id)
    if not overrides:
        return base
    for key, value in overrides.items():
        if hasattr(base, key):
            setattr(base, key, value)
    return base
