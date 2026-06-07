"""
反思状态机 — 代码硬控的 5 触发点闭环

设计原则：
- 触发时机由代码硬编码，模型无权决定何时反思
- 模型评估通过外部注入的 reflect_fn 调用（可使用 SelfReflection 或 ExpertSystem）
- 规则兜底：无 reflect_fn 时纯规则判断
- 输出结构化 JSON: {"has_error": bool, "error_reason": str, "fix_suggestion": str}
"""
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Dict, Awaitable

from utils.logger import setup_logger

logger = setup_logger("reflection_sm")


class TriggerType(Enum):
    """5 个固定触发点"""
    STEP_COMPLETE = "step_complete"          # 时机1：单步完成后
    TOOL_ERROR = "tool_error"                # 时机2：工具执行错误
    PROBE_FAIL = "probe_fail"                # 时机3：探针失败
    STUCK_TIMEOUT = "stuck_timeout"           # 时机4：卡住/超时
    COLLAB_ROUND = "collab_round"             # 时机5：协作回合


class ReflectionDecision(Enum):
    """5 种决策结果"""
    PROCEED = "proceed"           # 继续
    RETRY = "retry"               # 重试（带修正建议）
    ROLLBACK = "rollback"         # 回退（撤销上一步操作）
    ASK_USER = "ask_user"         # 向用户询问
    TERMINATE = "terminate"       # 终止当前流程


@dataclass
class StepContext:
    """单步上下文 — 反思系统需要的信息"""
    node_id: str
    trigger: TriggerType
    task_goal: str = ""
    execution_log: str = ""
    thought_text: str = ""
    error_message: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReflectionOutcome:
    """反思决策结果"""
    decision: ReflectionDecision
    error_reason: str = ""
    suggestion: str = ""
    retry_count: int = 0
    trigger: Optional[TriggerType] = None


