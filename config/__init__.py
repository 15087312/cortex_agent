"""
配置层 - 统一入口

所有配置通过 settings 单例访问，支持 .env 文件和环境变量覆盖。
子模块配置（model/memory/attention/output/plugin）通过各自模块获取。

Usage:
    from config import settings
    from config.model_config import get_large_model_config
"""
from config.settings import settings, Settings

__all__ = ["settings", "Settings"]
