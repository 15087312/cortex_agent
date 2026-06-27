"""
配置层 - 统一入口

所有配置通过 settings 单例访问，支持 .env + ~/.cortex/settings.json 双层覆盖。
模型角色从 config/prompts/roles.yaml 加载，prompt 规则从 config/prompts/base.yaml 加载。

Usage:
    from config import settings
    from config.prompts.composer import PromptComposer, PromptRequest
"""
from config.settings import settings, Settings

__all__ = ["settings", "Settings"]
