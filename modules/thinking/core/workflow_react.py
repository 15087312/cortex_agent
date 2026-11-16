"""ReAct 工具内循环工作流子图 (Workflow ReAct Subflow)

将原 ModelRunner._generate_with_tools 中的多层嵌套循环拆解为：
标准图状态机 + 节点流式发射 + 条件自环。
同时通过 runner_ref 100% 复用 ModelRunner 的前端推流、安全门控、
断点压缩、控制工具与委托链能力。

节点拆解：
1. ReActInferenceNode: 上下文压缩/断点保存 + 流式调用 client.chat_stream（同步推给前端通道 & 工作流事件流）
2. ReActToolDispatchNode: 分类控制/委托/主管/查询/普通工具，处理无工具退出与主管拒绝重试
3. ReActToolExecutionNode: 调用 ModelRunner._execute_react_turn_tools 执行工具与安全门控，写回 messages
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from infra.model.base_model import ChatMessage
from modules.thinking.core.workflow_streaming import (
    EventType,
    NodeContext,
    StreamingNode,
    StreamingWorkflowEngine,
)
from utils.logger import setup_logger

logger = setup_logger(__name__)

_CONTROL_CALL_NAMES = frozenset({
    "continue_thinking",
    "respond_to_user",
    "request_skill",
    "list_skills",
    "stop_skill",
    "set_memory_focus",
    "stop_task",
    "query_delegation",
    "resume_delegation",
    "inspect_delegation",
    "read_context",
    "request_mode_change",
    "ask_user_intent",
    "narrow_my_tools",
    "reset_my_tools",
})

_CONTROL_RESULT_TOOLS = (
    "request_skill",
    "stop_skill",
    "list_skills",
    "stop_task",
    "query_delegation",
    "resume_delegation",
    "inspect_delegation",
    "read_context",
    "narrow_my_tools",
    "reset_my_tools",
)


@dataclass
class ReActLoopState:
    """ReAct 内循环状态载荷"""
    session_id: str
    model_id: str
    tier: str
    system_prompt: str
    user_prompt: str
    messages: List[ChatMessage] = field(default_factory=list)
    tools_with_control: List[Any] = field(default_factory=list)

    turn: int = 0
    max_turns: int = 15
    is_finished: bool = False
    final_output: str = ""
    rejection_count: int = 0

    current_content: str = ""
    current_reasoning: str = ""
    current_tool_calls: List[Any] = field(default_factory=list)

    normal_calls: List[Any] = field(default_factory=list)
    control_calls: List[Any] = field(default_factory=list)
    delegate_calls: List[Any] = field(default_factory=list)
    query_calls: List[Any] = field(default_factory=list)
    supervisor_calls: List[Any] = field(default_factory=list)


def _safe_emit_bg(ctx: NodeContext, event_type: EventType, payload: Dict[str, Any]) -> None:
    """在同步流式回调中安全异步发射工作流事件"""
    if not ctx.is_live:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(ctx.emit(event_type, payload))
    except RuntimeError:
        pass


class ReActInferenceNode(StreamingNode):
    """ReAct 推理节点：断点/压缩维护 + 实时流式输出（同时推给前端通道与工作流事件流）"""

    def __init__(
        self,
        node_id: str = "react_inference",
        client: Optional[Any] = None,
        runner_ref: Optional[Any] = None,
    ) -> None:
        super().__init__(node_id)
        self.client = client
        self.runner_ref = runner_ref

    async def execute(self, state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
        messages: List[ChatMessage] = list(state.get("messages", []))
        tools = state.get("tools_with_control", [])
        turn = state.get("turn", 0)
        max_turns = state.get("max_turns", 15)
        tier = state.get("tier", "large")
        model_id = state.get("model_id", "")
        user_prompt = state.get("user_prompt", "")
        runner = self.runner_ref

        if runner is not None:
            runner._status = "tool_loop"
            runner._react_loop = {"turn": turn, "max": max_turns, "tool": ""}
            if messages and inspect.isfunction(getattr(type(runner), "_save_resume_context", None)):
                runner._save_resume_context(messages)
            if inspect.iscoroutinefunction(getattr(runner, "_maybe_summarize_context", None)):
                if await runner._maybe_summarize_context(messages, user_prompt):
                    if inspect.isfunction(getattr(type(runner), "_save_resume_context", None)):
                        runner._save_resume_context(messages)
            # 检查执行模式是否被外部变更
            try:
                from config.settings import settings as _mode_settings
                current_mode = _mode_settings.effective_execution_mode
                last_mode = getattr(runner, "_last_known_mode", "")
                if current_mode != last_mode:
                    if last_mode:
                        logger.info(f"[ModelRunner] {model_id} 执行模式变更: {last_mode} → {current_mode}")
                        messages.append(ChatMessage(
                            role="system",
                            content=f"[系统通知] 执行模式已从 {last_mode} 切换为 {current_mode}，请按新模式策略执行。",
                        ))
                    runner._last_known_mode = current_mode
            except Exception as e:
                logger.warning(f"[ModelRunner] 执行模式检查失败: {e}")

        partial_parts: List[str] = []
        reasoning_parts: List[str] = []
        last_emit_len = 0
        last_emitted_text = ""
        last_chunk_len = 0
        reasoning_streamed = False

        def _on_reasoning(chunk: str) -> None:
            nonlocal reasoning_streamed
            if not chunk:
                return
            reasoning_streamed = True
            reasoning_parts.append(chunk)
            if runner is not None and hasattr(runner, "_push_reasoning_delta"):
                runner._push_reasoning_delta(chunk, turn)
            _safe_emit_bg(ctx, EventType.LLM_THINKING_CHUNK, {"delta": chunk, "turn": turn})

        def _on_token(chunk: str) -> None:
            nonlocal last_emit_len, last_emitted_text, last_chunk_len
            if not chunk:
                return
            if runner is not None and hasattr(runner, "activity") and runner.activity:
                runner.activity.touch("stream")
            partial_parts.append(chunk)
            current_full = "".join(partial_parts)
            if runner is not None:
                runner._current_streaming_content = current_full
                total = len(current_full)
                if total - last_emit_len >= 100 or "\n" in chunk:
                    delta = current_full[len(last_emitted_text):]
                    if delta and hasattr(runner, "_emit_streaming_content"):
                        runner._emit_streaming_content(delta, turn)
                        last_emitted_text = current_full
                    last_emit_len = total
                if total - last_chunk_len >= 40 or "\n" in chunk or chunk.endswith(("。", "！", "？", ".", "!", "?")):
                    piece = current_full[last_chunk_len:]
                    if piece and hasattr(runner, "_push_stream_chunk"):
                        runner._push_stream_chunk(piece, turn)
                        last_chunk_len = total
            _safe_emit_bg(ctx, EventType.LLM_TEXT_CHUNK, {"delta": chunk, "turn": turn})

        kwargs = {
            "messages": messages,
            "tools": tools,
            "max_tokens": getattr(self.client, "max_tokens", 4096),
            "max_retries": 2,
        }

        if self.client and hasattr(self.client, "chat_stream"):
            response = await self.client.chat_stream(
                on_token=_on_token,
                on_reasoning=_on_reasoning,
                **kwargs,
            )
        elif self.client and hasattr(self.client, "chat"):
            response = await self.client.chat(**kwargs)
        else:
            response = type("Resp", (), {
                "message": type("Msg", (), {
                    "content": "模拟生成文本",
                    "tool_calls": [],
                    "reasoning_content": "",
                })(),
                "usage": None,
            })()

        content = getattr(response.message, "content", None) or "".join(partial_parts)
        tool_calls = getattr(response.message, "tool_calls", None) or []
        reasoning = getattr(response.message, "reasoning_content", "") or "".join(reasoning_parts)

        if runner is not None:
            if hasattr(runner, "_record_usage"):
                runner._record_usage(getattr(response, "usage", None))
            if hasattr(runner, "activity") and runner.activity:
                runner.activity.touch("response")
            if content and last_chunk_len < len(content) and hasattr(runner, "_push_stream_chunk"):
                runner._push_stream_chunk(content[last_chunk_len:], turn)
                last_chunk_len = len(content)
            if not reasoning_streamed and hasattr(runner, "_push_reasoning"):
                runner._push_reasoning(reasoning)
            if hasattr(runner, "_persist_reasoning"):
                runner._persist_reasoning(reasoning)
            if tier in ("supervisor", "expert") and content and content.strip() and hasattr(runner, "_push_expert_output"):
                runner._push_expert_output(content)
            if tool_calls:
                tc_names = [getattr(tc, "name", "") for tc in tool_calls]
                runner._status = "tool_loop"
                runner._status_detail = ", ".join(tc_names)
                if getattr(runner, "_react_loop", None) is not None:
                    runner._react_loop["tool"] = ", ".join(tc_names)

        logger.info(
            f"[ReActInferenceNode] {model_id} 第 {turn} 轮生成完毕: "
            f"content_len={len(content)}, tool_calls={len(tool_calls)}"
        )

        return {
            "messages": messages,
            "current_content": content,
            "current_reasoning": reasoning,
            "current_tool_calls": tool_calls,
            "turn": turn + 1,
        }


class ReActToolDispatchNode(StreamingNode):
    """工具分流与控制节点：将 tool_calls 分类并判定是否应结束或继续"""

    def __init__(self, node_id: str = "react_dispatch", runner_ref: Optional[Any] = None) -> None:
        super().__init__(node_id)
        self.runner_ref = runner_ref

    async def execute(self, state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
        tool_calls = state.get("current_tool_calls") or []
        content = state.get("current_content") or ""
        tier = state.get("tier", "large")
        turn = max(0, state.get("turn", 1) - 1)
        max_turns = state.get("max_turns", 15)
        rejection_count = state.get("rejection_count", 0)
        expert_errors = list(state.get("expert_errors") or [])
        messages: List[ChatMessage] = list(state.get("messages", []))
        runner = self.runner_ref

        # 1. 无工具调用时的终止 / 主管拒绝重试
        if not tool_calls:
            if tier == "large" and content.strip():
                if runner and getattr(runner, "_thinker", None):
                    runner._thinker.record_control_decision({"continue": False, "result_summary": content})
                return {"is_finished": True, "final_output": content, "needs_retry": False}

            if tier == "expert" and content.strip():
                if expert_errors:
                    content += "\n\n[工具调用失败记录]\n" + "\n".join(f"  - {e}" for e in expert_errors)
                if runner and getattr(runner, "_thinker", None):
                    runner._thinker.record_control_decision({"continue": False, "result_summary": content})
                return {"is_finished": True, "final_output": content, "needs_retry": False}

            max_rej = getattr(runner, "MAX_NO_TOOL_REJECTIONS", 3) if runner else 3
            if tier == "supervisor" and rejection_count < max_rej and turn < max_turns - 1:
                rejection_count += 1
                logger.info(
                    f"[ReActToolDispatchNode] {state.get('model_id')} 第{turn}轮无工具调用，"
                    f"注入拒绝指令重试 ({rejection_count}/{max_rej})"
                )
                messages.append(ChatMessage(
                    role="system",
                    content=(
                        f"[系统拒绝 第{rejection_count}次] 纯文本输出不被接受。\n"
                        "你必须调用 delegate_task 或 continue_thinking 来继续。\n"
                        "不要调用其他普通工具（如 web_search 等）。\n"
                        "❌ 错误：输出纯文本或调用无关工具\n"
                        "✅ 正确：delegate_task 或 continue_thinking"
                    ),
                ))
                return {
                    "messages": messages,
                    "rejection_count": rejection_count,
                    "needs_retry": True,
                    "is_finished": False,
                }

            role_name = getattr(getattr(runner, "identity", None), "role", tier) if runner else tier
            task_desc = getattr(runner, "_task_description", "") if runner else ""
            response_text = content or f"[{role_name}] 已处理：{task_desc}"
            if runner and getattr(runner, "_thinker", None):
                runner._thinker.record_control_decision({"continue": False, "result_summary": response_text})
            return {"is_finished": True, "final_output": response_text, "needs_retry": False}

        # 2. 对工具进行分类
        normal_calls = []
        control_calls = []
        delegate_calls = []
        query_calls = []
        supervisor_calls = []

        for tc in tool_calls:
            name = getattr(tc, "name", "")
            if name in _CONTROL_CALL_NAMES:
                control_calls.append(tc)
            elif name == "delegate_task":
                delegate_calls.append(tc)
            elif name == "create_supervisor":
                supervisor_calls.append(tc)
            elif name == "query_tool_details":
                query_calls.append(tc)
            else:
                normal_calls.append(tc)

        # 3. 将会产生回包的调用写入 assistant 消息（对齐 OpenAI 契约）
        result_control_calls = [tc for tc in control_calls if getattr(tc, "name", "") in _CONTROL_RESULT_TOOLS]
        all_result_calls = normal_calls + query_calls + result_control_calls

        if all_result_calls:
            messages.append(ChatMessage(
                role="assistant",
                content=None,
                tool_calls=all_result_calls,
                reasoning_content=state.get("current_reasoning") or None,
            ))

        return {
            "messages": messages,
            "normal_calls": normal_calls,
            "control_calls": control_calls,
            "delegate_calls": delegate_calls,
            "query_calls": query_calls,
            "supervisor_calls": supervisor_calls,
            "needs_retry": False,
            "is_finished": False,
        }


class ReActToolExecutionNode(StreamingNode):
    """工具安全审查与实际执行节点：优先复用 ModelRunner._execute_react_turn_tools"""

    def __init__(self, node_id: str = "react_tool_exec", runner_ref: Optional[Any] = None) -> None:
        super().__init__(node_id)
        self.runner_ref = runner_ref

    async def execute(self, state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
        messages: List[ChatMessage] = list(state.get("messages", []))
        normal_calls = state.get("normal_calls") or []
        delegate_calls = state.get("delegate_calls") or []
        control_calls = state.get("control_calls") or []
        query_calls = state.get("query_calls") or []
        supervisor_calls = state.get("supervisor_calls") or []
        expert_errors = list(state.get("expert_errors") or [])
        turn = max(0, state.get("turn", 1) - 1)
        content = state.get("current_content") or ""

        # 1. 真实运行路径：复用 ModelRunner 完整的安全门控、前端推送与控制工具链
        if self.runner_ref is not None and inspect.iscoroutinefunction(
            getattr(self.runner_ref, "_execute_react_turn_tools", None)
        ):
            mcp = state.get("mcp")
            if mcp is None:
                from infra.mcp.factory import get_mcp_tool_service
                mcp = get_mcp_tool_service()

            for tc in normal_calls:
                await ctx.emit(EventType.TOOL_CALL, {
                    "call_id": getattr(tc, "id", None) or getattr(tc, "name", ""),
                    "tool": getattr(tc, "name", ""),
                })

            early_return = await self.runner_ref._execute_react_turn_tools(
                messages=messages,
                content=content,
                turn=turn,
                mcp=mcp,
                query_calls=query_calls,
                control_calls=control_calls,
                delegate_calls=delegate_calls,
                supervisor_calls=supervisor_calls,
                normal_calls=normal_calls,
                expert_errors=expert_errors,
            )

            for tc in normal_calls:
                await ctx.emit(EventType.TOOL_RESULT, {
                    "call_id": getattr(tc, "id", None) or getattr(tc, "name", ""),
                    "tool": getattr(tc, "name", ""),
                })

            if early_return is not None:
                return {
                    "messages": messages,
                    "expert_errors": expert_errors,
                    "is_finished": True,
                    "final_output": early_return,
                }

            return {
                "messages": messages,
                "expert_errors": expert_errors,
                "normal_calls": [],
                "control_calls": [],
                "delegate_calls": [],
                "query_calls": [],
                "supervisor_calls": [],
            }

        # 2. 独立测试兜底路径（无完整 ModelRunner 实例时）
        from infra.tool_manager.tool_registry import ToolRegistry

        if delegate_calls:
            for tc in delegate_calls:
                args = json.loads(tc.arguments) if isinstance(tc.arguments, str) and tc.arguments.strip() else {}
                role = args.get("role", "")
                task = args.get("task", "")
                await ctx.emit(EventType.TOOL_CALL, {"call_id": getattr(tc, "id", tc.name), "tool": "delegate_task", "args": args})
                if self.runner_ref and getattr(self.runner_ref, "_thinker", None):
                    self.runner_ref._thinker.record_delegation(role, task, {"task_id": getattr(tc, "id", "del_1"), "success": True})
            return {
                "is_finished": True,
                "final_output": f"【已委托给专家】{', '.join(getattr(tc, 'name', '') for tc in delegate_calls)}",
            }

        for tc in normal_calls:
            call_id = getattr(tc, "id", None) or tc.name
            tool_name = tc.name
            args = json.loads(tc.arguments) if isinstance(tc.arguments, str) and tc.arguments.strip() else {}

            await ctx.emit(EventType.TOOL_CALL, {
                "call_id": call_id,
                "tool": tool_name,
                "args": args,
            })

            start_time = time.time()
            try:
                func = ToolRegistry.get_func(tool_name)
                if func is None:
                    res_str = f"【错误】找不到工具 {tool_name}"
                else:
                    res = func(**args)
                    if asyncio.iscoroutine(res):
                        res = await res
                    res_str = str(res)
                status = "ok"
            except Exception as e:
                res_str = f"【工具执行异常】{e}"
                status = "error"

            duration_ms = (time.time() - start_time) * 1000
            await ctx.emit(EventType.TOOL_RESULT, {
                "call_id": call_id,
                "tool": tool_name,
                "result": res_str[:500],
                "duration_ms": duration_ms,
                "status": status,
            })

            messages.append(ChatMessage(
                role="tool",
                content=res_str[:4000],
                tool_call_id=call_id,
            ))

        return {
            "messages": messages,
            "normal_calls": [],
        }


class ReActSubflow:
    """ReAct 工具循环工作流管理器

    组装：ReActInferenceNode -> ReActToolDispatchNode -> ReActToolExecutionNode -> 回环到 InferenceNode
    """

    def __init__(self, client: Any, runner_ref: Optional[Any] = None) -> None:
        self.client = client
        self.runner_ref = runner_ref
        self.engine = StreamingWorkflowEngine()

        self.infer_node = ReActInferenceNode("inference", client=self.client, runner_ref=self.runner_ref)
        self.dispatch_node = ReActToolDispatchNode("dispatch", runner_ref=self.runner_ref)
        self.exec_node = ReActToolExecutionNode("execution", runner_ref=self.runner_ref)

        self._build_graph()

    def _build_graph(self) -> None:
        self.engine.add_node(self.infer_node)
        self.engine.add_node(self.dispatch_node)
        self.engine.add_node(self.exec_node)

        self.engine.set_entry_point("inference")
        self.engine.add_edge("inference", "dispatch")

        def route_after_dispatch(state: Dict[str, Any]) -> Optional[str]:
            if state.get("is_finished"):
                return None
            if state.get("needs_retry"):
                return "inference"
            if (
                state.get("normal_calls")
                or state.get("delegate_calls")
                or state.get("control_calls")
                or state.get("query_calls")
                or state.get("supervisor_calls")
            ):
                return "execution"
            return None

        self.engine.add_conditional_edge("dispatch", route_after_dispatch)

        def route_after_execution(state: Dict[str, Any]) -> Optional[str]:
            if state.get("is_finished"):
                return None
            if state.get("turn", 0) >= state.get("max_turns", 15):
                return None
            return "inference"

        self.engine.add_conditional_edge("execution", route_after_execution)

    async def run(self, initial_state: Dict[str, Any]) -> str:
        """运行 ReAct 子图（含重试与上限保护），返回最终产出文本"""
        runner = self.runner_ref
        raw_retries = getattr(runner, "GENERATE_RETRIES", 1) if runner else 1
        max_retries = raw_retries if isinstance(raw_retries, int) and raw_retries > 0 else 1
        raw_delay = getattr(runner, "GENERATE_RETRY_DELAY", 1.0) if runner else 1.0
        retry_delay = float(raw_delay) if isinstance(raw_delay, (int, float)) else 1.0
        last_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                final_state: Dict[str, Any] = {}
                async for ev in self.engine.run_stream(dict(initial_state)):
                    if ev.event == EventType.WORKFLOW_END.value:
                        final_state = ev.payload.get("final_state") or {}
                    elif ev.event == EventType.NODE_ERROR.value:
                        err_msg = ev.payload.get("error", "ReAct 节点执行失败")
                        raise RuntimeError(err_msg)

                if final_state.get("is_finished"):
                    return final_state.get("final_output", "") or final_state.get("current_content", "")

                # 未显式 finished 且达到 max_turns
                if final_state.get("turn", 0) >= final_state.get("max_turns", 15):
                    logger.warning(f"[ReActSubflow] {initial_state.get('model_id')} 工具调用达到最大轮次")
                    expert_errors = final_state.get("expert_errors") or []
                    if initial_state.get("tier") == "expert" and expert_errors:
                        return "[工具调用达到上限]\n\n[工具调用失败记录]\n" + "\n".join(f"  - {e}" for e in expert_errors)
                    return "[工具调用达到上限，返回当前结果]"

                return final_state.get("final_output", "") or final_state.get("current_content", "")

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                else:
                    if runner is not None:
                        runner._status = "error"
                        runner._status_detail = str(e)[:300]
                        logger.error(f"[ReActSubflow] {runner.model_id} 工具调用失败: {e}")

        if last_error and "503" in str(last_error):
            return "[系统] 模型 API 服务暂时繁忙（503），请稍后重试。"
        if last_error is not None:
            return f"[模型调用失败: {last_error}]"
        return ""
