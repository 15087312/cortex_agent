"""
工具安全门控 — 所有工具调用经过统一安全审查

风险分级：
- LOW:    查询类工具，直接放行
- MEDIUM: 文件修改/白名单命令，快速路径检查
- HIGH:   exec_command/kill_process/git_push 等，需要审批

审查模式（SECURITY_REVIEW_MODE）：
- "llm":   安全专家 LLM 审批
- "user":  用户在 CLI 手动审批
- "auto":  LLM 可用时用 LLM，否则拒绝
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple
from utils.logger import setup_logger
from modules.security_system.audit_logger import SecurityAuditLogger

logger = setup_logger("tool_security_gate")

# 模块级事件回调 — stream 系统启动时注入
_security_event_callback: Optional[Callable[[Dict[str, Any]], None]] = None


def set_security_event_callback(callback: Callable[[Dict[str, Any]], None]):
    """设置安全事件回调（由 stream 系统调用）"""
    global _security_event_callback
    _security_event_callback = callback


def _emit_security_event(
    event_type: str,
    tool_name: str,
    caller_model_id: str,
    success: bool,
    detail: str = "",
    duration_ms: int = 0,
    request_id: str = "",
):
    """推送安全事件到 stream"""
    if not _security_event_callback:
        return
    try:
        _security_event_callback({
            "event_type": "security",
            "source": "tool_security_gate",
            "target": tool_name,
            "action": event_type,
            "success": success,
            "payload": {
                "caller": caller_model_id,
                "detail": detail,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        })
    except Exception as e:
        logger.debug(f"安全事件回调失败 (非致命): {e}")


# HIGH 风险工具 — 需要审批
HIGH_RISK_TOOLS = {
    "exec_command", "kill_process", "git_push",
    "external_api_call", "write_runtime_config",
    "delete_file",
}

# MEDIUM 风险工具 — 快速路径检查
MEDIUM_RISK_TOOLS = {
    "write_file", "file_edit", "append_file",
    "run_command", "run_python",
    "git_add", "git_commit",
    "install_dependency", "debug_code",
    "run_pytest",
    "create_plugin", "uninstall_plugin",
}

# 代码安全检查 — 禁止的危险模式
_FORBIDDEN_CODE_PATTERNS = (
    "__import__('os')", "__import__('subprocess')",
    "os.system(", "os.popen(", "subprocess.",
    "shutil.rmtree(", "shutil.rmtree (",
    "shutil.move(", "shutil.copytree(",
    "eval(", "exec(",
    "open('/etc/", "open('/proc/",
    "socket.socket(", "urllib.request.urlopen(",
    "os.remove(", "os.unlink(",
    "os.rmdir(", "os.rename(",
    "pathlib.Path(",
    ".unlink(", ".rmdir(",
    "ctypes.", "importlib.import_module(",
)

# 用户审查超时（秒）
USER_REVIEW_TIMEOUT = 120


def _check_code_safety(code: str) -> Tuple[bool, str]:
    """快速代码安全检查（不依赖 LLM）"""
    code_lower = code.lower()
    for pattern in _FORBIDDEN_CODE_PATTERNS:
        if pattern.lower() in code_lower:
            return False, f"代码包含禁止的危险模式: {pattern}"
    return True, ""


class ToolSecurityGate:
    """工具安全门控 — 统一审查所有工具调用"""

    # 待处理的用户审查请求 {request_id: asyncio.Future}
    _pending_reviews: Dict[str, asyncio.Future] = {}

    def __init__(self, lite_model=None):
        self._lite_model = lite_model
        self._model_available = lite_model is not None
        self._audit = SecurityAuditLogger()
        logger.info(
            f"ToolSecurityGate 初始化 (LLM={'可用' if self._model_available else '不可用'})"
        )

    @property
    def _review_mode(self) -> str:
        """获取当前审查模式"""
        try:
            from config.settings import settings
            return settings.SECURITY_REVIEW_MODE
        except Exception:
            return "auto"

    async def check(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        caller_tier: str,
        caller_model_id: str,
        dialog_context: str = "",
    ) -> Tuple[bool, str]:
        """
        审查工具调用请求

        Returns:
            (allowed, reason): 是否允许执行 + 原因
        """
        if tool_name in HIGH_RISK_TOOLS:
            _emit_security_event("审查中", tool_name, caller_model_id, True, "HIGH 风险，评估中...")
            start = time.time()
            allowed, reason = await self._check_high_risk(
                tool_name, tool_params, caller_tier, caller_model_id, dialog_context
            )
            duration_ms = int((time.time() - start) * 1000)
            _emit_security_event(
                "审批通过" if allowed else "审批拒绝",
                tool_name, caller_model_id, allowed, reason, duration_ms,
            )
            try:
                self._audit.log(
                    event_type="tool_approved" if allowed else "tool_blocked",
                    level="HIGH",
                    content=tool_name,
                    result=allowed,
                    metadata={"caller_model_id": caller_model_id, "caller_tier": caller_tier, "reason": reason},
                )
            except Exception as e:
                logger.warning(f"审计日志记录失败 (非致命): {e}")
            return allowed, reason
        elif tool_name in MEDIUM_RISK_TOOLS:
            allowed, reason = self._check_medium_risk(tool_name, tool_params)
            if not allowed:
                _emit_security_event("拦截", tool_name, caller_model_id, False, reason)
            try:
                self._audit.log(
                    event_type="tool_approved" if allowed else "tool_blocked",
                    level="MEDIUM",
                    content=tool_name,
                    result=allowed,
                    metadata={"caller_model_id": caller_model_id, "caller_tier": caller_tier, "reason": reason},
                )
            except Exception as e:
                logger.warning(f"审计日志记录失败 (非致命): {e}")
            return allowed, reason
        else:
            try:
                self._audit.log(
                    event_type="tool_approved",
                    level="LOW",
                    content=tool_name,
                    result=True,
                    metadata={"caller_model_id": caller_model_id, "caller_tier": caller_tier, "reason": "LOW 风险工具，直接放行"},
                )
            except Exception as e:
                logger.warning(f"审计日志记录失败 (非致命): {e}")
            return True, "LOW 风险工具，直接放行"

    async def _check_high_risk(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        caller_tier: str,
        caller_model_id: str,
        dialog_context: str,
    ) -> Tuple[bool, str]:
        """HIGH 风险工具 — 根据审查模式选择审批方式"""
        mode = self._review_mode

        if mode == "user":
            return await self._check_user_review(
                tool_name, tool_params, caller_tier, caller_model_id
            )
        elif mode == "llm":
            return await self._check_llm_review(
                tool_name, tool_params, caller_tier, caller_model_id, dialog_context
            )
        else:  # auto
            if self._model_available:
                return await self._check_llm_review(
                    tool_name, tool_params, caller_tier, caller_model_id, dialog_context
                )
            else:
                logger.warning("[安全门控] auto 模式：LLM 不可用，拒绝 HIGH 风险操作")
                return False, "安全专家不可用，auto 模式降级拒绝"

    async def _check_user_review(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        caller_tier: str,
        caller_model_id: str,
    ) -> Tuple[bool, str]:
        """用户审查模式 — 推送到 CLI，等待用户审批"""
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_reviews[request_id] = future

        params_summary = ", ".join(f"{k}={repr(v)[:100]}" for k, v in tool_params.items())
        if len(params_summary) > 300:
            params_summary = params_summary[:300] + "..."

        _emit_security_event(
            "等待用户审批",
            tool_name, caller_model_id, True,
            f"调用者: {caller_model_id} ({caller_tier})\n参数: {params_summary}",
            request_id=request_id,
        )

        logger.info(f"[安全门控] {tool_name} 等待用户审批 (id={request_id})")

        try:
            result = await asyncio.wait_for(future, timeout=USER_REVIEW_TIMEOUT)
            approved = result.get("approved", False)
            reason = result.get("reason", "用户决定")
            if approved:
                logger.info(f"[安全门控] {tool_name} 用户批准: {reason}")
                return True, f"用户批准: {reason}"
            else:
                logger.warning(f"[安全门控] {tool_name} 用户拒绝: {reason}")
                return False, f"用户拒绝: {reason}"
        except asyncio.TimeoutError:
            logger.warning(f"[安全门控] {tool_name} 用户审查超时，自动拒绝")
            return False, "用户审查超时，自动拒绝"
        finally:
            self._pending_reviews.pop(request_id, None)

    async def _check_llm_review(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        caller_tier: str,
        caller_model_id: str,
        dialog_context: str,
    ) -> Tuple[bool, str]:
        """LLM 审批模式 — 安全专家 LLM 评估"""
        if not self._model_available:
            logger.warning("[安全门控] 安全专家不可用，降级拒绝")
            return False, "安全专家不可用，降级拒绝"

        try:
            prompt = self._build_review_prompt(
                tool_name, tool_params, caller_tier, caller_model_id, dialog_context
            )
            result = await self._lite_model.generate(
                prompt, max_tokens=256, temperature=0.1
            )
            return self._parse_review_result(result, tool_name)
        except Exception as e:
            logger.error(f"[安全门控] 安全专家异常，降级拒绝: {e}")
            return False, "安全专家异常，降级拒绝"

    @classmethod
    def resolve_review(cls, request_id: str, approved: bool, reason: str = ""):
        """CLI 调用此方法来解决用户审查请求"""
        future = cls._pending_reviews.get(request_id)
        if future and not future.done():
            future.set_result({"approved": approved, "reason": reason or ("用户批准" if approved else "用户拒绝")})

    def _check_medium_risk(
        self, tool_name: str, tool_params: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """MEDIUM 风险工具 — 快速路径检查（不依赖 LLM）"""
        if tool_name in ("write_file", "file_edit", "append_file"):
            path = tool_params.get("path", "")
            if path:
                from pathlib import Path
                from infra.tool_manager.tools.file_manager import _is_path_allowed
                if not _is_path_allowed(Path(path).expanduser()):
                    return False, f"路径不在允许范围内: {path}"
        elif tool_name == "run_command":
            command = tool_params.get("command", "")
            if command:
                from infra.tool_manager.tools.exec_command import _check_command_whitelist
                if not _check_command_whitelist(command):
                    return False, f"命令不在白名单中: {command}"
        elif tool_name == "run_python":
            code = tool_params.get("code", "")
            if code:
                ok, reason = _check_code_safety(code)
                if not ok:
                    return False, reason
        return True, "MEDIUM 风险工具，快速检查通过"

    @staticmethod
    def _build_review_prompt(
        tool_name: str,
        tool_params: Dict[str, Any],
        caller_tier: str,
        caller_model_id: str,
        dialog_context: str,
    ) -> str:
        """构建安全专家审批提示词"""
        params_str = ", ".join(f"{k}={repr(v)}" for k, v in tool_params.items())
        params_display = params_str if len(params_str) < 500 else params_str[:500] + "..."

        context_section = ""
        if dialog_context:
            context_section = f"\n模型最近的对话上下文：\n{dialog_context[:2000]}"

        return (
            "你是一个安全审查专家。你需要评估以下工具调用请求的风险。\n\n"
            f"调用者: {caller_model_id} (层级: {caller_tier})\n"
            f"工具: {tool_name}\n"
            f"参数: {params_display}\n"
            f"{context_section}\n\n"
            "评估标准：\n"
            "- 这个操作是否与当前对话上下文中的任务相关？\n"
            "- 操作的目标是否合理（如修改的文件是否是项目文件）？\n"
            "- 是否有越权或滥用风险？\n"
            "- 命令/代码是否包含危险模式？\n\n"
            "严格返回JSON格式（不要额外文字）：\n"
            '{"approved": true/false, "reason": "简短的中文原因"}'
        )

    @staticmethod
    def _parse_review_result(result: str, tool_name: str) -> Tuple[bool, str]:
        """解析安全专家审批结果"""
        import json
        import re

        text = result.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning(f"[安全门控] 无法解析 LLM 输出: {text[:200]}")
                    return False, f"安全专家无法解析审批结果，{tool_name} 被拒绝"
            else:
                logger.warning(f"[安全门控] 无法解析 LLM 输出: {text[:200]}")
                return False, f"安全专家无法解析审批结果，{tool_name} 被拒绝"

        approved = parsed.get("approved", False)
        reason = parsed.get("reason", "无原因")

        if approved:
            logger.info(f"[安全门控] {tool_name} 审批通过: {reason}")
            return True, reason
        else:
            logger.warning(f"[安全门控] {tool_name} 审批拒绝: {reason}")
            return False, f"安全专家拒绝 {tool_name}: {reason}"


# 全局单例
_tool_security_gate: Optional[ToolSecurityGate] = None


def get_tool_security_gate() -> ToolSecurityGate:
    """获取工具安全门控单例"""
    global _tool_security_gate
    if _tool_security_gate is None:
        lite_model = None
        try:
            from modules.thinking.experts.pre_gen_experts import _get_lite_model
            lite_model = _get_lite_model()
        except Exception as e:
            logger.debug(f"获取 lite 模型失败，安全门控将无 LLM 审批: {e}")
        _tool_security_gate = ToolSecurityGate(lite_model=lite_model)
    return _tool_security_gate
