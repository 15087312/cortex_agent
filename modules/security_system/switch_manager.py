"""
分级开关管理器 - 仅用户可操作
"""
from typing import Dict
from .security_level import SecurityLevel
from utils.logger import setup_logger

logger = setup_logger("switch_manager")


class SecuritySwitchManager:
    def __init__(self):
        self._switch_state: Dict[SecurityLevel, bool] = {
            SecurityLevel.CORE: True,
            SecurityLevel.CONTENT: True,
            SecurityLevel.MODULE: True,
            SecurityLevel.EVOLVE: True,
            SecurityLevel.OUTPUT: True
        }
        logger.info("安全开关管理器初始化完成")

    def get_switch_state(self, level: SecurityLevel) -> bool:
        return self._switch_state.get(level, False)

    def set_switch(self, level: SecurityLevel, enable: bool, user_auth: bool = False) -> bool:
        if level == SecurityLevel.CORE:
            logger.warning("[安全拦截] L0核心安全层不可关闭，已拒绝操作")
            return False

        if not user_auth:
            logger.warning("[安全拦截] 仅用户可修改安全开关，已拒绝非授权操作")
            return False

        self._switch_state[level] = enable
        status = "开启" if enable else "关闭"
        logger.info(f"[安全开关] {level.value}级安全已{status}")
        return True

    def get_all_state(self) -> Dict[str, bool]:
        return {level.value: state for level, state in self._switch_state.items()}

    def is_enabled(self, level: SecurityLevel) -> bool:
        if level == SecurityLevel.CORE:
            return True
        return self._switch_state.get(level, True)
