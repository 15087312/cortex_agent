"""
API 认证

所有外部 API 端点统一使用 X-API-Key header 认证。
密钥由 SIMPLE_API_KEY 配置。
支持 query 参数 api_key 作为备选（供 dashboard 等前端使用）。
"""
from fastapi import Header, Query, HTTPException
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("api_auth")


def require_api_key(
    x_api_key: str = Header(None),
    api_key: str = Query(None),
) -> str:
    """统一认证依赖 — 校验 X-API-Key 或 ?api_key= 参数"""
    _api_key = settings.SIMPLE_API_KEY

    if not _api_key:
        logger.error("SIMPLE_API_KEY 未配置，API 认证无法工作")
        raise HTTPException(status_code=500, detail="服务器认证未配置")

    key = x_api_key or api_key
    if key and key == _api_key:
        return key

    raise HTTPException(status_code=401, detail="未授权访问：缺少或无效的 X-API-Key")
