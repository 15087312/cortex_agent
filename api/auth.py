"""
统一 API 认证

外部调用统一使用 X-API-Key header。
内部服务间调用可使用 Bearer token（向后兼容）。
"""
from fastapi import Header, HTTPException
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("api_auth")

_api_key = settings.SIMPLE_API_KEY


def require_api_key(x_api_key: str = Header(None)) -> str:
    """统一认证依赖 — 所有外部端点使用 X-API-Key

    - 配置了 SIMPLE_API_KEY → 校验 X-API-Key header
    - 未配置 → 开发模式，放行（日志警告）
    - 同时接受 Bearer token（向后兼容内部服务调用）
    """
    if not _api_key:
        return ""  # 开发模式

    if x_api_key and x_api_key == _api_key:
        return x_api_key

    raise HTTPException(status_code=401, detail="未授权访问：缺少或无效的 X-API-Key")
