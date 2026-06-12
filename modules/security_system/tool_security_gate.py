"""
工具安全门控 — 所有工具调用经过统一安全审查

设计意图：
  所有工具调用必须经过此门控。根据工具风险等级和执行模式，
  决定是直接放行、LLM 审批还是用户确认。

  多层防护链（从外到内）：
  1. check() — 外层包装：缓存相同调用 + 分发到 _check_impl
  2. _check_impl() — 实际审查：极端危险阻断 → 模式检查 → 风险等级检查
  3. _check_extreme_danger() — 硬阻断（rm -rf / 等）
  4. LLM 审批（HIGH 风险工具）
  5. 用户确认（control 模式）

  缓存设计（2026-06 新增）：
  完全相同的 tool_name + params 第二次调用直接返回缓存结果，
  避免同一命令重复触发 LLM 审批。缓存仅在当前会话有效，重启后清空。

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
import json
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
    extra_fields: Optional[Dict[str, Any]] = None,
):
    """推送安全事件到 stream"""
    if not _security_event_callback:
        return
    try:
        payload = {
            "caller": caller_model_id,
            "detail": detail,
            "duration_ms": duration_ms,
            "request_id": request_id,
        }
        if extra_fields:
            payload.update(extra_fields)
        _security_event_callback({
            "event_type": "security",
            "source": "tool_security_gate",
            "target": tool_name,
            "action": event_type,
            "success": success,
            "payload": payload,
        })
    except Exception as e:
        logger.debug(f"安全事件回调失败 (非致命): {e}")


# ── 从 ToolRegistry 动态读取风险等级和写操作工具（消除硬编码集合） ──

def _get_high_risk_tools() -> set:
    """HIGH + CRITICAL 风险工具（需要审批）"""
    from infra.tool_manager.tool_registry import ToolRegistry
    return ToolRegistry.get_tools_by_risk("HIGH") | ToolRegistry.get_tools_by_risk("CRITICAL")

def _get_medium_risk_tools() -> set:
    """MEDIUM 风险工具（快速路径检查）"""
    from infra.tool_manager.tool_registry import ToolRegistry
    return ToolRegistry.get_tools_by_risk("MEDIUM")

def _get_mutation_tools() -> set:
    """写操作工具（category 为 mutation 或 admin 的工具）"""
    from infra.tool_manager.tool_registry import ToolRegistry
    return ToolRegistry.get_mutation_tools()

# Plan 模式下 delegate_task 的写操作关键词（供 model_runner 和 security_gate 共用）
DELEGATE_WRITE_KEYWORDS = {
    "写入", "创建文件", "修改文件", "删除文件", "执行命令",
    "安装", "部署", "推送", "提交代码", "编写代码", "写文件",
    "新建文件", "编辑文件", "delete file", "execute command",
    "install", "deploy", "push --force", "git push",
    "commit", "edit file", "run command", "rm -rf",
    "write file", "create file", "modify file",
    "compile", "build", "write a", "write the",
}

# 用户审查超时（秒）
USER_REVIEW_TIMEOUT = 120

# ── 绝对危害性检测 — 无论何种模式都硬阻断 ──
import re as _re
_EXTREME_DANGER_PATTERNS_RAW = [
    # Unix destructive
    r'rm\s+-[rRf]*[rR][rRf]*f\s+/',              # rm -rf / (any target under /)
    r'rm\s+-[rRf]*[rR][rRf]*f\s+~',              # rm -rf ~
    r'rm\s+-[rRf]*[rR][rRf]*f\s+\.\s*$',         # rm -rf .
    r':\(\)\s*\{.*\|.*\}',                         # fork bomb :(){ :|:& };:
    r'\bmkfs\.',                                   # mkfs
    r'\bdd\s+if=/dev/',                            # dd if=/dev/zero etc
    r'>\s*/dev/sd',                                # overwrite disk
    r'nc\s+-l',                                    # reverse shell listener
    r'ncat\s+-l',                                  # reverse shell listener
    # Windows destructive
    r'del\s+/[sS]\s+/[qQ]\s+[A-Z]:\\',            # del /s /q C:\
    r'rd\s+/[sS]\s+/[qQ]\s+[A-Z]:\\',             # rd /s /q C:\
    r'\bformat\s+[A-Z]:',                          # format C:
    r'Remove-Item\s+.*-Recurse\s+.*-Force\s+[A-Z]:\\',  # PowerShell rm -rf
    r'Clear-RecycleBin\s.*-Force',                  # Empty recycle bin
    r'Invoke-Expression.*IEX.*Net\.WebClient',     # PowerShell download+execute
    r'New-Object\s+Net\.WebClient.*DownloadString', # PowerShell download
    r'powershell.*-enc\s+[A-Za-z0-9+/=]{20,}',     # Encoded PowerShell
]
_EXTREME_DANGER_RE = [_re.compile(p, _re.IGNORECASE) for p in _EXTREME_DANGER_PATTERNS_RAW]


def _check_extreme_danger(tool_name: str, tool_params: Dict[str, Any]) -> Optional[str]:
    """绝对危害性检测 — 匹配则硬阻断，无论执行模式

    检查 exec_command/run_command 的 command 参数，
    以及 run_script/run_python 的 code 参数。
    """
    # 提取要检查的文本
    texts = []
    if tool_name in ("exec_command", "run_command"):
        cmd = tool_params.get("command", "")
        if cmd:
            texts.append(cmd)
    elif tool_name in ("run_script", "run_python"):
        code = tool_params.get("code", "")
        if code:
            texts.append(code)

    for text in texts:
        for pattern in _EXTREME_DANGER_RE:
            if pattern.search(text):
                return f"极端危险操作被拦截: 匹配模式 '{pattern.pattern}'"
    return None


class ToolSecurityGate:
    """工具安全门控 — 统一审查所有工具调用"""

    def __init__(self, lite_model=None):
        self._pending_reviews: Dict[str, asyncio.Future] = {}
        self._lite_model = lite_model
        self._model_available = lite_model is not None
        self._audit = SecurityAuditLogger()
        self._active_blackboard = None  # 由编排层注入
        self._check_cache: Dict[str, Tuple[bool, str]] = {}  # 相同调用缓存
        logger.info(
            f"ToolSecurityGate 初始化 (LLM={'可用' if self._model_available else '不可用'})"
        )

    def set_active_blackboard(self, blackboard) -> None:
        """注入当前活跃的 Blackboard（由编排层调用）"""
        self._active_blackboard = blackboard

    @property
    def _review_mode(self) -> str:
        """获取当前审查模式"""
        try:
            from config.settings import settings
            return settings.SECURITY_REVIEW_MODE
        except Exception:
            return "auto"

    @property
    def _execution_mode(self) -> str:
        """获取当前执行模式（陪伴模式强制 plan）"""
        try:
            from config.settings import settings
            return settings.effective_execution_mode
        except Exception:
            return "edit"

    async def check(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        caller_tier: str,
        caller_model_id: str,
        dialog_context: str = "",
    ) -> Tuple[bool, str]:
        """审查工具调用请求（带相同调用缓存）"""
        # 相同调用缓存：完全相同的 tool_name + params 跳过重复审批
        cache_key = f"{tool_name}|{json.dumps(tool_params, sort_keys=True, ensure_ascii=False)}"
        cached = self._check_cache.get(cache_key)
        if cached is not None:
            return cached

        result = await self._check_impl(tool_name, tool_params, caller_tier, caller_model_id, dialog_context)
        self._check_cache[cache_key] = result
        return result

    async def _check_impl(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        caller_tier: str,
        caller_model_id: str,
        dialog_context: str = "",
    ) -> Tuple[bool, str]:
        """审查工具调用请求（实际实现，不带缓存）"""
        exec_mode = self._execution_mode

        # ── 绝对危害性硬阻断 — 无论何种模式都拦截 ──
        extreme_reason = _check_extreme_danger(tool_name, tool_params)
        if extreme_reason:
            _emit_security_event("极端危险拦截", tool_name, caller_model_id, False, extreme_reason)
            try:
                self._audit.log(
                    event_type="tool_blocked", level="CRITICAL",
                    content=tool_name, result=False,
                    metadata={"caller_model_id": caller_model_id, "reason": extreme_reason},
                )
            except Exception as audit_err:
                logger.error(f"[审计] 写入失败: {audit_err}")
            return False, extreme_reason

        # ── 安全最高指示：Blackboard 有安全拦截信号时，拒绝所有写操作 ──
        if tool_name in _get_mutation_tools():
            try:
                from modules.thinking.cognition.blackboard import CognitiveBlackboard
                # 检查当前活跃的 Blackboard 是否有安全拦截
                # 通过模块级变量获取（由编排层注入）
                bb = getattr(self, '_active_blackboard', None)
                if bb and bb.has_security_block():
                    block = bb.get_security_block()
                    reason = (
                        f"安全系统已拦截: {block.get('description', '检测到安全风险')}。"
                        f"请遵循最高指示后再继续。"
                    )
                    _emit_security_event("安全拦截", tool_name, caller_model_id, False, reason)
                    return False, reason
            except Exception as audit_err:
                logger.error(f'[审计] 写入失败: {audit_err}')

        # ── plan 模式：所有写操作直接拒绝 ──
        if exec_mode == "plan" and tool_name in _get_mutation_tools():
            reason = f"当前为 plan 模式（只读），禁止执行 {tool_name}"
            _emit_security_event("plan拦截", tool_name, caller_model_id, False, reason)
            try:
                self._audit.log(
                    event_type="tool_blocked", level="MEDIUM",
                    content=tool_name, result=False,
                    metadata={"caller_model_id": caller_model_id, "reason": reason, "execution_mode": "plan"},
                )
            except Exception as audit_err:
                logger.error(f'[审计] 写入失败: {audit_err}')
            return False, reason

        # ── plan 模式：delegate_task 检查 task 参数中的写操作关键词 ──
        if exec_mode == "plan" and tool_name == "delegate_task":
            task = str(tool_params.get("task", "")).lower()
            matched = [kw for kw in DELEGATE_WRITE_KEYWORDS if kw in task]
            if matched:
                reason = (
                    f"plan 模式下禁止委派写操作任务。"
                    f"检测到写操作关键词: {', '.join(matched[:3])}。"
                    f"如需执行写操作，请切换到 edit 或 yolo 模式。"
                )
                _emit_security_event("plan委派拦截", tool_name, caller_model_id, False, reason)
                return False, reason

        # ── control 模式：所有非 LOW 工具需用户确认 ──
        if exec_mode == "control":
            if tool_name in _get_high_risk_tools() or tool_name in _get_medium_risk_tools():
                _emit_security_event("等待用户审批", tool_name, caller_model_id, True, "control 模式，需用户确认")
                allowed, reason = await self._check_user_review(
                    tool_name, tool_params, caller_tier, caller_model_id
                )
                try:
                    self._audit.log(
                        event_type="tool_approved" if allowed else "tool_blocked",
                        level="HIGH" if tool_name in _get_high_risk_tools() else "MEDIUM",
                        content=tool_name,
                        result=allowed,
                        metadata={"caller_model_id": caller_model_id, "caller_tier": caller_tier,
                                 "reason": reason, "execution_mode": "control"},
                    )
                except Exception:
                    pass
                return allowed, reason
            else:
                # LOW 风险工具直接放行
                try:
                    self._audit.log(
                        event_type="tool_approved", level="LOW",
                        content=tool_name, result=True,
                        metadata={"caller_model_id": caller_model_id, "caller_tier": caller_tier,
                                 "reason": "LOW 风险工具，control 模式直接放行", "execution_mode": "control"},
                    )
                except Exception:
                    pass
                return True, "LOW 风险工具，control 模式直接放行"

        if tool_name in _get_high_risk_tools():
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
        elif tool_name in _get_medium_risk_tools():
            # MEDIUM 工具：edit/yolo 模式写操作需 LLM 审查
            if exec_mode in ("edit", "yolo") and tool_name in _get_mutation_tools():
                if self._model_available:
                    llm_ok, llm_reason = await self._check_llm_review(
                        tool_name, tool_params, caller_tier, caller_model_id, dialog_context
                    )
                    if not llm_ok:
                        allowed, reason = False, llm_reason
                    elif exec_mode == "edit":
                        # edit 模式：LLM 通过后再用户确认
                        allowed, reason = await self._check_user_review(
                            tool_name, tool_params, caller_tier, caller_model_id
                        )
                    else:
                        # yolo 模式：LLM 通过即可
                        allowed, reason = True, llm_reason
                else:
                    # LLM 不可用：edit 降级为用户确认，yolo 放行
                    if exec_mode == "edit":
                        allowed, reason = await self._check_user_review(
                            tool_name, tool_params, caller_tier, caller_model_id
                        )
                    else:
                        allowed, reason = True, "MEDIUM 风险工具，LLM 不可用，yolo 放行"
            else:
                allowed, reason = True, "MEDIUM 风险工具，直接放行"
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
        """HIGH 风险工具 — 根据审查模式和执行模式选择审批方式"""
        exec_mode = self._execution_mode
        mode = self._review_mode

        # yolo 模式：只走 LLM 审核，跳过用户确认
        if exec_mode == "yolo":
            if self._model_available:
                return await self._check_llm_review(
                    tool_name, tool_params, caller_tier, caller_model_id, dialog_context
                )
            else:
                return False, "yolo 模式下安全专家不可用，拒绝 HIGH 风险操作"

        # edit 模式：先 LLM 审批，通过后再用户确认（AND 逻辑）
        if exec_mode == "edit":
            if self._model_available:
                llm_ok, llm_reason = await self._check_llm_review(
                    tool_name, tool_params, caller_tier, caller_model_id, dialog_context
                )
                if not llm_ok:
                    return False, llm_reason  # LLM 拒绝直接拦截，不进用户审批
            # LLM 通过（或不可用）后，必须用户确认
            return await self._check_user_review(
                tool_name, tool_params, caller_tier, caller_model_id
            )

        # plan 模式（写操作已在 check() 最顶层拦截，不应到达这里）
        # 兜底按原 mode 路由（user/llm/auto）
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
        """用户审查模式 — 推送到 CLI，等待用户审批

        防重叠：如果同一工具已有待审批，返回等待提示而非创建新审批。
        """
        # 检查是否已有待审批
        for rid, fut in list(self._pending_reviews.items()):
            if not fut.done() and rid.startswith(f"review_{tool_name}_"):
                logger.info(f"[安全门控] {tool_name} 已有待审批 (id={rid})，跳过重复审批")
                return True, f"同一工具已在审批中，等待上一请求结果"

        request_id = uuid.uuid4().hex[:12]
        request_id = f"review_{tool_name}_{request_id}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_reviews[request_id] = future

        params_summary = ", ".join(f"{k}={repr(v)[:100]}" for k, v in tool_params.items())
        if len(params_summary) > 300:
            params_summary = params_summary[:300] + "..."

        # 确定风险等级和提示样式
        is_high_risk = tool_name in _get_high_risk_tools()
        risk_icon = "🔴" if is_high_risk else "🟠"
        risk_level = "HIGH" if is_high_risk else "MEDIUM"

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
                prompt, max_tokens=512, temperature=0.1
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
            '{"approved": true/false, "reason": "简短的中文原因", '
            '"guidance": "如果拒绝，告诉调用者应该怎么调整（用什么工具、改什么参数）"}'
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
            match = re.search(r'\{[\s\S]*?\}', text, re.DOTALL)
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
        guidance = parsed.get("guidance", "")

        if approved:
            logger.info(f"[安全门控] {tool_name} 审批通过: {reason}")
            return True, reason
        else:
            msg = f"安全专家拒绝 {tool_name}: {reason}"
            if guidance:
                msg += f"\n建议: {guidance}"
            logger.warning(f"[安全门控] {msg}")
            return False, msg


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


# ---------------------------------------------------------------------------
# Plan 模式输出检查 — 安全专家 LLM 审查模型输出是否包含写操作
# ---------------------------------------------------------------------------

_PLAN_OUTPUT_REVIEW_PROMPT = """你是安全审查专家。当前系统处于 plan 模式（只读），检查以下模型输出是否包含写操作指令。

