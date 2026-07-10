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
from textual.widgets import Footer, Static, TextArea
from cli_tui.widgets.approval_select import ApprovalSelect

from ..commands import find_command, is_command, get_all, Command
from ..services.api_client import APIClient
from ..services.ws_client import WSClient
from ..state import AppState

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

    PromptInput {
        min-height: 3;
        max-height: 10;
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
        Binding("ctrl+up", "history_back", "历史回退", show=False),
        Binding("ctrl+down", "history_forward", "历史前进", show=False),
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
        self._suggestion_handled_enter = False  # 建议框已处理 Enter

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

    def on_unmount(self):
        """TUI 退出时清理 aiohttp 会话，避免 'Event loop is closed' 错误"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.api.close())
        except Exception:
            pass

    def _on_command_selected(self, cmd: Command):
        """命令建议选中回调 — 执行选中的命令"""
        self._handle_command(cmd.name)

    def on_text_area_changed(self, event: TextArea.Changed):
        """输入变化时更新命令建议"""
        text = event.text_area.text
        if not self._suggestions:
            return
        if text.startswith("/") or text.startswith("!"):
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

            # 解析活跃专家/主管/大模型身份（更新状态，顶栏 Header 会显示）
            active_experts = data.get("active_experts", [])
            if active_experts:
                self.state.active_experts = active_experts
            active_supervisors = data.get("active_supervisors", [])
            if active_supervisors:
                self.state.active_supervisors = active_supervisors
            large_model = data.get("large_model", {})
            if large_model:
                self.state.large_model_identity = large_model
                # 如果大模型有激活的 skill，同步显示
                if large_model.get("active_skill"):
                    self.state.active_skill = large_model["active_skill"]
                else:
                    self.state.active_skill = ""
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
                    inp.text = val
                    last_line = val.split('\n')[-1]
                    inp.cursor_location = (val.count('\n'), len(last_line))
        except Exception as e:
            logger.debug("History back failed: %s", e)

    def action_history_forward(self):
        try:
            inp = self.query_one(PromptInput)
            if inp.has_focus:
                val = inp.history_forward()
                if val is not None:
                    inp.text = val
                    if val:
                        last_line = val.split('\n')[-1]
                        inp.cursor_location = (val.count('\n'), len(last_line))
                    else:
                        inp.cursor_location = (0, 0)
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
        inp.text = self.state.last_user_input
        # 触发提交
        self.on_prompt_input_submitted(PromptInput.Submitted(self.state.last_user_input))

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
                ml.write(f"[bold green]✅ 同意切换到 {mode} 模式[/bold green]")
            asyncio.create_task(self._set_execution_mode(mode))
            response_data = {"approved": True, "mode": mode}
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
                self._suggestion_handled_enter = True
                event.prevent_default()
        elif event.key == "tab":
            # Tab 补全：将选中的命令名填入输入框
            if self._suggestions._filtered and self._suggestions._selected_index < len(self._suggestions._filtered):
                cmd = self._suggestions._filtered[self._suggestions._selected_index]
                input_widget = self.query_one(PromptInput)
                input_widget.text = cmd.name + " "
                input_widget.cursor_location = (0, len(cmd.name) + 1)
                self._suggestions._dismiss()
            event.prevent_default()
        elif event.key == "escape":
            self._suggestions._dismiss()
            try:
                self.query_one(PromptInput).focus()
            except Exception as e:
                logger.debug("Could not refocus input after escape: %s", e)
            event.prevent_default()

    def on_prompt_input_submitted(self, event: PromptInput.Submitted):
        if self._suggestion_handled_enter:
            self._suggestion_handled_enter = False
            return
        text = event.text
        if not text:
            return

        # 审批组件可见时，文本输入作为自定义回答路由到审批组件
        if self._approval_widget and "visible" in self._approval_widget.classes:
            input_widget = self.query_one(PromptInput)
            input_widget.text = ""
            if self._approval_widget._on_confirm:
                self._approval_widget._on_confirm("custom", text)
            return

        # 关闭命令建议
        if self._suggestions:
            self._suggestions._dismiss()

        input_widget = self.query_one(PromptInput)
        input_widget.text = ""
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

        # 注入已编辑的对话历史（来自 .opencode/edits/）
        injected = ""
        try:
            from cli_tui.services.cordex_store import read_edit_history
            edits = read_edit_history(self.state.session_id)
            if edits:
                history_lines = ["【用户已修正的对话历史 — 以此为准】"]
                for e in edits:
                    idx = e.get("index", 0)
                    modified = e.get("modified", "")
                    original = e.get("original", "")
                    if modified and modified != original:
                        history_lines.append(f"  [{idx}] {modified}")
                if len(history_lines) > 1:
                    injected = "\n".join(history_lines) + "\n\n"
        except Exception:
            pass

        full_text = injected + text
        if ml:
            ml.write(f"[cyan]👤 用户[/cyan]: {full_text}")

        # 停止后台监听，避免与 process_input 的接收循环冲突
        self.ws.stop_background_listener()

        try:
            # 加载上下文和记忆（在发送前）
            await self._load_context_and_memory()

            await self.ws.process_input(
                full_text,
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
        """加载事件记忆上下文（显示为系统提示）"""
        ml = self._ml
        if not ml:
            return

        try:
            from modules.memory import EventStore
            store = EventStore.get_instance()
            total = store.count_events()
            if total > 0:
                recent = store.list_events(limit=3)
                ml.write(f"[dim]📚 事件记忆: {total} 条[/dim]")
                for ev in recent:
                    ml.write(f"  [dim]• [{ev.type}] {ev.fact[:80]}[/dim]")
        except Exception as e:
            logger.debug("Failed to load event memory (non-critical): %s", e)

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
        elif cmd.action == "session":
            parts = text.split(" ", 1)
            arg = parts[1].strip().lower() if len(parts) > 1 else ""
            if arg:
                self._show_sessions(arg)
            else:
                self.notify("加载会话列表...", timeout=1)
                self._show_sessions()
        elif cmd.action == "history":
            parts = text.split(" ", 2)
            sub = parts[1].strip().lower() if len(parts) > 1 else ""
            if sub == "edit" and len(parts) >= 3:
                try:
                    idx = int(parts[2].strip())
                    self._edit_history_entry(idx)
                except ValueError:
                    self.notify("用法: /history edit <编号>", severity="warning", timeout=2)
            else:
                self._show_history()
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
        elif cmd.action == "stop":
            self._pause_thinking()
        elif cmd.action == "mode":
            # 支持 /mode plan/edit/yolo/control (执行模式) 或 /mode skill:<skill_id> (激活技能)
            parts = text.split(" ", 1)
            toggle_value = parts[1].strip().lower() if len(parts) > 1 else None
            if toggle_value in ("plan", "edit", "yolo", "control"):
                asyncio.create_task(self._set_execution_mode(toggle_value))
            else:
                self.notify(f"用法: /mode plan/edit/yolo/control", severity="information", timeout=2)
        elif cmd.action == "config":
            # 支持 /config 查看或 /config KEY VALUE 修改
            parts = text.split(" ", 1)
            if len(parts) > 1:
                config_args = parts[1].strip()
                self._manage_config(config_args)
            else:
                self._show_config()
        elif cmd.action == "notools":
            self.state.no_tools = not self.state.no_tools
            state_str = "禁用（纯聊天）" if self.state.no_tools else "启用"
            self.notify(f"AI工具: {state_str}", timeout=2)
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
    async def _show_sessions(self, filter_name: str = ""):
        """列出会话并弹出选择器"""
        sessions = await self.api.get_sessions()
        if not sessions:
            ml = self._ml
            if ml:
                ml.add_response("当前没有可用会话。")
            return

        if filter_name:
            # 按主管名过滤 — 保持原有文本显示逻辑
            ml = self._ml
            target = None
            for s in sessions:
                if s.get("supervisor_name", "") == filter_name:
                    target = s
                    break
            if not target:
                if ml:
                    ml.add_response(f"未找到副会话「{filter_name}」")
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
            return

        # 弹出会话选择器
        try:
            from cli_tui.screens.session_picker import SessionPicker
            picker = SessionPicker(
                sessions,
                on_switch=self._switch_session,
                on_actions=self._show_session_actions,
            )
            self.app.push_screen(picker)
        except Exception as e:
            logger.error(f"会话选择器显示失败: {e}")
            # 回退文本展示
            ml = self._ml
            if not ml:
                return
            lines = ["=== 会话列表 ==="]
            for s in sessions:
                sid = s.get("session_id", "?")[:20]
                created = s.get("created_at", "")[:16]
                n = s.get("dialog_size", s.get("message_count", 0))
                marker = "★  " if s.get("is_main") else "   "
                lines.append(f"  {marker}{sid}  ({created})  {n}条消息")
            ml.add_response("\n".join(lines))

    def _show_history(self):
        """显示当前对话历史列表（本地 dialog_entries + edits）"""
        ml = self._ml
        if not ml:
            return
        entries = self.state.dialog_entries
        if not entries:
            ml.write("[dim]暂无对话历史[/dim]")
            return

        # 加载本地编辑记录
        from cli_tui.services.cordex_store import read_edit_history
        edits = read_edit_history(self.state.session_id)
        edited_indices = {e.get("index") for e in edits}

        lines = ["=== 对话历史（/history edit <n> 编辑） ==="]
        for i, e in enumerate(entries):
            tier = e.get("tier", "?")
            icon = {"user": "👤", "large": "🧠", "supervisor": "📊", "expert": "🔧"}.get(tier, "❓")
            content = e.get("content", "")[:120]
            tag = " [bold yellow]✎已编辑[/bold yellow]" if i in edited_indices else ""
            lines.append(f"  [{i}] {icon} {content}{tag}")
        ml.add_response("\n".join(lines))

    def _edit_history_entry(self, idx: int):
        """打开历史编辑器修改指定条目"""
        entries = self.state.dialog_entries
        if idx < 0 or idx >= len(entries):
            self.notify(f"编号 {idx} 超出范围 (0-{len(entries)-1})", severity="warning", timeout=2)
            return

        entry = entries[idx]
        content = entry.get("content", "")
        from cli_tui.screens.history_editor import HistoryEditor

        def on_save(index: int, new_text: str):
            # 更新内存
            self.state.dialog_entries[index]["content"] = new_text
            # 持久化到 .opencode/edits/（合并已有编辑）
            from cli_tui.services.cordex_store import write_edit_history, read_edit_history
            edits = read_edit_history(self.state.session_id)
            # 移除同 index 旧条目
            edits = [e for e in edits if e.get("index") != index]
            edits.append({
                "index": index,
                "original": content,
                "modified": new_text,
                "timestamp": time.time(),
            })
            write_edit_history(self.state.session_id, edits)
            # 显示到消息列表
            ml = self._ml
            if ml:
                ml.write(f"[bold green]✓ 第 {index} 条已编辑[/bold green]")
            self.notify(f"第 {index} 条已保存（下次对话生效）", timeout=2)

        self.app.push_screen(HistoryEditor(idx, content, on_save))

    @work
    async def _switch_session(self, target_id: str):
        """切换到指定会话"""
        if self.state.processing:
            self.notify("请先等当前处理完成或按 ESC 停止", severity="warning", timeout=3)
            return

        ml = self._ml
        if not ml:
            return

        # 断开当前连接
        self.ws.stop_background_listener()
        await self.ws.close()

        # 连接到目标会话
        ok = await self.ws.connect(session_id=target_id)
        if not ok:
            self.notify(f"切换到会话 {target_id[:12]}... 失败", severity="error", timeout=3)
            self.state.connected = False
            return

        self.state.session_id = target_id
        self.state.connected = True

        # 获取历史消息并显示
        messages = await self.api.get_session_messages(target_id, limit=50)
        ml.clear()
        if messages:
            ml.write(f"[dim]📋 会话 {target_id[:16]}... 历史 ({len(messages)} 条)[/dim]")
            for msg in messages[-20:]:
                role = msg.get("role", "system")
                content = str(msg.get("content", ""))[:200]
                ml.write(f"  [dim][{role}] {content}[/dim]")
        else:
            ml.write(f"[dim]📋 会话 {target_id[:16]}... (空)[/dim]")

        self.ws._event_callbacks.clear()
        self.ws.on_event(self._persistent_ws_callback)
        self.ws.start_background_listener()
        self.notify(f"已切换到会话 {target_id[:12]}...", severity="information", timeout=3)

    # ── 会话操作菜单 ──

    def _show_session_actions(self, session_id: str, session_title: str = ""):
        """弹出会话操作菜单"""
        from cli_tui.screens.session_action_menu import SessionActionMenu
        menu = SessionActionMenu(session_id, session_title)
        menu.set_on_action(self._handle_session_action)
        self.app.push_screen(menu)

    def _handle_session_action(self, action: str, session_id: str):
        """处理会话操作"""
        if action == "switch":
            self._switch_session(session_id)
        elif action == "delete":
            self._delete_session(session_id)
        elif action == "rollback":
            self._rollback_and_delete_session(session_id)
        elif action == "continue":
            self._switch_session(session_id)
        elif action == "fork":
            self._fork_session(session_id)

    @work
    async def _delete_session(self, session_id: str):
        """删除会话"""
        ml = self._ml
        try:
            from modules.database.session_repo import get_session_repo
            repo = get_session_repo()
            ok = repo.delete_session(session_id)
            if ok:
                if ml:
                    ml.write(f"[bold red]🗑 已删除会话 {session_id[:16]}...[/bold red]")
                self.notify(f"会话已删除", severity="information", timeout=2)
                # 如果删除的是当前会话，切换到新会话
                if session_id == self.state.session_id:
                    await self.ws.close()
                    self.state.session_id = ""
                    self.state.connected = False
                    if ml:
                        ml.write("[dim]当前会话已删除，输入消息将自动创建新会话[/dim]")
            else:
                if ml:
                    ml.write(f"[yellow]会话 {session_id[:16]}... 不存在或已删除[/yellow]")
        except Exception as e:
            logger.error(f"删除会话失败: {e}")
            self.notify(f"删除失败: {e}", severity="error", timeout=3)

    @work
    async def _rollback_and_delete_session(self, session_id: str):
        """回滚文件操作 + 删除会话（用户消息→剪贴板）"""
        ml = self._ml
        try:
            from modules.database.session_repo import get_session_repo
            from modules.cortex.file_history import get_file_history

            repo = get_session_repo()
            history = get_file_history()

            # 1. 获取用户消息
            messages = repo.get_messages(session_id, limit=200)
            user_msgs = [m["content"] for m in messages if m.get("role") == "user" and m.get("content")]
            clipboard_text = "\n".join(user_msgs)

            # 2. 通过文件历史系统回滚
            rollback_results = history.rollback_session(session_id)
            restored = sum(1 for v in rollback_results.values() if v == "restored")
            failed = sum(1 for v in rollback_results.values() if "error" in str(v))

            # 3. 删除会话和文件历史
            repo.delete_session(session_id)
            history.delete_session_history(session_id)

            # 4. 复制用户消息到剪贴板
            if clipboard_text:
                try:
                    process = await asyncio.create_subprocess_exec(
                        "pbcopy",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await process.communicate(input=clipboard_text.encode("utf-8"))
                    clipboard_msg = f"用户消息已复制到剪贴板 ({len(user_msgs)} 条)"
                except Exception:
                    clipboard_msg = "剪贴板复制失败（pbcopy 不可用）"
            else:
                clipboard_msg = "无用户消息"

            # 5. 显示结果
            if ml:
                lines = [f"[bold yellow]⏪ 回滚完成[/bold yellow]"]
                if restored > 0:
                    lines.append(f"  已恢复 {restored} 个文件" + (f"，{failed} 个失败" if failed else ""))
                else:
                    lines.append(f"  无文件需要回滚")
                lines.append(f"  {clipboard_msg}")
                lines.append(f"  [red]🗑 会话 {session_id[:16]}... 已删除[/red]")
                ml.write("\n".join(lines))
            self.notify(f"回滚完成，{clipboard_msg}", severity="information", timeout=3)

            # 如果删除的是当前会话
            if session_id == self.state.session_id:
                await self.ws.close()
                self.state.session_id = ""
                self.state.connected = False

        except Exception as e:
            logger.error(f"回滚+删除会话失败: {e}")
            self.notify(f"操作失败: {e}", severity="error", timeout=3)

    async def _rollback_file_changes(self) -> str:
        """通过 git 回滚文件变更，返回描述信息"""
        import os
        try:
            # 检查是否在 git 仓库中
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--is-inside-work-tree",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd(),
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return "非 git 仓库，跳过文件回滚"

            # 获取当前未提交的变更
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd(),
            )
            stdout, _ = await proc.communicate()
            changes = stdout.decode().strip()

            if not changes:
                return "无文件变更需要回滚"

            # 统计变更文件数
            changed_files = [line[3:] for line in changes.split("\n") if line.strip()]

            # 回滚：丢弃工作区变更
            proc = await asyncio.create_subprocess_exec(
                "git", "checkout", "--", ".",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd(),
            )
            await proc.communicate()

            # 清理未跟踪文件（可选，仅清理新增的文件）
            proc = await asyncio.create_subprocess_exec(
                "git", "clean", "-fd",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd(),
            )
            await proc.communicate()

            return f"已回滚 {len(changed_files)} 个文件的变更"

        except Exception as e:
            return f"文件回滚失败: {e}"

    @work
    async def _fork_session(self, session_id: str):
        """分叉会话到新会话"""
        ml = self._ml
        try:
            import uuid
            from modules.database.session_repo import get_session_repo
            repo = get_session_repo()

            # 获取源会话信息
            summary = repo.get_session_summary(session_id)
            source_title = summary.get("title", "") if summary else ""

            # 创建新会话
            new_id = str(uuid.uuid4())
            repo.create_session(new_id)
            if source_title:
                repo.set_session_title(new_id, f"{source_title} (分叉)")

            # 复制消息
            count = repo.copy_messages_to_session(session_id, new_id)

            if ml:
                ml.write(
                    f"[bold cyan]🔀 已分叉到新会话[/bold cyan]\n"
                    f"  源: {session_id[:16]}... ({source_title or '无标题'})\n"
                    f"  新: {new_id[:16]}... ({count} 条消息)"
                )
            self.notify(f"已分叉 {count} 条消息到新会话", severity="information", timeout=2)

            # 切换到新会话
            await self._switch_session(new_id)

        except Exception as e:
            logger.error(f"分叉会话失败: {e}")
            self.notify(f"分叉失败: {e}", severity="error", timeout=3)

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

    async def _sync_execution_mode(self):
        """从后端同步执行模式到本地状态"""
        try:
            from config.settings import settings
            self.state.execution_mode = settings.effective_execution_mode
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

    async def _set_execution_mode(self, mode: str):
        """设置执行模式（本地 + 后端）"""
        _MODE_CYCLE = ["plan", "edit", "yolo", "control"]
        if mode not in _MODE_CYCLE:
            self.notify(f"未知模式: {mode}，可选: plan/edit/yolo/control", severity="warning", timeout=3)
            return

        self.state.execution_mode = mode
        # 同步发送到后端
        try:
            await asyncio.wait_for(self.api.update_config("EXECUTION_MODE", mode), timeout=5.0)
        except asyncio.TimeoutError:
            self.notify(f"模式切换超时（后端无响应），本地已切换", severity="warning", timeout=3)
        except Exception as e:
            self.notify(f"模式切换失败: {e}", severity="error", timeout=3)
        _LABELS = {"plan": "📋 Plan (只读)", "edit": "✏️ Edit (确认)", "yolo": "🚀 YOLO (宽松)", "control": "🎛️ Control (审批)"}
        self.notify(f"✓ 执行模式: {_LABELS[mode]}", severity="information", timeout=2)

    async def action_cycle_execution_mode(self):
        """Shift+Tab 循环切换执行模式: plan → edit → yolo → control → plan"""
        _MODE_CYCLE = ["plan", "edit", "yolo", "control"]
        current = self.state.execution_mode
        idx = _MODE_CYCLE.index(current) if current in _MODE_CYCLE else 1
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        await self._set_execution_mode(next_mode)

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
                "EXECUTION_MODE",
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
                "用法: /config KEY VALUE\n示例: /config EXECUTION_MODE plan",
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


