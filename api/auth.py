"""
API 认证

所有外部 API 端点统一使用 X-API-Key header 认证。
密钥由 SIMPLE_API_KEY 配置。
"""
from fastapi import Header, HTTPException
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("api_auth")


def require_api_key(x_api_key: str = Header(None)) -> str:
    """统一认证依赖 — 校验 X-API-Key 是否匹配 SIMPLE_API_KEY"""
    _api_key = settings.SIMPLE_API_KEY

    if not _api_key:
        logger.error("SIMPLE_API_KEY 未配置，API 认证无法工作")
        raise HTTPException(status_code=500, detail="服务器认证未配置")

    if x_api_key and x_api_key == _api_key:
        return x_api_key

    raise HTTPException(status_code=401, detail="未授权访问：缺少或无效的 X-API-Key")
