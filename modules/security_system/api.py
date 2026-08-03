"""
安全系统统一API - 全局唯一入口 + HTTP路由
"""
from typing import Tuple, Dict
from fastapi import Depends,  APIRouter, Body
from api.auth import require_api_key
from api.errors import AppError, ErrorCode
from .security_level import SecurityLevel
from .switch_manager import SecuritySwitchManager
from .audit_logger import SecurityAuditLogger
from .validators import (
    CoreValidator, ContentValidator
)
from utils.logger import setup_logger

logger = setup_logger("security_api")

# 只读端点（GET）由中间件白名单控制（/security/status、/security/audit 免鉴权）；
# 写操作端点单独挂 require_api_key。
router = APIRouter(prefix="/security", tags=["安全系统"])


class SecurityAPI:
    def __init__(self):
        self.switch_manager = SecuritySwitchManager()
        self.audit_logger = SecurityAuditLogger()

        self.core_validator = CoreValidator()
        self.content_validator = ContentValidator()

        logger.info("安全系统API初始化完成")

    def validate_input(self, user_input: str) -> Tuple[bool, str]:
        passed, result = self.core_validator.validate_all(user_input)
        self.audit_logger.log("输入校验", "L0", user_input, passed)
        if not passed:
            return False, result

        if self.switch_manager.is_enabled(SecurityLevel.CONTENT):
            passed, result = self.content_validator.validate(user_input)
            self.audit_logger.log("输入校验", "L1", user_input, passed)
            if not passed:
                return False, result

        return True, user_input

    def set_security_switch(self, level: SecurityLevel, enable: bool, user_auth: bool = False) -> bool:
        result = self.switch_manager.set_switch(level, enable, user_auth)
        if result:
            self.audit_logger.log("开关修改", level.value, f"设置为{enable}", True)
        return result

    def get_security_state(self) -> Dict[str, bool]:
        return self.switch_manager.get_all_state()

    def get_audit_logs(self, limit: int = 50) -> list:
        return self.audit_logger.get_recent_logs(limit)


# ========== HTTP 路由 ==========

@router.get("/status")
async def get_security_status():
    """获取安全系统状态"""
    api = get_security_api()
    return {"success": True, "data": {
        "state": api.get_security_state(),
        "audit_enabled": True
    }}


@router.get("/audit")
async def get_audit_logs(limit: int = 50):
    """获取审计日志"""
    api = get_security_api()
    logs = api.get_audit_logs(limit)
    return {"success": True, "data": {"logs": logs, "count": len(logs)}}


@router.post("/switch", dependencies=[Depends(require_api_key)])
async def set_security_switch(
    level: str,
    enable: bool
):
    """设置安全开关"""
    api = get_security_api()
    try:
        sec_level = SecurityLevel(level)
        result = api.set_security_switch(sec_level, enable)
        return {"success": True, "data": {"result": result}}
    except ValueError:
        raise AppError(ErrorCode.BAD_REQUEST, f"无效的安全级别: {level}")


@router.post("/validate/input", dependencies=[Depends(require_api_key)])
async def validate_input(content: str = Body(..., description="要校验的输入内容")):
    """校验输入 - SEC-13: Use request body instead of query parameters"""
    api = get_security_api()
    passed, result = api.validate_input(content)
    return {"success": True, "data": {"passed": passed, "result": result}}


import threading as _threading

_security_api = None
_security_api_lock = _threading.Lock()


def get_security_api() -> SecurityAPI:
    """获取安全系统 API 单例"""
    global _security_api
    if _security_api is None:
        with _security_api_lock:
            if _security_api is None:
                _security_api = SecurityAPI()
    return _security_api
