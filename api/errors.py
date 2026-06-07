"""
统一错误处理基础设施

提供:
1. ErrorCode — 标准错误码枚举
2. ErrorDetail — 结构化错误信息 Pydantic 模型
3. ErrorResponse — 统一错误响应信封 {"success": false, "error": {...}}
4. AppError — 应用级异常，替代裸 HTTPException
5. error_response() — 快速构造错误响应的工具函数
"""
from enum import Enum
from typing import Optional, Any, Dict
from pydantic import BaseModel


class ErrorCode(str, Enum):
    """标准错误码"""
    # 4xx
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    BAD_REQUEST = "BAD_REQUEST"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    # 5xx
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


# 错误码 → HTTP 状态码映射
ERROR_CODE_STATUS: Dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.NOT_IMPLEMENTED: 501,
}


class ErrorDetail(BaseModel):
    """结构化错误详情"""
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    """统一错误响应信封"""
    success: bool = False
    error: ErrorDetail


def error_response(code: ErrorCode, message: str) -> ErrorResponse:
    """快速构造 ErrorResponse"""
    return ErrorResponse(
        success=False,
        error=ErrorDetail(code=code, message=message)
    )


class AppError(Exception):
    """应用级异常 — 替代裸 HTTPException

    用法:
        raise AppError(ErrorCode.NOT_FOUND, "模块 xxx 不存在")
        raise AppError(ErrorCode.VALIDATION_ERROR, "参数校验失败")
    """

    def __init__(self, code: ErrorCode, message: str, status_code: Optional[int] = None):
        self.code = code
        self.message = message
        self.status_code = status_code or ERROR_CODE_STATUS.get(code, 500)
