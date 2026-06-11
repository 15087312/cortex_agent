"""
安全系统统一API - 全局唯一入口 + HTTP路由
"""
from typing import Tuple, Dict, Optional
from fastapi import Depends,  APIRouter, Body
from api.auth import require_api_key
from api.errors import AppError, ErrorCode
from .security_level import SecurityLevel
from .switch_manager import SecuritySwitchManager
from .audit_logger import SecurityAuditLogger
from .validators import (
    CoreValidator, ContentValidator, ModuleValidator,
    EvolveValidator, OutputValidator
)
from utils.logger import setup_logger

logger = setup_logger("security_api")

router = APIRouter(prefix="/security", tags=["安全系统"],
    dependencies=[Depends(require_api_key)],
)


class SecurityAPI:
    def __init__(self):
        self.switch_manager = SecuritySwitchManager()
        self.audit_logger = SecurityAuditLogger()

        self.core_validator = CoreValidator()
        self.content_validator = ContentValidator()
        self.module_validator = ModuleValidator()
        self.evolve_validator = EvolveValidator()
        self.output_validator = OutputValidator()

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

    def validate_output(self, output_content: str) -> Tuple[bool, str]:
        passed, result = self.core_validator.validate_all(output_content)
        self.audit_logger.log("输出校验", "L0", output_content, passed)
        if not passed:
            return False, result

        if self.switch_manager.is_enabled(SecurityLevel.CONTENT):
            passed, result = self.content_validator.validate(output_content)
            self.audit_logger.log("输出校验", "L1", output_content, passed)
            if not passed:
                return False, result

        if self.switch_manager.is_enabled(SecurityLevel.OUTPUT):
            passed, result = self.output_validator.validate(output_content)
            self.audit_logger.log("输出校验", "L4", output_content, passed)
            if not passed:
                return False, result

        return True, output_content

    def validate_module_call(self, caller: str, target: str) -> Tuple[bool, str]:
        passed, result = self.core_validator.validate_module_protect(target)
        self.audit_logger.log("模块调用", "L0", f"{caller}→{target}", passed)
        if not passed:
            return False, result

        if self.switch_manager.is_enabled(SecurityLevel.MODULE):
            passed, result = self.module_validator.validate(caller, target)
            self.audit_logger.log("模块调用", "L2", f"{caller}→{target}", passed)
            if not passed:
                return False, result

        return True, target

    def validate_evolve(self, code: str, target_module: str) -> Tuple[bool, str]:
        passed, result = self.core_validator.validate_module_protect(target_module)
        self.audit_logger.log("自进化校验", "L0", f"修改{target_module}", passed)
        if not passed:
            return False, result

        passed, result = self.core_validator.validate_code_safety(code)
        self.audit_logger.log("自进化校验", "L0", f"代码安全检查", passed)
        if not passed:
            return False, result

        if self.switch_manager.is_enabled(SecurityLevel.EVOLVE):
            passed, result = self.evolve_validator.validate(code, target_module)
            self.audit_logger.log("自进化校验", "L3", f"修改{target_module}", passed)
            if not passed:
                return False, result

        return True, code

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


@router.post("/switch")
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


@router.post("/validate/input")
async def validate_input(content: str = Body(..., description="要校验的输入内容")):
    """校验输入 - SEC-13: Use request body instead of query parameters"""
    api = get_security_api()
    passed, result = api.validate_input(content)
    return {"success": True, "data": {"passed": passed, "result": result}}


@router.post("/validate/output")
async def validate_output(content: str = Body(..., description="要校验的输出内容")):
    """校验输出 - SEC-13: Use request body instead of query parameters"""
    api = get_security_api()
    passed, result = api.validate_output(content)
    return {"success": True, "data": {"passed": passed, "result": result}}


@router.post("/validate/module")
async def validate_module_call(
    caller: str = Body(..., description="调用者模块"),
    target: str = Body(..., description="目标模块")
):
    """校验模块调用 - SEC-13: Use request body instead of query parameters"""
    api = get_security_api()
    passed, result = api.validate_module_call(caller, target)
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