【判定标准】
- PASS: 输出仅包含查询结果、分析、建议、说明，没有实际的写操作指令
- BLOCK: 输出包含实际的文件写入、代码修改、命令执行等写操作指令或代码

【注意】
- 提供建议和分析是允许的（如"建议修改xxx"、"分析结果如下"）
- 实际执行写操作是禁止的（如直接给出要写入的文件内容、要执行的命令）
- 如果输出只是告诉用户"当前为 plan 模式，无法执行写操作"，这是正确的 PASS 行为

请严格按 JSON 格式回复：
{{"decision": "PASS" 或 "BLOCK", "reason": "原因"}}"""


async def check_plan_output(output_text: str, caller_tier: str, caller_model_id: str) -> Tuple[bool, str]:
    """Plan 模式下检查模型输出是否包含写操作。

    Args:
        output_text: 模型输出文本
        caller_tier: 调用者层级 (large/supervisor/expert)
        caller_model_id: 调用者模型 ID

    Returns:
        (allowed, reason): True=通过, False=被拦截
    """
    # 非 plan 模式直接通过
    try:
        from config.settings import settings
        if settings.effective_execution_mode != "plan":
            return True, "非 plan 模式，跳过检查"
    except Exception:
        return True, "无法获取执行模式，跳过检查"

    # 输出太短不检查
    if len(output_text.strip()) < 50:
        return True, "输出过短，跳过检查"

    # 获取安全专家模型
    gate = get_tool_security_gate()
    if not gate._model_available:
        logger.warning("[Plan输出检查] 安全专家不可用，放行")
        return True, "安全专家不可用，放行"

    check_text = output_text

    try:
        prompt = (
            f"{_PLAN_OUTPUT_REVIEW_PROMPT}\n\n"
            f"【调用者】{caller_tier} ({caller_model_id})\n"
            f"【输出内容】\n{check_text}"
        )
        result = await gate._lite_model.generate(prompt, max_tokens=256, temperature=0.1)
        return _parse_plan_check_result(result, caller_tier, caller_model_id)
    except Exception as e:
        logger.error(f"[Plan输出检查] 安全专家异常，拦截: {e}")
        return False, f"安全专家异常，安全起见拦截: {e}"


def _parse_plan_check_result(result: str, caller_tier: str, caller_model_id: str) -> Tuple[bool, str]:
    """解析安全专家的 plan 输出检查结果"""
    import json
    try:
        # 提取 JSON
        text = result.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            decision = parsed.get("decision", "PASS").upper()
            reason = parsed.get("reason", "")
            if decision == "BLOCK":
                _emit_security_event(
                    "Plan输出拦截", f"{caller_tier}_output",
                    caller_model_id, False, reason,
                )
                logger.warning(f"[Plan输出检查] 拦截: {caller_tier} ({caller_model_id}) reason={reason}")
                return False, f"[Plan 模式拦截] {reason}"
            else:
                logger.debug(f"[Plan输出检查] 通过: {caller_tier} ({caller_model_id})")
                return True, reason
    except (json.JSONDecodeError, KeyError):
        pass

    # 解析失败 → 放行（不因解析问题阻断正常流程）
    logger.warning(f"[Plan输出检查] 解析失败，放行: {result[:100]}")
    return True, "解析失败，放行"