class ReflectionStateMachine:
    """反思状态机 — 代码硬控触发时机"""

    MAX_RETRIES = 3  # 同一节点最大重试次数

    # 严重错误关键词（触发 TERMINATE 或 ASK_USER）
    _MAJOR_ERROR_KEYWORDS = [
        "权限不足", "拒绝访问", "未授权", "forbidden", "permission denied",
        "不存在", "not found", "404",
        "超时", "timeout", "连接失败", "connection refused",
        "崩溃", "crash", "internal error", "internal server error",
    ]

    # 中等错误关键词（触发 RETRY）
    _MINOR_ERROR_KEYWORDS = [
        "失败", "错误", "error", "fail", "exception",
        "无效", "invalid", "格式错误", "parse error",
    ]

    def __init__(
        self,
        event_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        reflect_fn: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
    ):
        """
        Args:
            event_callback: 事件回调（用于向 CLI 发送诊断事件）
            reflect_fn: 外部注入的反思分析函数
                        async fn(context: dict) -> {"has_error": bool,
                                                     "error_reason": str,
                                                     "fix_suggestion": str}
                        可使用 SelfReflection._model_analyze() 或 ExpertSystem
        """
        self._event_callback = event_callback
        self._reflect_fn = reflect_fn
        # 节点级重试计数: {node_id: count}
        self._retry_counts: Dict[str, int] = {}
        self._trigger_counts: Dict[str, int] = {}  # 触发类型触发次数
        self._current_node: str = ""

        # 轮询模式的 stuck 检测
        self._last_thought_text: str = ""
        self._consecutive_identical: int = 0
        self._stuck_round_limit: int = 0  # 从 StuckTimeout 设置

    def set_event_callback(self, callback: Callable[[Dict[str, Any]], Any]):
        """设置事件回调（供外部动态注入）"""
        self._event_callback = callback

    def set_reflect_fn(self, fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]):
        """设置反思分析函数（供外部动态注入）"""
        self._reflect_fn = fn

    def reset(self):
        """重置状态机（新对话/新请求开始前调用）"""
        self._retry_counts.clear()
        self._trigger_counts.clear()
        self._current_node = ""
        self._last_thought_text = ""
        self._consecutive_identical = 0
        self._stuck_round_limit = 0

    # ── 5 个触发点 ──

    async def on_step_complete(self, ctx: StepContext) -> ReflectionOutcome:
        """时机1：单步完成后"""
        self._current_node = ctx.node_id
        self._track_trigger("step_complete")

        # 检测精确重复（stuck）
        if ctx.thought_text and ctx.thought_text == self._last_thought_text:
            self._consecutive_identical += 1
        else:
            self._consecutive_identical = 0
        self._last_thought_text = ctx.thought_text or ""

        # 先做规则检查
        rule_result = self._rule_check(ctx)
        if rule_result.decision != ReflectionDecision.PROCEED:
            self._emit_event(ctx, rule_result)
            return rule_result

        # stuck 检测
        stuck_outcome = self._check_stuck(ctx)
        if stuck_outcome.decision != ReflectionDecision.PROCEED:
            self._emit_event(ctx, stuck_outcome)
            return stuck_outcome

        # 模型反思（可选）
        if self._reflect_fn:
            model_result = await self._model_reflect(ctx)
            if model_result.decision != ReflectionDecision.PROCEED:
                self._emit_event(ctx, model_result)
                return model_result

        return ReflectionOutcome(decision=ReflectionDecision.PROCEED)

    async def on_tool_error(self, ctx: StepContext) -> ReflectionOutcome:
        """时机2：工具执行错误"""
        self._current_node = ctx.node_id
        self._track_trigger("tool_error")

        # 严重错误 → 立即 ASK_USER 或 TERMINATE
        if self._is_major_error(ctx.error_message):
            outcome = ReflectionOutcome(
                decision=ReflectionDecision.ASK_USER,
                trigger=TriggerType.TOOL_ERROR,
                error_reason=f"工具严重错误: {ctx.error_message[:100]}",
                suggestion="请检查工具配置或权限后重试",
            )
            self._emit_event(ctx, outcome)
            return outcome

        # 中等错误 → RETRY（需检查重试次数）
        if self._is_minor_error(ctx.error_message):
            node_key = f"{ctx.node_id}::{ctx.trigger.value}"
            count = self._retry_counts.get(node_key, 0)
            if count < self.MAX_RETRIES:
                self._retry_counts[node_key] = count + 1
                outcome = ReflectionOutcome(
                    decision=ReflectionDecision.RETRY,
                    trigger=TriggerType.TOOL_ERROR,
                    error_reason=f"工具错误: {ctx.error_message[:100]}",
                    suggestion=f"请修正后重试 (第{count + 1}/{self.MAX_RETRIES}次)",
                    retry_count=count + 1,
                )
                self._emit_event(ctx, outcome)
                return outcome
            else:
                outcome = ReflectionOutcome(
                    decision=ReflectionDecision.ASK_USER,
                    trigger=TriggerType.TOOL_ERROR,
                    error_reason=f"工具重试{self.MAX_RETRIES}次仍失败: {ctx.error_message[:100]}",
                    suggestion="建议更换工具或手动处理",
                )
                self._emit_event(ctx, outcome)
                return outcome

        # 模型反思（可选）
        if self._reflect_fn:
            model_result = await self._model_reflect(ctx)
            if model_result.decision != ReflectionDecision.PROCEED:
                self._emit_event(ctx, model_result)
                return model_result

        return ReflectionOutcome(decision=ReflectionDecision.PROCEED)

    async def on_probe_fail(self, ctx: StepContext) -> ReflectionOutcome:
        """时机3：探针失败"""
        self._current_node = ctx.node_id
        self._track_trigger("probe_fail")

        node_key = f"{ctx.node_id}::{ctx.trigger.value}"
        count = self._retry_counts.get(node_key, 0)

        if count < self.MAX_RETRIES:
            self._retry_counts[node_key] = count + 1
            outcome = ReflectionOutcome(
                decision=ReflectionDecision.RETRY,
                trigger=TriggerType.PROBE_FAIL,
                error_reason=ctx.error_message or "探针执行失败",
                suggestion=f"探针异常，重试 (第{count + 1}/{self.MAX_RETRIES}次)",
                retry_count=count + 1,
            )
        else:
            outcome = ReflectionOutcome(
                decision=ReflectionDecision.TERMINATE,
                trigger=TriggerType.PROBE_FAIL,
                error_reason=f"探针重试{self.MAX_RETRIES}次仍失败: {ctx.error_message}",
                suggestion="请检查系统状态后重试",
            )

        self._emit_event(ctx, outcome)
        return outcome

    async def on_stuck_timeout(self, ctx: StepContext) -> ReflectionOutcome:
        """时机4：卡住/超时"""
        self._current_node = ctx.node_id
        self._track_trigger("stuck_timeout")

        outcome = ReflectionOutcome(
            decision=ReflectionDecision.ROLLBACK,
            trigger=TriggerType.STUCK_TIMEOUT,
            error_reason=ctx.error_message or "思考卡住或超时",
            suggestion="回退到上一步，重新规划路径",
        )
        self._emit_event(ctx, outcome)
        return outcome

    async def on_collaboration_round(self, ctx: StepContext) -> ReflectionOutcome:
        """时机5：协作回合"""
        self._current_node = ctx.node_id
        self._track_trigger("collab_round")

        # 规则检查
        rule_result = self._rule_check(ctx)
        if rule_result.decision != ReflectionDecision.PROCEED:
            self._emit_event(ctx, rule_result)
            return rule_result

        if self._reflect_fn:
            model_result = await self._model_reflect(ctx)
            if model_result.decision != ReflectionDecision.PROCEED:
                self._emit_event(ctx, model_result)
                return model_result

        return ReflectionOutcome(decision=ReflectionDecision.PROCEED)

    # ── 内部方法 ──

    def _rule_check(self, ctx: StepContext) -> ReflectionOutcome:
        """规则检查 — 快速模式匹配，无模型调用"""
        error = ctx.error_message or ""

        # 精确重复卡住
        if self._consecutive_identical >= 3:
            return ReflectionOutcome(
                decision=ReflectionDecision.TERMINATE,
                error_reason="连续3轮输出完全相同的内容",
                suggestion="模型陷入死循环，强制终止",
            )

        # 严重错误
        if error:
            if self._is_major_error(error):
                return ReflectionOutcome(
                    decision=ReflectionDecision.ASK_USER,
                    error_reason=f"检测到严重错误: {error[:100]}",
                    suggestion="请检查配置或权限后重试",
                )

            if self._is_minor_error(error) and len(error) > 3:
                node_key = f"{ctx.node_id}::{ctx.trigger.value}"
                count = self._retry_counts.get(node_key, 0)
                if count < self.MAX_RETRIES:
                    self._retry_counts[node_key] = count + 1
                    return ReflectionOutcome(
                        decision=ReflectionDecision.RETRY,
                        error_reason=f"工具错误: {error[:100]}",
                        suggestion=f"修正后重试 (第{count + 1}/{self.MAX_RETRIES}次)",
                        retry_count=count + 1,
                    )
                else:
                    return ReflectionOutcome(
                        decision=ReflectionDecision.ASK_USER,
                        error_reason=f"重试{self.MAX_RETRIES}次仍失败: {error[:100]}",
                        suggestion="建议更换方案或手动处理",
                    )

        return ReflectionOutcome(decision=ReflectionDecision.PROCEED)

    async def _model_reflect(self, ctx: StepContext) -> ReflectionOutcome:
        """调用外部反思分析函数进行模型评估"""
        if not self._reflect_fn:
            return ReflectionOutcome(decision=ReflectionDecision.PROCEED)

        try:
            model_context = {
                "node_id": ctx.node_id,
                "trigger": ctx.trigger.value,
                "task_goal": ctx.task_goal,
                "execution_log": ctx.execution_log,
                "thought_text": ctx.thought_text,
                "error_message": ctx.error_message,
            }
            result = await self._reflect_fn(model_context)
            if result.get("has_error"):
                error_reason = result.get("error_reason", "")
                suggestion = result.get("fix_suggestion", "")

                # 根据错误严重程度决定决策
                if self._is_major_error(error_reason):
                    decision = ReflectionDecision.TERMINATE
                elif ctx.trigger in (TriggerType.STEP_COMPLETE, TriggerType.COLLAB_ROUND):
                    decision = ReflectionDecision.RETRY
                else:
                    decision = ReflectionDecision.RETRY

                return ReflectionOutcome(
                    decision=decision,
                    error_reason=error_reason,
                    suggestion=suggestion,
                )
        except Exception as e:
            logger.debug(f"模型反思调用失败 (非致命): {e}")

        return ReflectionOutcome(decision=ReflectionDecision.PROCEED)

    def _check_stuck(self, ctx: StepContext) -> ReflectionOutcome:
        """检测卡住情况"""
        # 连续重复内容
        if self._consecutive_identical >= 3:
            return ReflectionOutcome(
                decision=ReflectionDecision.TERMINATE,
                error_reason=f"连续{self._consecutive_identical}轮输出相同内容",
                suggestion="模型陷入重复循环，强制终止",
            )

        # 轮询模式：超过限制轮数无进展
        if self._stuck_round_limit > 0:
            step_count = self._trigger_counts.get("step_complete", 0)
            if step_count > self._stuck_round_limit:
                return ReflectionOutcome(
                    decision=ReflectionDecision.ROLLBACK,
                    error_reason=f"超过{self._stuck_round_limit}轮仍无进展",
                    suggestion="回退到上一步重新规划",
                )

        return ReflectionOutcome(decision=ReflectionDecision.PROCEED)

    def _is_major_error(self, text: str) -> bool:
        """判断是否为严重错误"""
        text_lower = text.lower()
        return any(kw in text_lower for kw in self._MAJOR_ERROR_KEYWORDS)

    def _is_minor_error(self, text: str) -> bool:
        """判断是否为中等错误"""
        text_lower = text.lower()
        return any(kw in text_lower for kw in self._MINOR_ERROR_KEYWORDS)

    def _track_trigger(self, trigger_name: str):
        """记录触发次数"""
        self._trigger_counts[trigger_name] = self._trigger_counts.get(trigger_name, 0) + 1

    def _emit_event(self, ctx: StepContext, outcome: ReflectionOutcome):
        """发送诊断事件到 CLI/日志"""
        if not self._event_callback:
            return
        try:
            self._event_callback({
                "type": "reflection",
                "event": "reflection_outcome",
                "content": {
                    "node": ctx.node_id,
                    "trigger": ctx.trigger.value,
                    "decision": outcome.decision.value,
                    "error_reason": outcome.error_reason[:200],
                    "suggestion": outcome.suggestion[:200],
                    "retry_count": outcome.retry_count,
                    "timestamp": time.time(),
                },
            })
        except Exception as e:
            logger.debug(f"事件回调发送失败: {e}")
