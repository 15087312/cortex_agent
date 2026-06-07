"""
模块权限校验器 - L2级
"""
from typing import Tuple, Dict, Set


class ModuleValidator:
    def __init__(self):
        self.allowed_calls: Dict[str, Set[str]] = {
            "gateway": {"memory", "output_system", "security_system"},
            "attention": {"resource"},
            "thinking": {"output_system", "memory"},
            "output_system": {"security_system"},
            "management": {"memory", "attention", "resource"}
        }

    def validate(self, caller: str, target: str) -> Tuple[bool, str]:
        # SEC-6: Unknown callers must be rejected (fail-closed)
        if caller not in self.allowed_calls:
            return False, f"[L2权限拦截] 未知调用者「{caller}」"

        if target in self.allowed_calls.get(caller, set()):
            return True, target

        return False, f"[L2权限拦截] 模块「{caller}」无权调用「{target}」"

    def allow_call(self, caller: str, target: str) -> None:
        if caller not in self.allowed_calls:
            self.allowed_calls[caller] = set()
        self.allowed_calls[caller].add(target)
