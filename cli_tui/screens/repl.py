"""主 REPL 屏幕 — 参考 Open-ClaudeCode screens/REPL.tsx"""

import asyncio
import time
from typing import Optional
from textual import work
from utils.logger import setup_logger

logger = setup_logger("tui_repl")
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Footer, Static
from cli_tui.widgets.approval_select import ApprovalSelect

from ..commands import find_command, is_command, get_all, Command
from ..services.api_client import APIClient
from ..services.ws_client import WSClient
from ..state import AppState

# 全局学习进度通知（由 model_runner 的学习管线回调）
_learn_progress_state: Optional[dict] = None


def notify_learn_progress(event: str, data: dict):
    """供后端 learn pipeline 调用的进度通知（运行在同一进程时有效）"""
    global _learn_progress_state
    if _learn_progress_state is None:
        _learn_progress_state = {}
    _learn_progress_state.update({"last_event": event, "last_data": data})

from ..widgets.header import Header
from ..widgets.message_list import MessageList
from ..widgets.prompt_input import PromptInput
from ..widgets.status_line import StatusLine
from ..widgets.debug_panel import DebugPanel
from ..widgets.tool_panel import ToolPanel
from ..widgets.command_suggestions import CommandSuggestions


class REPL(Screen):
    """主 REPL 界面"""

    # input-container 高度动态：输入框(3) + 建议框(最多12)
    CSS = """
    #header-container {
        dock: top;
        height: auto;
    }

    #status-container {
        dock: bottom;
        height: 1;
    }

    #input-area {
        dock: bottom;
        height: auto;
        margin: 0 1 1 1;
    }

    #input-box {
        height: 3;
    }

    #suggestions {
        height: auto;
        max-height: 12;
    }

    #body-area {
        height: 1fr;
        layout: horizontal;
    }

    #msg-col {
        width: 2fr;
    }

    #tool-col {
        width: 1fr;
        display: none;
    }

    #tool-col.visible {
        display: block;
    }

    #debug-col {
        width: 1fr;
        display: none;
    }

    #debug-col.visible {
        display: block;
    }

    MessageList {
        height: 100%;
    }

    ToolPanel {
        height: 100%;
    }

    DebugPanel {
        height: 100%;
    }

    PromptInput.approval-mode {
        border: heavy $warning;
        background: $warning 10%;
    }

    #approval-select {
        display: none;
        dock: bottom;
        margin: 0 1;
    }

    #approval-select.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("up", "history_back", "历史回退", show=False),
        Binding("down", "history_forward", "历史前进", show=False),
        Binding("escape", "stop_thinking", "停止思考", show=True, priority=True),
        Binding("ctrl+y", "retry_last", "重试", show=True, priority=True),
        Binding("ctrl+a", "approve_security", "批准", show=True, priority=True),
        Binding("ctrl+d", "reject_security", "拒绝", show=True, priority=True),
        Binding("ctrl+c", "app_quit", "退出", show=True, priority=True),
        Binding("ctrl+x", "cancel_and_reset", "取消", show=True, priority=True),
        Binding("shift+tab", "cycle_execution_mode", "切换模式", show=True),
    ]

    def __init__(self, state: AppState, ws_client: WSClient, api_client: APIClient):
        super().__init__()
        self.state = state
        self.ws = ws_client
        self.api = api_client
        self._ml = None
        self._suggestions: Optional[CommandSuggestions] = None
        self._paused_state = None  # ESC 暂停时保存的状态

    def compose(self) -> ComposeResult:
        with Vertical(id="header-container"):
            yield Header(self.state)

        with Horizontal(id="body-area"):
            with Vertical(id="msg-col"):
                ml = MessageList()
                self._ml = ml
                yield ml
            with VerticalScroll(id="tool-col",
                               classes="visible" if self.state.show_tools else ""):
                yield ToolPanel(self.state)
            with VerticalScroll(id="debug-col",
                               classes="visible" if self.state.debug_enabled else ""):
                yield DebugPanel(self.state)

        with Vertical(id="status-container"):
            yield StatusLine(self.state)

        # 审批选择器 — 默认隐藏，安全事件到达时显示
        self._approval_widget = ApprovalSelect(
            tool_name="",
            tool_detail="",
            options=[
                {"label": "Yes, approve", "value": "yes"},
                {"label": "No, reject", "value": "no"},
                {"label": "Custom reason (Tab)", "value": "custom"},
            ],
            on_confirm=self._on_approval_confirm,
            on_cancel=self._on_approval_cancel,
            id="approval-select",
        )
        yield self._approval_widget

        with Vertical(id="input-area"):
            suggestions = CommandSuggestions()
            self._suggestions = suggestions
            yield suggestions
            yield PromptInput(self.state)

    def on_mount(self):
        self._connect_ws()
        self.set_interval(0.3, self._refresh_chrome)
        # 设置命令建议回调
        if self._suggestions:
            self._suggestions.set_on_select(self._on_command_selected)

    def _on_command_selected(self, cmd: Command):
        """命令建议选中回调 — 执行选中的命令"""
        self._handle_command(cmd.name)

    def on_input_changed(self, event: Input.Changed):
        """输入变化时更新命令建议"""
        text = event.value
        if not self._suggestions:
            return
        if text.startswith("/") or text.startswith("!"):
            # 提取 / 后面的部分作为查询
            query = text.lstrip("/!").strip()
            self._suggestions.update_query(query)
        else:
            self._suggestions._dismiss()

    @work
    async def _connect_ws(self):
        ok = await self.ws.connect()
        if ok:
            self.state.connected = True
            self.state.session_id = self.ws.session_id or ""
            self.notify("已连接到后端", severity="information", timeout=2)
            # 同步执行模式
            self._sync_execution_mode()
            # 注册持久事件回调 (只注册一次，避免回调累积)
            self.ws._event_callbacks.clear()
            self.ws.on_event(self._persistent_ws_callback)
            # 启动后台监听，空闲时也能接收服务端主动推送（如主动搭话）
            self.ws.start_background_listener()
        else:
            self.notify("无法连接到后端服务", severity="error", timeout=5)

    async def _persistent_ws_callback(self, event):
        """持久 WebSocket 事件回调 — 只注册一次，所有请求共享"""
        # 更新最后事件时间
        if self.state.processing:
            self.state.last_event_time = time.time()

        ml = self._ml

        # 处理系统级事件
        msg_type = event.get("type", "")
        event_name = event.get("event", "")
        content = event.get("content", "")
        data = event.get("data", {}) or {}

        if msg_type == "retrying":
            if ml:
                ml.write(f"[bold yellow]🔄 {event.get('content', '重试中…')}[/bold yellow]")
            return
        if msg_type == "cancelled":
            return
        if msg_type == "status" and event_name == "thinking_progress":
            self.state.thinking_hint = content or "思考中…"
            self.state.debug_phase = data.get("phase", "thinking")
            self.state.debug_badge = data.get("badge", "思考中")
            self.state.debug_card = data or {}
            self.state.debug_events.append({
                "timestamp": time.time(),
                "phase": self.state.debug_phase,
                "content": content,
            })
            if len(self.state.debug_events) > self.state.max_debug_events:
                self.state.debug_events = self.state.debug_events[-self.state.max_debug_events:]

            # 解析活跃专家信息（更新状态，顶栏 Header 会显示）
            active_experts = data.get("active_experts", [])
            if active_experts:
                self.state.active_experts = active_experts
            # 上下文窗口占用（始终更新，即使为 0）
            if "context_tokens" in data:
                self.state.context_tokens = data["context_tokens"]
            if "context_window_size" in data:
                self.state.context_window_size = data["context_window_size"]
            return

        # 原有逻辑
        parsed = self.ws.parse_event(event)
        if parsed and ml:
            if parsed["kind"] == "dialog":
                # 流式增量：直接追加到消息列表（不走去重）
                if parsed.get("entry_type") == "streaming_delta":
                    ml.write(parsed.get("content", ""))
                else:
                    self.state.add_dialog_entry(parsed)
                    ml.add_dialog_entry(parsed)
            elif parsed["kind"] == "tool":
                self.state.add_tool_call(parsed)
            elif parsed["kind"] == "reflection":
                ml.add_reflection_event(parsed)
            elif parsed["kind"] == "security_review":
                self.state.pending_security_review = parsed
                tool = parsed.get('tool', '?')
                caller = parsed.get('caller', '?')
                detail = parsed.get('detail', '')
                request_id = parsed.get('request_id', '')
                self.state.thinking_hint = "🔒 等待安全审批"
                logger.info(f"[TUI] 安全审批事件: tool={tool}, request_id={request_id}")

                # 重建审批组件为标准安全审批选项
                try:
                    self._approval_widget.rebuild_options(
                        new_options=[
                            {"label": "Yes, approve", "value": "yes"},
                            {"label": "No, reject", "value": "no"},
                            {"label": "Custom reason (Tab)", "value": "custom"},
                        ],
                        new_title=f"安全审批 — {tool}",
                        new_detail=f"调用者: {caller} | {detail[:100]}",
                    )
                    self._approval_widget._on_confirm = self._on_approval_confirm
                    self._approval_widget._on_cancel = self._on_approval_cancel
                    self._approval_widget.add_class("visible")
                    self._approval_widget.focus_index = 0
                    self._approval_widget.focus()
                except Exception as e:
                    logger.error(f"[TUI] 安全审批组件显示失败: {e}", exc_info=True)
                return
            elif parsed["kind"] == "security":
                action = parsed.get("action", "")
                tool = parsed.get("tool", "")
                detail = parsed.get("detail", "")
                duration = parsed.get("duration_ms", 0)
                duration_str = f" ({duration}ms)" if duration else ""
                success = parsed.get("success", True)
                icon = "✅" if success else "❌"
                ml.write(f"  {icon} [dim]安全审查: {tool} {action}{duration_str} — {detail}[/dim]")

            elif parsed["kind"] == "mode_change_request":
                # 大模型请求切换执行模式
                request_id = parsed["request_id"]
                reason = parsed.get("reason", "")
                suggested = parsed.get("suggested_mode", "edit")
                _MODE_LABELS = {"plan": "📋 Plan", "edit": "✏️ Edit", "yolo": "🚀 YOLO", "control": "🎛️ Control"}
                ml.write(
                    f"\n[bold cyan]🔄 模式切换请求[/bold cyan]\n"
                    f"  原因: {reason}\n"
                    f"  建议: {_MODE_LABELS.get(suggested, suggested)}"
                )
                # 重建审批选择器选项
                try:
                    self._approval_widget.rebuild_options(
                        new_options=[
                            {"label": f"Yes, switch to {suggested}", "value": f"approve:{suggested}"},
                            {"label": "Switch to edit", "value": "approve:edit"},
                            {"label": "Switch to yolo", "value": "approve:yolo"},
                            {"label": "No, stay in current mode", "value": "reject"},
                        ],
                        new_title="模式切换",
                        new_detail=f"建议切换到 {suggested} 模式: {reason[:80]}",
                    )
                    self._approval_widget._on_confirm = lambda v, t: self._respond_mode_change(request_id, v, t)
                    self._approval_widget._on_cancel = lambda: self._respond_mode_change(request_id, "reject", "")
                    self._approval_widget.add_class("visible")
                    self._approval_widget.focus_index = 0
                    self._approval_widget.focus()
                except Exception as e:
                    logger.error(f"[TUI] 模式切换组件显示失败: {e}", exc_info=True)

            elif parsed["kind"] == "user_intent_request":
                # 大模型询问用户意图
                request_id = parsed["request_id"]
                question = parsed.get("question", "")
                options = parsed.get("options", [])
                context = parsed.get("context", "")
                if context:
                    ml.write(f"[dim]{context}[/dim]")
                ml.write(f"\n[bold cyan]❓ {question}[/bold cyan]")
                # 重建审批选择器选项
                try:
                    self._approval_widget.rebuild_options(
                        new_options=[
                            {"label": opt, "value": opt} for opt in options[:5]
                        ] + [{"label": "Custom answer (Tab)", "value": "custom"}],
                        new_title="用户意图",
                        new_detail=question[:100],
                    )
                    self._approval_widget._on_confirm = lambda v, t: self._respond_user_intent(request_id, v, t)
                    self._approval_widget._on_cancel = lambda: self._respond_user_intent(request_id, "", "用户取消")
                    self._approval_widget.add_class("visible")
                    self._approval_widget.focus_index = 0
                    self._approval_widget.focus()
                except Exception as e:
                    logger.error(f"[TUI] 用户意图组件显示失败: {e}", exc_info=True)

        if msg_type == "message" and event_name == "assistant_message":
            if content:
                self.state.final_response = content
                self.state.trace_id = data.get("trace_id", "")
                if ml:
                    ml.add_response(content)

        elif msg_type == "error":
            self.state.processing = False
            self.state.thinking_hint = ""
            error_message = data.get("error_message", content)
            self.state.last_error = error_message
            self.state.debug_phase = data.get("phase", "error")
            self.state.debug_badge = "错误"
            self.state.debug_card = data or {}
            self.state.debug_events.append({
                "timestamp": time.time(),
                "phase": self.state.debug_phase,
                "content": error_message,
            })
            if len(self.state.debug_events) > self.state.max_debug_events:
                self.state.debug_events = self.state.debug_events[-self.state.max_debug_events:]

            # 构建错误链信息（追踪错误来源）
            error_source = data.get("error_source", "cli")  # expert / supervisor / large_model / cli
            error_tier = data.get("tier", "unknown")
            self.state.error_chain.append({
                "timestamp": time.time(),
                "source": error_source,
                "tier": error_tier,
                "phase": self.state.debug_phase,
                "message": error_message,
            })

            if ml:
                ml.add_error(content)
                # 显示错误链信息
                if self.state.error_chain:
                    ml.write("[bold red]📋 错误链:[/bold red]")
                    for i, err in enumerate(self.state.error_chain[-3:], 1):  # 显示最近3个错误
                        tier_icon = {"expert": "👨", "supervisor": "👔", "large_model": "🤖", "cli": "💻"}.get(err["tier"], "❓")
                        ml.write(f"  [{i}] {tier_icon} [{err['source'].upper()}] {err['message']}")
                ml.write("[bold green]💡 提示：按 Ctrl+Y 重试上一次请求[/bold green]")


    def _refresh_chrome(self):
        for widget in self.query("Header"):
            widget.refresh()
        for widget in self.query("ToolPanel"):
            widget.refresh()
        for widget in self.query("StatusLine"):
            widget.refresh()

    # ── 历史导航 ──

    def action_history_back(self):
        try:
            inp = self.query_one(PromptInput)
            if inp.has_focus:
                val = inp.history_back()
                if val is not None:
                    inp.value = val
                    inp.cursor_position = len(val)
        except Exception as e:
            logger.debug("History back failed: %s", e)

    def action_history_forward(self):
        try:
            inp = self.query_one(PromptInput)
            if inp.has_focus:
                val = inp.history_forward()
                if val is not None:
                    inp.value = val
                    inp.cursor_position = len(val)
        except Exception as e:
            logger.debug("History forward failed: %s", e)

    def action_app_quit(self):
        self._do_exit()

    def action_retry_last(self):
        """Ctrl+Y：重试上一次请求"""
        if not self.state.last_user_input:
            self.notify("没有可重试的请求", severity="warning", timeout=2)
            return

        if self.state.processing:
            self.notify("当前还在处理中，请等待或按 ESC 停止", severity="warning", timeout=2)
            return

        self.notify(f"重试请求... (attempt {self.state.retry_count + 1})", timeout=1)
        self.state.retry_count += 1

        # 重新发送最后的输入
        inp = self.query_one(PromptInput)
        inp.value = self.state.last_user_input
        # 触发提交
        self.on_input_submitted(Input.Submitted(inp, self.state.last_user_input))

    def _resolve_security_review(self, approved: bool, reason: str = ""):
        """统一处理安全审批响应"""
        if not self.state.pending_security_review:
            self.notify("当前没有待审批的安全请求", severity="warning", timeout=2)
            return

        review = self.state.pending_security_review
        self.state.pending_security_review = None
        self.state.thinking_hint = ""

        # 隐藏审批组件
        self._approval_widget.remove_class("visible")

        # 恢复输入框焦点
        try:
            self.query_one(PromptInput).focus()
        except Exception:
            pass

        ml = self._ml
        if ml:
            if approved:
                ml.write(f"[bold green]✅ 用户批准: {review['tool']}[/bold green]")
            else:
                ml.write(f"[bold red]❌ 用户拒绝: {review['tool']}[/bold red]" + (f" — {reason}" if reason else ""))

        if self.ws:
            self.run_worker(
                self.ws.send_security_response(
                    review["request_id"], approved, reason
                )
            )

    def action_approve_security(self):
        """Ctrl+A：批准当前待审批的安全请求"""
        self._resolve_security_review(approved=True)

    def action_reject_security(self):
        """Ctrl+D：拒绝当前待审批的安全请求"""
        self._resolve_security_review(approved=False, reason="用户快捷键拒绝")

    def _on_approval_confirm(self, value: str, custom_text: str):
        """ApprovalSelect 确认回调"""
        self._resolve_security_review(
            approved=(value == "yes"),
            reason=custom_text if value == "custom" else ("用户拒绝" if value == "no" else "")
        )

    def _on_approval_cancel(self):
        """ApprovalSelect 取消回调"""
        self._resolve_security_review(approved=False, reason="用户取消")

    def _respond_mode_change(self, request_id: str, value: str, custom_text: str):
        """响应模式切换请求"""
        self._approval_widget.remove_class("visible")
        ml = self._ml
        if value.startswith("approve:"):
            mode = value.split(":", 1)[1]
            if ml:
                if mode == "learn":
                    desc = f" (任务: {custom_text})" if custom_text else ""
                    ml.write(f"[bold green]✅ 同意切换到 {mode} 模式{desc}[/bold green]")
                else:
                    ml.write(f"[bold green]✅ 同意切换到 {mode} 模式[/bold green]")
            self._set_execution_mode(mode)
            response_data = {"approved": True, "mode": mode}
            if mode == "learn" and custom_text:
                response_data["task"] = custom_text
            self._send_interactive_response(request_id, response_data)
        else:
            reason = custom_text or "用户拒绝"
            if ml:
                ml.write(f"[bold red]❌ 拒绝切换模式: {reason}[/bold red]")
            self._send_interactive_response(request_id, {"approved": False, "reason": reason})
        try:
            self.query_one(PromptInput).focus()
        except Exception:
            pass

    def _respond_user_intent(self, request_id: str, value: str, custom_text: str):
        """响应用户意图询问"""
        self._approval_widget.remove_class("visible")
        answer = custom_text if value == "custom" else value
        ml = self._ml
        if ml:
            ml.write(f"[bold green]💬 用户回答: {answer}[/bold green]")
        self._send_interactive_response(request_id, {"answer": answer})
        try:
            self.query_one(PromptInput).focus()
        except Exception:
            pass

    def _send_interactive_response(self, request_id: str, response: dict):
        """发送交互式工具响应到后端"""
        if self.ws:
            self.run_worker(self.ws.send_interactive_response(request_id, response))

    def action_cancel_and_reset(self):
        """Ctrl+X：立即取消当前处理，重置连接，提示用户重新输入"""
        if not self.state.processing:
            self.notify("当前没有正在处理的请求", timeout=2)
            return
        self.state.cancel_requested = True
        self.ws.request_cancel()
        self.state.processing = False
        self.state.cancel_requested = False
        if self._ml:
            self._ml.write("[bold red]✖ 已取消处理[/bold red]  请重新输入")
        self._reconnect_after_cancel()
        self.notify("已取消，正在重置连接…", severity="warning", timeout=3)

    @work
    async def _reconnect_after_cancel(self):
        """取消后异步重连"""
        self.ws._cancel_flag = False
        self.ws.stop_background_listener()
        await self.ws.close()
        ok = await self.ws.connect()
        if ok:
            self.state.connected = True
            self.state.session_id = self.ws.session_id or ""
            self.ws._event_callbacks.clear()
            self.ws.on_event(self._persistent_ws_callback)
            self.ws.start_background_listener()
            self.notify("连接已重置，可重新输入", severity="information", timeout=2)
        else:
            self.state.connected = False
            self.notify("重连失败，请检查后端", severity="error", timeout=5)

    # ── 输入处理 ──

    def on_key(self, event):
        """拦截键盘事件 — 审批组件和建议框可见时优先处理"""
        # 审批组件优先级最高
        if self._approval_widget and "visible" in self._approval_widget.classes:
            if event.key == "up":
                self._approval_widget.action_previous()
                event.prevent_default()
                return
            elif event.key == "down":
                self._approval_widget.action_next()
                event.prevent_default()
                return
            elif event.key == "enter":
                self._approval_widget.action_confirm()
                event.prevent_default()
                return
            elif event.key == "escape":
                self._approval_widget.action_cancel()
                event.prevent_default()
                return
            elif event.key == "tab":
                self._approval_widget.action_toggle_input()
                event.prevent_default()
                return
            elif event.key in ("1", "2", "3", "4", "5"):
                idx = int(event.key) - 1
                if 0 <= idx < len(self._approval_widget.options):
                    self._approval_widget.focus_index = idx
                    value = self._approval_widget.options[idx]["value"]
                    if self._approval_widget._on_confirm:
                        self._approval_widget._on_confirm(value, "")
                event.prevent_default()
                return

        if not self._suggestions or self._suggestions.styles.display == "none":
            return
        if event.key == "up":
            self._suggestions.action_navigate_up()
            event.prevent_default()
        elif event.key == "down":
            self._suggestions.action_navigate_down()
            event.prevent_default()
        elif event.key == "enter":
            # 如果建议框有选中项，执行选中而不是提交输入
            if self._suggestions._filtered:
                self._suggestions.action_select()
                event.prevent_default()
        elif event.key == "tab":
            # Tab 补全：将选中的命令名填入输入框
            if self._suggestions._filtered and self._suggestions._selected_index < len(self._suggestions._filtered):
                cmd = self._suggestions._filtered[self._suggestions._selected_index]
                input_widget = self.query_one(PromptInput)
                input_widget.value = cmd.name + " "
                input_widget.cursor_position = len(input_widget.value)
                self._suggestions._dismiss()
            event.prevent_default()
        elif event.key == "escape":
            self._suggestions._dismiss()
            try:
                self.query_one(PromptInput).focus()
            except Exception as e:
                logger.debug("Could not refocus input after escape: %s", e)
            event.prevent_default()

    def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        if not text:
            return

        # 审批组件可见时，文本输入作为自定义回答路由到审批组件
        if self._approval_widget and "visible" in self._approval_widget.classes:
            input_widget = self.query_one(PromptInput)
            input_widget.value = ""
            if self._approval_widget._on_confirm:
                self._approval_widget._on_confirm("custom", text)
            return

        # 关闭命令建议
        if self._suggestions:
            self._suggestions._dismiss()

        input_widget = self.query_one(PromptInput)
        input_widget.value = ""
        input_widget.reset_history()

        # 安全审查响应拦截（文本输入兜底 — ApprovalSelect 优先）
        if self.state.pending_security_review:
            review = self.state.pending_security_review
            self.state.pending_security_review = None
            self.state.thinking_hint = ""
            # 移除审批组件
            for w in self.query("ApprovalSelect"):
                w.remove()
            approved = text.lower() in ("y", "yes", "是", "批准", "允许", "approve")
            ml = self._ml
            if ml:
                if approved:
                    ml.write(f"[bold green]✅ 用户批准: {review['tool']}[/bold green]")
                else:
                    reason = text if text.lower() not in ("n", "no", "否", "拒绝", "deny") else ""
                    ml.write(f"[bold red]❌ 用户拒绝: {review['tool']}[/bold red]" + (f" — {reason}" if reason else ""))
            if self.ws:
                reason = "" if approved else (text if text.lower() not in ("n", "no", "否", "拒绝", "deny") else "用户拒绝")
                self.run_worker(
                    self.ws.send_security_response(
                        review["request_id"], approved, reason
                    )
                )
            return

        if is_command(text):
            self._handle_command(text)
            return

        # 保存用户输入以支持Ctrl+Y重试
        self.state.last_user_input = text
        self.state.add_input_history(text)
        self._process_user_input(text)

    @work(exclusive=True)
    async def _process_user_input(self, text: str):
        self.state.processing = True
        self.state.reset_for_new_input()
        ml = self._ml
        if ml:
            ml.reset_for_new_input()
            ml.write(f"[cyan]👤 用户[/cyan]: {text}")

        # 停止后台监听，避免与 process_input 的接收循环冲突
        self.ws.stop_background_listener()

        try:
            # 加载上下文和记忆（在发送前）
            await self._load_context_and_memory()

            await self.ws.process_input(
                text,
                state=self.state,
                warn_callback=self._warn_to_ml,
            )
        except Exception as e:
            self.notify(f"处理出错: {e}", severity="error")
            if ml:
                ml.add_error(str(e))
        finally:
            self.state.processing = False
            self.state.elapsed_ms = self.ws.elapsed_ms
            self.state.trace_id = self.ws.trace_id
            self.state.thinking_hint = ""
            if ml and self.state.elapsed_ms:
                ml.write(
                    f"\n[dim]耗时: {self.state.elapsed_ms:.0f}ms  "
                    f"trace: {self.state.trace_id[:12] if self.state.trace_id else '-'}[/dim]"
                )
            # 重启后台监听，空闲时继续接收主动消息
            self.ws.start_background_listener()

    async def _warn_to_ml(self, markup: str):
        """向消息列表写一行提示（由 ws_client 通过 warn_callback 调用）"""
        if self._ml:
            self._ml.write(markup)

    async def _load_context_and_memory(self):
        """加载上下文和记忆信息（显示为系统提示）"""
        ml = self._ml
        if not ml:
            return

        try:
            # 并行加载上下文、个性和记忆
            context_data, personality, emotion = await asyncio.gather(
                self.api.get_context(limit=5),
                self.api.get_personality(),
                self.api.get_user_emotion(),
                return_exceptions=True
            )

            # 显示加载的上下文信息
            if context_data and isinstance(context_data, dict):
                messages = context_data.get("messages", [])
                if messages:
                    ml.write("[dim]📚 已加载上下文:[/dim]")
                    for msg in messages[-3:]:  # 显示最近3条
                        sender = msg.get("role", "unknown")
                        content = msg.get("content", "")[:100]
                        ml.write(f"  [dim]• {sender}: {content}...[/dim]")

            # 显示人格和情绪
            if personality and isinstance(personality, dict):
                traits = personality.get("traits", {})
                if traits:
                    ml.write(f"[dim]⚙️ 人格特质: {', '.join(list(traits.keys())[:3])}[/dim]")

            if emotion and isinstance(emotion, dict):
                emotion_type = emotion.get("type", "neutral")
                ml.write(f"[dim]😊 当前情绪: {emotion_type}[/dim]")

        except Exception as e:
            logger.debug("Failed to load context/memory (non-critical): %s", e)

    # ── 命令处理 ──

    def _handle_command(self, text: str):
        cmd = find_command(text)
        if not cmd or cmd.action == "exit":
            self._do_exit()
        elif cmd.action == "help":
            self.app.push_screen("help")
        elif cmd.action == "clear":
            self.state.dialog_entries = []
            self.state.tool_calls = []
            self.state.tool_stats = {"total": 0, "success": 0, "failed": 0, "total_latency_ms": 0.0}
            self.state.final_response = ""
            if self._ml:
                self._ml.clear()
            self.notify("已清屏", timeout=1)
        elif cmd.action == "status":
            self._show_status()
        elif cmd.action == "memory":
            self._show_memory()
        elif cmd.action == "session":
            # 支持 /session <主管名> 查看副会话详情
            parts = text.split(" ", 1)
            supervisor_name = parts[1].strip() if len(parts) > 1 else ""
            self._show_sessions(supervisor_name)
        elif cmd.action == "tools":
            self.state.show_tools = not self.state.show_tools
            state_str = "开" if self.state.show_tools else "关"
            self.notify(f"工具面板: {state_str}", timeout=1)
            try:
                tool_col = self.query_one("#tool-col")
                if self.state.show_tools:
                    tool_col.add_class("visible")
                else:
                    tool_col.remove_class("visible")
                tool_col.refresh(layout=True)
            except Exception as e:
                logger.debug("Failed to toggle tool panel visibility: %s", e)
        elif cmd.action == "debug":
            self.state.debug_enabled = not self.state.debug_enabled
            state_str = "开" if self.state.debug_enabled else "关"
            self.notify(f"调试面板: {state_str}", timeout=1)
            try:
                debug_col = self.query_one("#debug-col")
                if self.state.debug_enabled:
                    debug_col.add_class("visible")
                else:
                    debug_col.remove_class("visible")
                debug_col.refresh(layout=True)
            except Exception as e:
                logger.debug("Failed to toggle debug panel visibility: %s", e)
        elif cmd.action == "thinking":
            self.state.show_thinking = not self.state.show_thinking
            state_str = "开" if self.state.show_thinking else "关"
            self.notify(f"思考显示: {state_str}", timeout=1)
        elif cmd.action == "export":
            self._do_export()
        elif cmd.action == "search":
            # 支持 /search <query> 搜索记忆
            parts = text.split(" ", 1)
            query = parts[1].strip() if len(parts) > 1 else ""
            if query:
                self._search_memory(query)
            else:
                self.notify("用法: /search <查询词>", severity="warning", timeout=2)
        elif cmd.action == "context":
            self._show_context()
        elif cmd.action == "stop":
            self._pause_thinking()
        elif cmd.action == "mode":
            # 支持 /mode on/off (陪伴模式) 或 /mode plan/edit/yolo (执行模式)
            parts = text.split(" ", 1)
            toggle_value = parts[1].strip().lower() if len(parts) > 1 else None
            if toggle_value in ("plan", "edit", "yolo", "control"):
                self._set_execution_mode(toggle_value)
            else:
                self._toggle_companion_mode(toggle_value)
        elif cmd.action == "config":
            # 支持 /config 查看或 /config KEY VALUE 修改
            parts = text.split(" ", 1)
            if len(parts) > 1:
                config_args = parts[1].strip()
                self._manage_config(config_args)
            else:
                self._show_config()
        elif cmd.action == "setup":
            # /setup [component] — 直接下载或显示状态
            parts = text.strip().split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            if arg in ("all", "omniparser", "qwen-vl-2b", "qwen-vl-7b-mlx"):
                self._run_setup_download(arg)
            elif arg == "status":
                self._show_setup_guide()
            else:
                self._show_setup_guide()
        else:
            self.notify(f"未知命令: {text}", severity="warning")

    @work
    async def _do_exit(self):
        self.state.connected = False
        await self.ws.close()
        self.app.exit()

    @work
    async def _show_status(self):
        data = await self.api.get_status()
        if data:
            self.notify(
                f"运行中: {data.get('running')}\n"
                f"会话数: {data.get('sessions')}\n"
                f"运行中会话: {data.get('running_sessions')}",
                title="系统状态", timeout=5,
            )
        else:
            self.notify("无法获取状态", severity="error", timeout=3)

    @work
    async def _show_memory(self):
        data = await self.api.get_memory()
        if data:
            st = data.get("short_term", {})
            bb = data.get("blackbox", {})
            self.notify(
                f"短期记忆轮数: {st.get('context_turns', '?')}\n"
                f"黑盒日志: {bb.get('total_size_kb', '?')} KB",
                title="记忆状态", timeout=5,
            )
        else:
            self.notify("无法获取记忆状态", severity="error", timeout=3)

    @work
    async def _show_sessions(self, supervisor_name: str = ""):
        sessions = await self.api.get_sessions()
        if sessions is None:
            self.notify("无法获取会话信息", severity="error", timeout=3)
            return

        ml = self._ml
        if not sessions:
            if ml:
                ml.add_response("当前没有活跃会话。")
            return

        if supervisor_name:
            # 查看指定副会话详情
            target = None
            for s in sessions:
                if s.get("supervisor_name", "") == supervisor_name:
                    target = s
                    break
            if not target:
                names = [s["supervisor_name"] for s in sessions if s.get("supervisor_name")]
                msg = f"未找到副会话「{supervisor_name}」。可用主管: {', '.join(names) if names else '无'}"
                if ml:
                    ml.add_response(msg)
                return

            lines = [f"=== 副会话 [{target['supervisor_name']}] ==="]
            entries = target.get("dialog_entries", [])
            if not entries:
                lines.append("(暂无聊天记录)")
            else:
                tier_labels = {"large": "总指挥", "supervisor": "主管", "expert": "专家", "user": "用户"}
                for e in entries:
                    label = tier_labels.get(e.get("tier", ""), e.get("tier", ""))
                    etype = {"thought": "思考", "response": "回复", "user_input": "输入"}.get(
                        e.get("type", ""), e.get("type", "")
                    )
                    lines.append(f"  [{label}] {e.get('model_id','')} ({etype}): {e.get('content','')[:200]}")
            if ml:
                ml.add_response("\n".join(lines))
        else:
            # 显示所有会话概览
            lines = ["=== 会话列表 ==="]
            for s in sessions:
                markers = []
                if s.get("is_main"):
                    markers.append("主会话")
                if s.get("supervisor_name"):
                    markers.append(f"主管: {s['supervisor_name']}")
                participants = s.get("participant_count", 0)
                dialog_size = s.get("dialog_size", 0)
                sid = s["session_id"][:20]
                lines.append(f"  {sid} | {' | '.join(markers)} | {participants}参与者 | {dialog_size}条消息")
            lines.append("")
            lines.append("查看副会话详情: /session <主管名>")
            if ml:
                ml.add_response("\n".join(lines))

    @work
    async def _search_memory(self, query: str):
        """搜索长期记忆"""
        ml = self._ml
        if not ml:
            return

        self.notify(f"搜索中: {query}", timeout=1)
        results = await self.api.search_memory(query, memory_type="thought", limit=5)

        if results and isinstance(results, list):
            ml.write(f"[bold cyan]📚 记忆搜索结果: '{query}' ({len(results)} 条)[/bold cyan]")
            for i, result in enumerate(results[:5], 1):
                content = result.get("content", "")[:150]
                created_at = result.get("created_at", "")
                ml.write(f"  [{i}] {content}")
                ml.write(f"      [dim]时间: {created_at}[/dim]")
        else:
            ml.write(f"[dim]未找到相关记忆: '{query}'[/dim]")

    @work
    async def _show_context(self):
        """加载并显示当前上下文"""
        ml = self._ml
        if not ml:
            return

        self.notify("加载上下文中...", timeout=1)
        ml.write("[bold cyan]📖 当前上下文:[/bold cyan]")

        context_data, personality, emotion = await asyncio.gather(
            self.api.get_context(limit=10),
            self.api.get_personality(),
            self.api.get_user_emotion(),
            return_exceptions=True
        )

        if context_data and isinstance(context_data, dict):
            messages = context_data.get("messages", [])
            ml.write(f"[dim]对话历史 ({len(messages)} 条):[/dim]")
            for msg in messages[-5:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:100]
                ml.write(f"  [{role}]: {content}...")

        if personality and isinstance(personality, dict):
            traits = personality.get("traits", {})
            if traits:
                ml.write(f"[dim]人格特质: {', '.join(f'{k}={v}' for k, v in list(traits.items())[:3])}[/dim]")

        if emotion and isinstance(emotion, dict):
            emotion_type = emotion.get("type", "neutral")
            intensity = emotion.get("intensity", 0)
            ml.write(f"[dim]情绪状态: {emotion_type} (强度: {intensity})[/dim]")

    def _do_export(self):
        import json
        from datetime import datetime

        if not self.state.tool_calls:
            self.notify("暂无工具调用可导出", severity="warning")
            return

        filename = f"tool_trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(
                {"stats": self.state.tool_stats, "tool_calls": self.state.tool_calls},
                f, indent=2, ensure_ascii=False,
            )
        self.notify(f"已导出到 {filename} ({len(self.state.tool_calls)} 条)", timeout=3)

    # ── 思考控制和配置管理 ──

    def action_stop_thinking(self):
        """ESC 键处理 — 暂停当前思考"""
        # 如果建议框可见，ESC 只关闭建议框
        if self._suggestions and self._suggestions.styles.display != "none":
            self._suggestions._dismiss()
            try:
                self.query_one(PromptInput).focus()
            except Exception as e:
                logger.debug("Could not refocus input after dismissing suggestions: %s", e)
            return

        if not self.state.processing:
            self.notify("当前没有正在处理的任务", severity="warning", timeout=2)
        else:
            self._pause_thinking()

    @work
    async def _pause_thinking(self):
        """暂停当前思考处理 — 显示暂停状态和恢复提示"""
        if not self.state.processing:
            self.notify("当前没有正在处理的任务", severity="warning", timeout=2)
            return

        ml = self._ml
        elapsed_s = 0
        if self.state.processing_start_time:
            elapsed_s = int(time.time() - self.state.processing_start_time)

        # 保存暂停前的状态
        self._paused_state = {
            "last_input": self.state.last_user_input,
            "elapsed_s": elapsed_s,
            "thinking_hint": self.state.thinking_hint,
            "active_experts": list(self.state.active_experts),
        }

        self.notify("正在暂停思考...", timeout=1)
        # 通过 WebSocket 发送 stop 信号（HTTP /stream/stop 不存在）
        success = await self.ws.send_stop()
        if success:
            self.state.processing = False
            self.state.thinking_hint = ""

            if ml:
                # 显示暂停状态面板
                pause_info = f"⏸ [bold yellow]思考已暂停[/bold yellow]"
                if elapsed_s > 0:
                    pause_info += f"  [dim](已运行 {elapsed_s}s)[/dim]"
                if self._paused_state.get("thinking_hint"):
                    pause_info += f"\n  [dim]状态: {self._paused_state['thinking_hint']}[/dim]"
                if self._paused_state.get("active_experts"):
                    experts = ", ".join(self._paused_state["active_experts"][:3])
                    pause_info += f"\n  [dim]活跃专家: {experts}[/dim]"
                pause_info += (
                    f"\n  [dim]Ctrl+Y 重试上次请求  |  直接输入新内容继续[/dim]"
                )
                ml.write(pause_info)

            self.notify("✓ 思考已暂停 — Ctrl+Y 重试，或输入新内容继续", severity="information", timeout=4)
        else:
            self.notify("✗ 暂停失败，请重试", severity="error", timeout=2)

    @work
    async def _toggle_companion_mode(self, toggle_value: Optional[str] = None):
        """切换或设置陪伴模式"""
        if toggle_value in ["on", "off"]:
            # 设置具体的值
            success = await self.api.update_config("COMPANION_MODE", toggle_value == "on")
            if success:
                mode_str = "开启" if toggle_value == "on" else "关闭"
                self.notify(f"✓ 陪伴模式: {mode_str}", severity="information", timeout=2)
                self.state.companion_mode = toggle_value == "on"
                self._sync_execution_mode()
            else:
                self.notify("✗ 设置失败", severity="error", timeout=2)
        else:
            # 切换
            result = await self.api.toggle_companion_mode()
            if result is not None:
                mode_str = "开启 (完全AI引导)" if result else "关闭 (工作模式)"
                self.notify(f"✓ 陪伴模式: {mode_str}", severity="information", timeout=2)
                self.state.companion_mode = result
                self._sync_execution_mode()
            else:
                self.notify("✗ 切换失败", severity="error", timeout=2)

    def _sync_execution_mode(self):
        """从后端同步执行模式到本地状态"""
        try:
            from config.settings import settings
            self.state.execution_mode = settings.effective_execution_mode
            self.state.companion_mode = settings.COMPANION_MODE
        except Exception:
            pass
        self._fetch_execution_mode()

    @work
    async def _fetch_execution_mode(self):
        """从后端 API 获取最新执行模式"""
        config = await self.api.get_config()
        if config:
            if "EXECUTION_MODE" in config:
                self.state.execution_mode = config["EXECUTION_MODE"]
            if "COMPANION_MODE" in config:
                self.state.companion_mode = config["COMPANION_MODE"]
            if self.state.companion_mode:
                self.state.execution_mode = "plan"

    def _set_execution_mode(self, mode: str):
        """设置执行模式（本地 + 后端）"""
        if self.state.companion_mode:
            self.notify("陪伴模式下执行模式固定为 plan，无法切换", severity="warning", timeout=3)
            return

        _MODE_CYCLE = ["plan", "edit", "yolo", "control", "learn"]
        if mode not in _MODE_CYCLE:
            self.notify(f"未知模式: {mode}，可选: plan/edit/yolo/control", severity="warning", timeout=3)
            return

        self.state.execution_mode = mode
        self.api.update_config("EXECUTION_MODE", mode)
        _LABELS = {"plan": "📋 Plan (只读)", "edit": "✏️ Edit (确认)", "yolo": "🚀 YOLO (宽松)", "control": "🎛️ Control (审批)", "learn": "🎓 Learn (学习)"}
        self.notify(f"✓ 执行模式: {_LABELS[mode]}", severity="information", timeout=2)

    def action_cycle_execution_mode(self):
        """Shift+Tab 循环切换执行模式: plan → edit → yolo → control → learn → plan"""
        if self.state.companion_mode:
            self.notify("陪伴模式下执行模式固定为 plan", severity="warning", timeout=2)
            return

        _MODE_CYCLE = ["plan", "edit", "yolo", "control", "learn"]
        current = self.state.execution_mode
        idx = _MODE_CYCLE.index(current) if current in _MODE_CYCLE else 1
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        self._set_execution_mode(next_mode)

    @work
    async def _show_config(self):
        """显示当前配置"""
        ml = self._ml
        if not ml:
            return

        config = await self.api.get_config()
        if config:
            ml.write("[bold cyan]⚙️  当前配置:[/bold cyan]")
            # 只显示重要配置
            important_keys = [
                "COMPANION_MODE",
                "PERCEPTION_ENABLED",
                "DIFFERENCE_DETECTOR_ENABLED",
                "APP_ENV",
                "LOG_LEVEL",
            ]
            for key in important_keys:
                if key in config:
                    value = config[key]
                    # 布尔值友好展示
                    if isinstance(value, bool):
                        value = "✓ 开启" if value else "✗ 关闭"
                    ml.write(f"  {key}: {value}")
        else:
            ml.write("[dim]无法获取配置信息[/dim]")

    @work
    async def _manage_config(self, config_args: str):
        """修改配置"""
        parts = config_args.split(" ", 1)
        if len(parts) == 1:
            # 只提供了KEY，不知道怎么处理
            self.notify(
                "用法: /config KEY VALUE\n示例: /config COMPANION_MODE true",
                severity="warning", timeout=3
            )
            return

        key = parts[0].strip().upper()
        value_str = parts[1].strip().lower()

        # 类型转换
        if value_str in ["true", "yes", "on", "1"]:
            value = True
        elif value_str in ["false", "no", "off", "0"]:
            value = False
        elif value_str.isdigit():
            value = int(value_str)
        else:
            value = value_str

        success = await self.api.update_config(key, value)
        if success:
            self.notify(f"✓ 配置已更新: {key} = {value}", severity="information", timeout=2)
        else:
            self.notify(f"✗ 更新失败: {key}", severity="error", timeout=2)

    def _show_setup_guide(self):
        """显示模型下载引导并提供直接下载选项"""
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script_path = os.path.join(project_root, "scripts", "setup_models.py")

        if not os.path.exists(script_path):
            self.notify("setup_models.py 未找到", title="错误", severity="error", timeout=5)
            return

        # 检查组件是否已安装
        status_lines = []
        components = {
            "omniparser": ("OmniParser UI 检测", "OmniParser/weights"),
            "qwen-vl-2b": ("Qwen2-VL-2B", "models/qwen2-vl-2b"),
            "qwen-vl-7b-mlx": ("Qwen2-VL-7B MLX", "models/qwen2-vl-7b-mlx"),
        }
        for key, (name, target) in components.items():
            full_path = os.path.join(project_root, target)
            installed = os.path.exists(full_path) and os.listdir(full_path)
            icon = "✅" if installed else "⬜"
            status_lines.append(f"  {icon} {name} {'已安装' if installed else '未安装'}")

        guide = (
            "可选模型组件下载\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            + "\n".join(status_lines) + "\n\n"
            "选择操作:\n"
            "  /setup all          下载全部组件\n"
            "  /setup omniparser   只下载 OmniParser\n"
            "  /setup qwen-vl-2b   只下载 Qwen-VL 2B\n"
            "  /setup qwen-vl-7b-mlx  只下载 MLX 模型\n"
            "  /setup status       查看安装状态"
        )
        self.notify(guide, title="模型下载", timeout=15)

    @work
    async def _run_setup_download(self, component: str):
        """在后台执行模型下载脚本"""
        import asyncio
        import os
        import sys as _sys

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script_path = os.path.join(project_root, "scripts", "setup_models.py")

        args = [_sys.executable, script_path]
        if component != "all":
            args.append(component)

        self.notify(f"开始下载: {component} ...", title="模型下载", timeout=5)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=project_root,
            )

            output_lines = []
            async for line in proc.stdout:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                output_lines.append(decoded)
                # 实时显示最后 3 行
                recent = "\n".join(output_lines[-3:])
                self.notify(f"下载中...\n{recent}", title="模型下载", timeout=30)

            await proc.wait()

            if proc.returncode == 0:
                self.notify(
                    f"✅ {component} 下载完成\n\n" + "\n".join(output_lines[-5:]),
                    title="模型下载",
                    timeout=10,
                )
            else:
                self.notify(
                    f"❌ {component} 下载失败 (exit={proc.returncode})\n\n" + "\n".join(output_lines[-5:]),
                    title="模型下载",
                    severity="error",
                    timeout=10,
                )
        except Exception as e:
            self.notify(f"下载异常: {e}", title="模型下载", severity="error", timeout=10)
