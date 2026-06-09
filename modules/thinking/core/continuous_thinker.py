"""
连续思考器 - 纯编排层

功能：
- 让大/中/小模型进行持续循环思考
- 每次短思考结果存入记忆模块的短期记忆
- 下一轮从记忆模块获取历史记忆注入提示词
- 支持外部模块动态添加提示词
"""
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Callable, Awaitable
import asyncio
import time
import uuid
from utils.logger import setup_logger
from modules.memory.core.memory_manager import MemoryManager
from modules.memory.utils.task_notebook import TaskNotebook
from modules.thinking.context import ContextManager
from modules.thinking.context.compression import CompressionEngine
from modules.thinking.core.control_tools import (
    ThinkingControlDecision,
    ThinkingTaskContext,
)
from modules.thinking.core.delegation_port import (
    DelegationPort,
    create_delegation_port,
)
from modules.thinking.core.process_collector import (
    ThinkingProcessCollector,
    create_thinking_process_collector,
)
from modules.attention import AttentionInterface, create_attention_interface
from modules.management.core.error_bus import error_bus, ErrorContext

# 单次思考超时（秒）
SINGLE_THINK_TIMEOUT = 120.0
# 单次思考最大重试次数
MAX_THINK_RETRIES = 3


class ContinuousThinker:
    """连续思考器 - 多轮短思考循环"""
    
    def __init__(
        self,
        think_fn: Optional[Callable[[str], Awaitable[str]]] = None,
        max_rounds: int = 30,
        min_rounds: int = 1,
        interval: float = 3.0,
        memory_manager: Optional[MemoryManager] = None,
        session_id: Optional[str] = None,
        tool_validator: Optional[Callable[[Dict[str, Any], str], Awaitable[Any]]] = None,
        gcm_pool: Optional[Any] = None,
        model_id: str = "",
        tier: str = "large",
        task_context: Optional[ThinkingTaskContext] = None,
        attention: Optional[AttentionInterface] = None,
        prompt_builder: Optional[Callable[..., Any]] = None,
        process_collector: Optional[ThinkingProcessCollector] = None,
        delegation_port: Optional[DelegationPort] = None,
        blackboard: Optional[Any] = None,
        runner_ref: Optional[Any] = None,
    ):
        """
        初始化连续思考器

        Args:
            think_fn: 外部注入的思考函数 async def fn(prompt) -> str
            max_rounds: 默认最大思考轮次
            min_rounds: 最小思考轮次（前N轮模型不可主动终止，防草草结束）
            interval: 每轮思考默认间隔（秒）
            memory_manager: 记忆管理器实例
            session_id: 会话 ID，用于隔离不同用户的记忆
            tool_validator: 工具调用验证器 async def fn(tool_call, user_input) -> {"safe": bool, "reason": str}
            gcm_pool: 全局上下文池实例（可选，用于将思考结果写入全局上下文）
            model_id: 模型标识（如 large_primary, supervisor_code_001）
            tier: 模型层级 (large / supervisor / expert)
            task_context: 当前连续思考循环的任务目标和结果返回目标
            attention: 注意力决策接口，默认通过注意力模块工厂创建
            prompt_builder: 可选的外部 prompt 构建器（兼容 ModelRunner 注入）
            process_collector: 思考过程收集器，供其他模块通过抽象接口读取过程快照
            delegation_port: 委托执行抽象端口，默认使用 probe_start 适配器
        """
        self.think_fn = think_fn
        self.max_rounds = max_rounds
        self.min_rounds = min_rounds
        self.interval = interval
        self.memory = memory_manager or MemoryManager()
        # 分类外部提示词：持久引导每轮都注入，临时引导仅下一轮有效
        self._persistent_prompts: List[str] = []
        self._transient_prompts: List[str] = []
        self.logger = setup_logger("continuous_thinker")
        self._running = False
        self._session_id: str = session_id or str(uuid.uuid4())
        self.history_thoughts: List[str] = []  # 本地缓存用于去重校验
        self.notebook = TaskNotebook(self._session_id)  # 初始化记事本
        self._tool_validator = tool_validator  # 工具调用安全验证器（由输出系统注入）
        self.gcm_pool = gcm_pool  # 全局上下文池（可选注入）
        self._blackboard = blackboard  # CognitiveBlackboard
        self._runner_ref = runner_ref  # ModelRunner 引用（用于技能切换等）
        # 上下文窗口追踪
        self._context_tokens: int = 0      # 当前 prompt 估算 token 数
        self._context_window_size: int = 128000  # 窗口大小
        self._model_id = model_id  # 模型标识
        self._tier = tier  # 模型层级
        self._task_context = task_context
        self._last_control_decision: Optional[ThinkingControlDecision] = None
        self._external_prompt_builder = prompt_builder
        self._process_collector = process_collector or create_thinking_process_collector()
        self._delegation_port = delegation_port or create_delegation_port()
        self._last_process_snapshot = None
        self._pending_delegations: Dict[str, Dict[str, Any]] = {}  # 委托追踪（从 blackboard 读取或本地）
        self._last_sd_read_count: int = 0  # Blackboard dialog 读取位置
        self._consecutive_new_delegation_rounds: int = 0  # 连续新建委托轮次计数器

        # 设置记忆管理器的 session_id，实现按会话隔离
        self.memory.set_session_id(self._session_id)

        # 上下文操作系统：检索、注意力排序、prompt 构建都由 ContextManager 统一负责
        self.context_manager = ContextManager(memory_manager=self.memory)
        self.attention = attention or create_attention_interface()

        # 工具调用累计计数器 (防止无限工具调用循环)
        self._total_tool_calls_in_session: int = 0
        # ModelRunner 直通字段
        self._delegation_results: List[Dict[str, Any]] = []
        self._last_control_data: Optional[Dict[str, Any]] = None
        self._supervisor_strict_retries: int = 0

    @property
    def _get_dialog(self) -> Optional[Any]:
        """获取当前可用的 CognitiveBlackboard"""
        return self._blackboard

    def set_think_fn(self, think_fn: Callable[[str], Awaitable[str]]):
        """设置思考函数"""
        self.think_fn = think_fn
        self.logger.info("思考函数已注入")

    # ── ModelRunner 直通接口 ──

    def record_delegation(self, role: str, task: str, result: Optional[Dict[str, Any]] = None) -> None:
        import time
        # 使用 task_id 作为 key（与 _process_delegation_response 的 delegation_id 一致）
        task_id = ""
        if isinstance(result, dict):
            task_id = result.get("task_id", "") or result.get("metadata", {}).get("task_id", "")
        elif hasattr(result, "metadata") and isinstance(result.metadata, dict):
            task_id = result.metadata.get("task_id", "")
        if not task_id:
            # 兜底：用签名作为 key
            task_id = f"{role}::{task[:60]}"
        # 判断委托是否真正成功（result 对象可能有 success 字段）
        is_success = True
        error_msg = ""
        if isinstance(result, dict):
            is_success = result.get("success", True)
            error_msg = result.get("error", "")
        elif hasattr(result, "success"):
            is_success = bool(result.success)
            error_msg = getattr(result, "error", "")
        if is_success:
            if task_id not in self._pending_delegations:
                self._pending_delegations[task_id] = {"round": len(self.history_thoughts) + 1, "role": role, "task": task[:120], "status": "pending", "timestamp": time.time()}
                self._delegation_results.append({"role": role, "task": task[:120], "success": True})
                self.logger.info(f"[直通委托] 记录: role={role}, task_id={task_id}")
        else:
            self._delegation_results.append({"role": role, "task": task[:120], "success": False, "error": error_msg or "委托失败：无法找到匹配的专家角色"})
            self.logger.warning(f"[直通委托] 失败: role={role}, task={task[:60]}, error={error_msg}")

    def record_control_decision(self, data: Dict[str, Any]) -> None:
        self._last_control_data = data
        self.logger.info(f"[直通控制] continue={data.get('continue', True)}")
    
    def add_external_prompt(self, prompt: str, persistent: bool = False):
        """
        外部接口：在循环中添加提示词

        Args:
            prompt: 要添加的提示词
            persistent: True=持久引导（每轮都注入），False=临时引导（仅下一轮有效）
        """
        if persistent:
            self._persistent_prompts.append(prompt)
            self.logger.debug("持久提示词已添加: %s", prompt)
        else:
            self._transient_prompts.append(prompt)
            self.logger.debug("临时提示词已添加: %s", prompt)

    def clear_external_prompts(self):
        """清除所有外部提示词"""
        self._persistent_prompts.clear()
        self._transient_prompts.clear()
        self.logger.debug("外部提示词已清空")

    def get_external_prompts(self) -> List[str]:
        """获取所有外部提示词（供探针/外部模块读取）"""
        return self._persistent_prompts + self._transient_prompts
    
    def clear_memory(self):
        """清除短期记忆和记事本"""
        self.memory.clear_short_term()
        self.notebook.clear()
        self.logger.debug("短期记忆和记事本已清空")

    def get_process_snapshot(self):
        """返回最近一次连续思考循环的过程快照。"""
        if self._last_process_snapshot is not None:
            return self._last_process_snapshot
        return self._process_collector.snapshot

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str, n: int = 8) -> float:
        """计算两个文本的 Jaccard 相似度（基于字符 n-gram，默认 8-gram 适合中文）"""
        if len(text_a) < n or len(text_b) < n:
            return 0.0
        a_ngrams = {text_a[i:i+n] for i in range(len(text_a) - n + 1)}
        b_ngrams = {text_b[i:i+n] for i in range(len(text_b) - n + 1)}
        intersection = a_ngrams & b_ngrams
        union = a_ngrams | b_ngrams
        if not union:
            return 0.0
        return len(intersection) / len(union)

    @staticmethod
    def _strip_control_markers(text: str) -> str:
        """剥离内部控制标记，防止泄露到用户界面"""
        import re
        # 清理多余空行
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _sanitize_final_context_text(self, text: str, limit: int = 4000) -> str:
        """构建最终整合可见上下文时的安全清洗。"""
        import re
        cleaned = self._strip_control_markers(str(text or ""))
        blocked_patterns = (
            r'probe_start\([^\n]*',
            r'probe_started[^\n]*',
            r'MessageBus[^\n]*',
            r'SharedDialog[^\n]*',
            r'continue_thinking[^\n]*',
            r'delegate_task[^\n]*',
        )
        for pattern in blocked_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        return cleaned

    def _collect_final_synthesis_context(
        self,
        question: str,
        results: List[Dict[str, Any]],
    ) -> str:
        """收集本模型可见的安全上下文，用于最终整合。"""
        parts: List[str] = []

        recent_steps = []
        for item in results[-6:]:
            if not isinstance(item, dict):
                continue
            content = self._sanitize_final_context_text(item.get("thought", ""), limit=700)
            if content:
                recent_steps.append(content)
        if recent_steps:
            parts.append("【本次内部思考摘要】\n" + "\n---\n".join(recent_steps))

        expert_context = self._sanitize_final_context_text(
            self._build_expert_context_section(),
            limit=2500,
        )
        if expert_context:
            parts.append("【本模型可见的新上下文/专家回复】\n" + expert_context)

        delegation_status = self._sanitize_final_context_text(
            self._build_delegation_status_section(),
            limit=1200,
        )
        if delegation_status:
            parts.append("【委托状态摘要】\n" + delegation_status)

        notebook_status = self._sanitize_final_context_text(
            self.notebook.get_status(),
            limit=1200,
        )
        if notebook_status:
            parts.append("【记事本状态】\n" + notebook_status)

        return "\n\n".join(parts)

    def _build_task_contract_section(self, task_context: Optional[ThinkingTaskContext]) -> str:
        """构建本次连续思考循环的任务契约。"""
        ctx = task_context or self._task_context
        if not ctx:
            return ""

        # 根据 tier 输出不同的控制工具指令
        if self._tier == "supervisor":
            control_hint = (
                "⚠️ 【硬性规则】你是主管，必须通过工具调用来控制和委托，不能输出纯文本分析！\n"
                "- 分析完毕后，【必须】使用 delegate_task 工具向专家委托任务。\n"
                "- 禁止输出'我需要...'、'应该...'等自然语言分析；系统只接收工具调用。\n"
                "- 你的每一轮输出都会被检查：如果没有工具调用，会被拒绝并要求重新尝试。\n"
                "- 整合阶段：使用 continue_thinking 并设置 continue=false 来结束循环。"
            )
        elif self._tier == "expert":
            control_hint = (
                "- 完成工作后使用内部工具输出 result_summary。\n"
                "- 不要把控制标记写进自然语言回复。"
            )
        else:  # large
            control_hint = (
                "- 使用 continue_thinking 工具控制循环继续/结束/等待。\n"
                "- 使用 delegate_task 工具向主管委托任务（主管负责调度专家）。\n"
                "- 不要把内部工具名、委托协议、控制标记写进自然语言回复。"
            )

        return (
            "【本次连续思考循环契约】\n"
            f"- 任务ID：{ctx.task_id}\n"
            f"- 循环目标：{ctx.loop_goal}\n"
            f"- 发起模型：{ctx.origin_model_id or 'unknown'}\n"
            f"- 结果返回给：{ctx.return_to_model_id or '用户/主流程'}\n"
            + control_hint
        )

    async def _notify_return_target(
        self,
        task_context: Optional[ThinkingTaskContext],
        final_result: str,
    ) -> None:
        """思考循环结束后，把结果返回给应该继续任务的模型。"""
        ctx = task_context or self._task_context
        if not ctx or not ctx.return_to_model_id or not final_result.strip():
            return
        if ctx.return_to_model_id == self._model_id:
            return
        # 有待处理委托时不发送唤醒 — 主管/大模型需要先收集完专家结果再回报
        real_pending = {
            k: v for k, v in self._pending_delegations.items()
            if k != "natural_delegation" and v.get("status") == "pending"
        }
        if real_pending:
            self.logger.debug(
                f"[思考结果回传] 跳过：仍有 {len(real_pending)} 个待处理委托"
            )
            return
        try:
            from modules.thinking.communication.interface import (
                Message,
                MessageType,
                get_message_bus_port,
            )

            bus = get_message_bus_port()
            await bus.send(Message(
                msg_type=MessageType.SYSTEM,
                sender=self._model_id or ctx.origin_model_id or "continuous_thinker",
                recipient=ctx.return_to_model_id,
                content={
                    "action": "thinking_result",
                    "task_id": ctx.task_id,
                    "delegation_id": ctx.task_id,
                    "from_model_id": self._model_id or ctx.origin_model_id,
                    "to_model_id": ctx.return_to_model_id,
                    "source_model_id": self._model_id or ctx.origin_model_id,
                    "source_tier": self._tier,
                    "source_role": ctx.metadata.get("identity_key", "") if ctx else "",
                    "loop_goal": ctx.loop_goal,
                    "result": final_result,
                    "session_id": ctx.return_to_session_id or self._session_id,
                    "caller_tier": ctx.caller_tier,
                },
            ))
            self.logger.info(
                f"[思考结果回传] {self._model_id} → {ctx.return_to_model_id} "
                f"task={ctx.task_id}"
            )
        except Exception as e:
            self.logger.debug(f"[思考结果回传] 失败 (非致命): {e}")

    def _select_final_result(
        self,
        results: List[Dict[str, Any]],
        control_decision: Optional[ThinkingControlDecision] = None,
    ) -> str:
        """选择最终结果。

        优先返回整合后的 final_output；若没有（如模型调用失败），
        回退到最后一轮 raw thought，确保错误信息仍能回传。
        """
        for item in reversed(results):
            final_output = str(item.get("final_output", "") or "").strip()
            if final_output:
                return self._strip_control_markers(final_output)
        # 后备：无 final_output 时取最后一条 raw thought
        for item in reversed(results):
            thought = str(item.get("thought", "") or "").strip()
            if thought:
                return self._strip_control_markers(thought)
        return ""

    def _has_successful_external_result(self, text: str) -> bool:
        """判断安全上下文中是否存在真实外部工具/专家结果。"""
        markers = (
            "【委托工具执行结果:",
            "专家已执行完成",
            "【工具结果】",
            "status': 'success'",
            '"status": "success"',
            "'success': True",
            '"success": true',
        )
        lowered = str(text or "").lower()
        return any(marker.lower() in lowered for marker in markers)

    def _build_final_synthesis_prompt(
        self,
        question: str,
        results: List[Dict[str, Any]],
        control_decision: Optional[ThinkingControlDecision] = None,
    ) -> str:
        """构建最终总结 prompt，不回溯原始思考过程，避免暴露内部实现。"""
        ctx = self._task_context
        result_summary = control_decision.result_summary if control_decision else ""
        safe_context = self._collect_final_synthesis_context(question, results)
        has_external_result = self._has_successful_external_result(safe_context)
        evidence_rule = (
            "5. 如果任务需要读取桌面、文件、网页、系统状态或其他外部事实，但安全上下文中没有真实成功的工具/专家结果，"
            "必须明确说明尚未获取到结果或工具调用失败，不得编造软件列表、文件列表、网页内容或观察结论。\n"
            if not has_external_result else
            "5. 只能基于安全上下文中的真实工具/专家结果作答，不得补全或编造未出现的项目。\n"
        )
        return (
            "你正在进行一次单模型内部 ReAct 思考的最终输出整理。请基于任务目标、候选结论和安全上下文，生成一次干净、完整、面向接收方的最终结果。\n"
            "要求：\n"
            "1. 不要暴露内部实现、工具调用协议、探针、MessageBus、Blackboard、continue_thinking、delegate_task 等内部细节。\n"
            "2. 不要原样输出委托指令、工具失败日志或中间思考过程。\n"
            "3. 只输出最终可交付结果；不要解释你如何思考、如何调用工具或如何组织多模型协作。\n"
            "4. 如果结果返回给其他模型，输出应帮助该模型继续执行任务；如果返回给用户，输出应直接可读。\n"
            f"{evidence_rule}\n"
            f"原始任务：{question}\n"
            f"本轮目标：{ctx.loop_goal if ctx else question}\n"
            f"结果接收方：{ctx.return_to_model_id if ctx and ctx.return_to_model_id else '用户/主流程'}\n"
            f"候选结论：{result_summary}\n\n"
            f"安全上下文：\n{safe_context}\n\n"
            "请输出最终结果："
        )

    async def _run_final_synthesis(
        self,
        question: str,
        results: List[Dict[str, Any]],
        control_decision: Optional[ThinkingControlDecision] = None,
    ) -> Optional[Dict[str, Any]]:
        """连续思考循环结束后进行一次最终整合，净化输出并生成唯一可发布结果。"""
        if not self.think_fn:
            return None
        prompt = self._build_final_synthesis_prompt(question, results, control_decision)
        try:
            start_time = time.time()
            final_text = await self.think_fn(prompt)
            duration_ms = (time.time() - start_time) * 1000
            final_text = self._strip_control_markers(str(final_text or ""))
            record = {
                "thought": final_text,
                "final_output": final_text,
                "timestamp": time.time(),
                "duration_ms": duration_ms,
                "is_final_synthesis": True,
            }
            if final_text:
                self.history_thoughts.append(final_text)
                self.notebook.update(new_content=final_text, is_finished=True)
                if self._get_dialog and self._model_id:
                    try:
                        self._get_dialog.write_thought(
                            model_id=self._model_id,
                            tier=self._tier,
                            content=final_text,
                            round_num=len(self.history_thoughts),
                            metadata={"phase": "final_synthesis"},
                        )
                    except TypeError:
                        self._get_dialog.write_thought(
                            model_id=self._model_id,
                            tier=self._tier,
                            content=final_text,
                            round_num=len(self.history_thoughts),
                        )
                    except Exception as e:
                        self.logger.debug(f"[Blackboard] 最终总结写入失败 (非致命): {e}")
                self._process_collector.record_step(
                    round_num=len(results) + 1,
                    content=final_text,
                    duration_ms=duration_ms,
                    metadata={"phase": "final_synthesis"},
                )
            self.logger.info("最终总结完成，耗时: %.0fms", duration_ms)
            return record
        except Exception as e:
            self.logger.warning(f"最终总结失败，未发布原始思考过程: {e}")
            return None

    async def _finalize_thinking_results(
        self,
        question: str,
        results: List[Dict[str, Any]],
        error: Optional[Exception] = None,
    ) -> str:
        """统一完成最终整合、过程快照和结果回传。"""
        _real_pending = {k: v for k, v in self._pending_delegations.items() if k != "natural_delegation" and v.get("status") == "pending"}
        if self._tier == "large" and _real_pending:
            self.logger.info("[最终整合] 大模型有 %d 个待处理委托，跳过最终合成", len(_real_pending))
            final_synthesis = None
        elif self._last_control_decision and self._last_control_decision.result_summary:
            # 模型已有最终结果（通过 continue_thinking(result_summary=...) 或强制终止注入），跳过冗余总结
            self.logger.info("[最终整合] 模型已提供 result_summary，跳过最终合成")
            final_synthesis = {
                "thought": self._last_control_decision.result_summary,
                "final_output": self._last_control_decision.result_summary,
                "timestamp": time.time(),
                "duration_ms": 0,
                "is_final_synthesis": True,
            }
            results.append(final_synthesis)
        else:
            # 没有 pending 也没有 result_summary → 无可用结果，不生成虚假总结
            final_synthesis = None
        if final_synthesis:
            results.append(final_synthesis)

        final_result = self._select_final_result(results, self._last_control_decision)
        metadata = {
            "rounds": len(results),
            "has_final_synthesis": final_synthesis is not None,
        }
        if error is not None:
            metadata["error"] = str(error)
        self._last_process_snapshot = self._process_collector.complete(
            final_result=final_result,
            control_decision=self._last_control_decision,
            metadata=metadata,
        )
        await self._notify_return_target(self._task_context, final_result)
        return final_result

    def _parse_wait_seconds(self, thought: str) -> float:
        """
        从思考结果中解析模型自主决定的等待秒数
        未指定时返回默认 interval

        Returns:
            等待秒数（限制在 0.5 ~ 300 秒之间）
        """
        return self.interval
    
    def _build_tool_prompt_section(self) -> str:
        """构建思考控制工具说明（tier-aware）。"""
        tc = self._task_context
        goal = tc.loop_goal if tc else ""

        if self._tier == "supervisor":
            return (
                "## 可用工具\n"
                "- delegate_task: 向专家委托任务\n"
                "- continue_thinking: 继续/结束思考循环\n"
                "三阶段：1.目标分析 → 2.规划与委托 → 3.等待整合"
            )
        elif self._tier == "expert":
            return (
                "## 可用工具\n"
                "- continue_thinking: 继续/结束思考循环\n"
                "完成工作后使用 continue_thinking(continue=false) 输出 result_summary。"
            )
        else:  # large
            return (
                "## 可用工具\n"
                "- delegate_task: 【关键】向主管委托任务。所有需要查询、搜索、文件操作等具身任务都必须通过 delegate_task 委托，不能自己用 probe_start。\n"
                "- continue_thinking: 继续/结束思考循环\n"
                "- respond_to_user: 向用户输出最终回复\n"
                "- request_skill: 请求激活技能（按角色、规章、流程执行任务）\n"
                "- list_skills: 列出所有可用技能\n"
                                f"当前任务：{goal}"
            )

    async def _build_prompt(self, initial_question: str, round_num: int = 0) -> str:
        """构建当前轮 prompt — 委托 ContextManager 统一管理上下文。"""
        external_guidance = self._consume_external_guidance()
        notebook_status = self.notebook.get_status()
        # 记事本内容为默认值时（未实际更新），不注入 prompt，防止未完成草稿回灌
        _default_notebook = "任务刚开始，请制定初步计划。"
        if self.notebook.content.strip() == _default_notebook:
            notebook_status = ""
        current_state = {
            "notebook_status": notebook_status,
            "history_output": "\n".join(self.history_thoughts[:-1]) if len(self.history_thoughts) > 1 else "",
            "available_tools": self._build_tool_prompt_section(),
            "expert_context": self._build_expert_context_section(),
            "delegation_status": self._build_delegation_status_section(),
            "external_guidance": external_guidance,
            "tier": self._tier,
            "has_skill": bool(getattr(self._runner_ref, '_active_skill', None)),
        }

        attention_level = 0.6
        try:
            short_memories = []
            try:
                short_items = self.memory.get_context(limit=20)
                short_memories = [
                    str(item.get("text", ""))
                    for item in short_items
                    if isinstance(item, dict) and item.get("text")
                ]
            except Exception:
                short_memories = []

            attention_decision = self.attention.analyze(
                user_input=initial_question,
                context=[],
                short_term_memory=short_memories,
            )
            attention_level = getattr(attention_decision, "attention_level", 0.6)
        except Exception as e:
            self.logger.debug(f"[Attention] 动态注意力计算失败，回退默认值: {e}")
            attention_level = 0.6

        prompt = await self.context_manager.build_prompt(
            current_goal=initial_question,
            current_state=current_state,
            attention_level=attention_level,
        )

        prompt = await self._compress_prompt_if_needed(prompt)

        # 调用外部 prompt builder（如果存在），注入 ModelRunner 的上下文（如消息检查）
        if self._external_prompt_builder is not None:
            try:
                import inspect
                if inspect.iscoroutinefunction(self._external_prompt_builder):
                    external_section = await self._external_prompt_builder(round_num=round_num)
                else:
                    external_section = self._external_prompt_builder(round_num=round_num)
                if external_section and str(external_section).strip():
                    prompt = prompt + "\n\n" + str(external_section).strip()
            except Exception as e:
                self.logger.debug(f"[_build_prompt] 外部 prompt builder 调用失败: {e}")

        if self._tier == "supervisor":
            phases = {
                1: "【阶段：目标分析】理解任务需求，明确目标范围和约束条件。仅分析，不执行任何操作。",
                2: "【阶段：规划与委托】制定执行计划，识别需要的专家角色，然后用 delegate_task 委托给对应专家。",
                3: "【阶段：等待结果】已委托任务，使用 continue_thinking(continue=false) 结束当前思考循环。系统自动等待专家结果后唤醒你。",
            }
            phase = phases.get(round_num)
            if phase:
                prompt = prompt + "\n\n" + phase

        return prompt

    def _consume_external_guidance(self) -> str:
        """消费外部引导文本。委托 ContextManager。"""
        from modules.thinking.context.manager import ContextManager
        result = ContextManager.build_external_guidance(
            persistent_prompts=self._persistent_prompts,
            transient_prompts=self._transient_prompts,
        )
        self._transient_prompts.clear()
        return result

    async def _compress_prompt_if_needed(self, prompt: str) -> str:
        """水位线上下文压缩检查 — 超出上下文窗口时压缩到指定比例。"""
        if not prompt:
            return prompt

        try:
            from config.settings import settings
            window_size = settings.CONTEXT_WINDOW_SIZE
            compress_ratio = settings.CONTEXT_COMPRESS_RATIO
        except Exception:
            window_size = 128000
            compress_ratio = 0.2

        self._context_window_size = window_size

        try:
            from modules.thinking.context.compression import get_compression_engine
            engine = get_compression_engine()
            estimated_tokens = engine.estimate_tokens(prompt)
            self._context_tokens = estimated_tokens

            # 未超出窗口，不压缩
            if estimated_tokens <= window_size:
                return prompt
            # 超出窗口，压缩到窗口的 compress_ratio
            target_tokens = int(window_size * compress_ratio)
            self.logger.info(
                f"[压缩] prompt ~{estimated_tokens} tokens > 窗口 {window_size}，"
                f"压缩到 {target_tokens} tokens ({compress_ratio:.0%})"
            )
            compressed = await engine.compress(prompt, max_tokens=target_tokens)
            self._context_tokens = engine.estimate_tokens(compressed)
            return compressed
        except Exception as e:
            self.logger.debug(f"[压缩] 上下文压缩失败（非致命）: {e}")
            return prompt

    def _build_expert_context_section(self) -> str:
        """构建可用主管和专家上下文（大模型关键信息）"""
        if self._tier != "large":
            return ""  # 只给大模型提供这些信息

        return """## 可用主管（delegate_task 的 role 参数）

| role 参数 | 主管名称 | 专长 |
|-----------|---------|------|
| code_supervisor | 代码主管 | 代码审查、实现、测试、架构设计 |
| query_supervisor | 查询主管 | 文件操作、数据查询、信息检索 |
| creative_supervisor | 创意主管 | 内容生成、文案、方案设计 |

## 可用专家（主管通过 delegate_task 调度）

| role 参数 | 专家名称 | 专长 |
|-----------|---------|------|
| code_reviewer | 审查专家 | 代码审查、安全审计 |
| code_writer | 实现专家 | 代码实现、算法设计 |
| test_writer | 测试专家 | 测试编写、边界分析 |
| data_analyzer | 分析专家 | 数据分析、文件查询 |
| memory_manager | 记忆管理员 | 记忆操作 |
| emotion | 情绪分析师 | 情绪识别、共情 |
| customer | 客户 | 用户交互 |

## 工作流
1. 分析任务，判断是否需要委托（简单问题自己回答）
2. 用 `delegate_task(role="...", task="...")` 委托给主管
3. 主管会按"分析→规划与委托→等待整合"三阶段执行
4. 等待主管唤醒，收到结果后整理并回复用户"""

    def _build_delegation_status_section(self) -> str:
        """构建委托状态摘要。委托 ContextManager。"""
        from modules.thinking.context.manager import ContextManager
        return ContextManager.build_delegation_status(self._pending_delegations)

    def _process_delegation_response(
        self,
        response_msg: str,
        delegation_id: str = "",
    ) -> None:
        """处理来自专家的委托回复，更新本地状态

        当专家完成任务并返回结果时调用，用于标记委托已完成。
        """
        if not delegation_id:
            self.logger.warning("[委托响应处理] 缺少 delegation_id，无法更新委托状态")
            return

        try:
            # 更新本地 pending_delegations 状态（这是实际生效的追踪机制）
            if delegation_id in self._pending_delegations:
                self._pending_delegations[delegation_id]["status"] = "completed"
                self._pending_delegations[delegation_id]["reply_time"] = (
                    __import__("time").time()
                )
                self._pending_delegations[delegation_id]["response"] = (
                    response_msg[:500]
                )
                self.logger.info(
                    f"[委托响应处理] 本地记录已更新: {delegation_id} → completed"
                )
            else:
                self.logger.debug(
                    f"[委托响应处理] 未在本地找到委托记录: {delegation_id}（可能已完成或超时）"
                )

            # 尝试更新黑板（如果存在且能匹配）
            if self._blackboard:
                try:
                    self._blackboard.update_delegation_status(
                        delegation_id=delegation_id,
                        status="completed",
                        metadata={"response": response_msg[:1000]},
                    )
                except Exception as e:
                    self.logger.debug(f"[Blackboard] 委托状态更新失败 (非致命，本地追踪已更新): {e}")
        except Exception as e:
            self.logger.warning(
                f"[委托响应处理] 更新委托状态异常 ({delegation_id}): {e}"
            )

    def _update_delegation_status_from_sd(self, round_num: int = 0) -> None:
        """【已废弃】在新架构中由 CognitiveBlackboard.update_delegation_status 替代

        保留方法以保证兼容性，但直接返回（不做任何事）。
        所有委托状态更新工作现由 CognitiveBlackboard 负责。
        """
        return

    def _normalize_think_result(self, result: Any) -> Dict[str, Any]:
        """确保单轮思考结果始终是 dict，避免下游 `.get` 崩溃。"""
        if isinstance(result, dict):
            return result
        if result is None:
            return {
                "thought": "",
                "duration_ms": 0,
                "error": "think_once returned None",
                "is_finished": True,
            }
        return {
            "thought": str(result),
            "duration_ms": 0,
        }

    async def think_once(self, context: str, initial_question: str = "") -> Dict[str, Any]:
        """
        单次思考

        Args:
            context: 思考上下文
            initial_question: 初始问题

        Returns:
            思考结果
        """
        if not self.think_fn:
            self.logger.warning("未设置思考函数")
            return {
                "thought": "",
                "duration_ms": 0,
                "error": "思考函数未配置"
            }

        thought = ""
        duration_ms = 0.0
        for attempt in range(1, MAX_THINK_RETRIES + 1):
            try:
                start_time = time.time()
                raw_thought = await asyncio.wait_for(self.think_fn(context), timeout=SINGLE_THINK_TIMEOUT)
                duration_ms = (time.time() - start_time) * 1000
                thought = str(raw_thought or "")
                break
            except asyncio.TimeoutError:
                if attempt == MAX_THINK_RETRIES:
                    self.logger.warning(
                        f"单次思考超时（>{SINGLE_THINK_TIMEOUT}s），已达最大重试次数 {MAX_THINK_RETRIES}"
                    )
                    error_bus.report_error(
                        asyncio.TimeoutError(
                            f"单次思考超时（>{SINGLE_THINK_TIMEOUT}s），已达最大重试次数 {MAX_THINK_RETRIES}"
                        ),
                        ErrorContext(
                            module="continuous_thinker",
                            function="think_once",
                            extra={"context": context[:100] if context else None, "attempt": attempt}
                        )
                    )
                    # 持久化超时结果 — 让下一轮模型能看到发生了什么
                    timeout_thought = "[思考超时]"
                    self.history_thoughts.append(timeout_thought)
                    if self._get_dialog and self._model_id:
                        try:
                            self._get_dialog.write_thought(
                                model_id=self._model_id,
                                tier=self._tier,
                                content=timeout_thought,
                                round_num=len(self.history_thoughts),
                            )
                        except Exception as e:
                            self.logger.debug(f"[Blackboard] 超时记录写入失败 (非致命): {e}")
                    return {
                        "thought": timeout_thought,
                        "duration_ms": SINGLE_THINK_TIMEOUT * 1000,
                        "error": f"单次思考超时（>{SINGLE_THINK_TIMEOUT}s，已达最大重试次数 {MAX_THINK_RETRIES}）",
                        "is_finished": True,
                    }
                self.logger.warning(
                    f"单次思考超时（>{SINGLE_THINK_TIMEOUT}s），第 {attempt} 次重试..."
                )
                error_bus.report_error(
                    asyncio.TimeoutError(f"单次思考超时（>{SINGLE_THINK_TIMEOUT}s），第 {attempt} 次"),
                    ErrorContext(
                        module="continuous_thinker",
                        function="think_once",
                        extra={"context": context[:100] if context else None, "attempt": attempt}
                    )
                )
                continue
            except Exception as e:
                self.logger.warning(f"单次思考发生异常: {e}")
                error_bus.report_error(
                    e,
                    ErrorContext(
                        module="continuous_thinker",
                        function="think_once",
                        extra={"context": context[:100] if context else None}
                    )
                )
                # 持久化异常结果 — 让下一轮模型能看到发生了什么
                error_thought = f"[思考异常: {str(e)[:200]}]"
                self.history_thoughts.append(error_thought)
                if self._get_dialog and self._model_id:
                    try:
                        self._get_dialog.write_thought(
                            model_id=self._model_id,
                            tier=self._tier,
                            content=error_thought,
                            round_num=len(self.history_thoughts),
                        )
                    except Exception as write_err:
                        self.logger.debug(f"[Blackboard] 异常记录写入失败 (非致命): {write_err}")
                return {
                    "thought": error_thought,
                    "duration_ms": 0,
                    "error": f"思考异常: {str(e)}",
                    "is_finished": True,
                }

        # === GCM: 同步思考输出到全局上下文池 ===
        if self.gcm_pool:
            try:
                from modules.thinking.context.wire import sync_model_call
                sync_model_call(
                    self.gcm_pool,
                    "continuous_thinker",
                    thought,
                    metadata={
                        "duration_ms": duration_ms,
                        "question": initial_question[:100],
                    },
                    importance=0.5,
                )
            except Exception as e:
                self.logger.debug(f"[GCM] 思考同步失败: {e}")

        record = {
            "thought": thought,
            "timestamp": time.time(),
            "duration_ms": duration_ms,
        }

        self.memory.set_working_memory(f"last_thought_{int(time.time())}", record, ttl=300)
        self.history_thoughts.append(thought)

        if self._get_dialog and self._model_id:
            try:
                clean_content = self._strip_control_markers(thought)
                self._get_dialog.write_thought(
                    model_id=self._model_id,
                    tier=self._tier,
                    content=clean_content,
                    round_num=len(self.history_thoughts),
                )
            except Exception as e:
                self.logger.debug(f"[Blackboard] 写入失败 (非致命): {e}")

        self.logger.debug(
            "思考完成，耗时: %.0fms, 长度: %d字符",
            duration_ms, len(thought)
        )
        return record

    async def think(self, question: str) -> Dict[str, Any]:
        """
        单次思考 - 便捷入口
        
        Args:
            question: 用户问题
             
        Returns:
            思考结果字典
        """
        if not self.think_fn:
            try:
                from modules.thinking.core.model_manager import model_manager
                model = model_manager.big_model
                if not model:
                    raise ValueError("大模型未初始化")
                self.set_think_fn(model.generate)
            except Exception as e:
                self.logger.warning(f"模型初始化失败: {e}，返回占位符")
                error_bus.report_error(
                    e,
                    ErrorContext(
                        module="continuous_thinker",
                        function="continuous_think",
                        extra={"question": question[:50] if question else None}
                    )
                )
                return {
                    "thought": f"处理: {question[:50]}...",
                    "duration_ms": 0,
                    "is_finished": True
                }
        
        result = await self.think_once(question, question)
        return result
    
    def deep_think(self, question: str, max_rounds: int = 10, min_rounds: int = 1) -> List[Dict[str, Any]]:
        """同步版本的深度思考 — 安全兼容事件循环内/外调用"""
        import concurrent.futures
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.continuous_think(question, max_rounds, min_rounds))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    self.continuous_think(question, max_rounds, min_rounds)
                ).result()
    
    async def continuous_think(
        self,
        question: str,
        max_rounds: Optional[int] = None,
        min_rounds: Optional[int] = None,
        callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        task_context: Optional[ThinkingTaskContext] = None,
    ) -> List[Dict[str, Any]]:
        """
        连续思考循环

        Args:
            question: 初始问题
            max_rounds: 最大轮次（覆盖默认值）
            min_rounds: 最小轮次（覆盖默认值，前N轮不可主动终止）
            callback: 每轮思考后的回调函数 async def fn(result)
            task_context: 当前循环的目标、发起模型和结果返回目标

        Returns:
            所有思考记录列表
        """
        if not self.think_fn:
            self.logger.warning("未设置思考函数")
            return []

        rounds = max_rounds if max_rounds is not None else self.max_rounds
        self.logger.info(f"[DEBUG] continuous_think: question={str(question)!r}, rounds={rounds}, think_fn={self.think_fn is not None}")
        min_rounds_required = min_rounds if min_rounds is not None else 1
        results = []
        self._running = True
        previous_task_context = self._task_context
        if task_context is not None:
            self._task_context = task_context
        self._last_control_decision = None

        # 每次连续思考开始时重置委托追踪状态
        self._pending_delegations.clear()
        self._consecutive_new_delegation_rounds = 0
        self._last_sd_read_count = 0
        self._delegation_results.clear()
        self._last_control_data = None
        self._supervisor_strict_retries = 0
        self._process_collector.reset(
            session_id=self._session_id,
            model_id=self._model_id,
            tier=self._tier,
            task_context=self._task_context,
        )

        try:
            self.logger.info(
                "会话[%s]开始思考：%s (最少%d轮, 最多%d轮)",
                self._session_id, question[:50], min_rounds_required, rounds
            )

            for i in range(rounds):
                if not self._running:
                    self.logger.info("思考被中断")
                    break

                # 构建提示词（包含历史记忆和外部提示）
                prompt = await self._build_prompt(question, round_num=i + 1)

                # 执行思考
                result = self._normalize_think_result(await self.think_once(prompt, question))
                results.append(result)

                current_thought = str(result.get("thought", "") or "").strip()
                # ── 从 _last_control_data 构建 control_decision（ModelRunner 直通）──
                control_decision = None
                if self._last_control_data is not None:
                    control_decision = ThinkingControlDecision.from_payload(self._last_control_data)
                    self._last_control_decision = control_decision
                    self._last_control_data = None
                elif self._last_control_decision and self._last_control_decision.should_continue:
                    control_decision = self._last_control_decision
                round_num = i + 1

                # ── 从 _delegation_results 检测委托（ModelRunner 直通）──
                has_new_delegation = bool(self._delegation_results)
                has_delegation = has_new_delegation
                if has_new_delegation:
                    self.logger.info(f"第{round_num}轮：检测到 {len(self._delegation_results)} 个委托（直通），停止循环等待结果")
                    self._delegation_results.clear()
                    # 委托后结束思考循环，不再继续轮询
                    self._last_control_decision = ThinkingControlDecision(
                        should_continue=False,
                        reason="已委托任务给专家/主管，等待异步结果返回",
                    )
                    self._last_control_data = None
                    break

                # 记录本轮思考步骤
                cleaned_for_notebook = self._strip_control_markers(current_thought)
                self._process_collector.record_step(
                    round_num=round_num,
                    content=cleaned_for_notebook,
                    duration_ms=float(result.get("duration_ms", 0) or 0),
                    metadata={"has_control_decision": control_decision is not None},
                )
                notebook_is_finished = bool(control_decision and not control_decision.should_continue)
                self.notebook.update(new_content=cleaned_for_notebook, is_finished=notebook_is_finished)

                # 更新委托计数器
                if has_delegation:
                    if has_new_delegation:
                        self._consecutive_new_delegation_rounds += 1
                    else:
                        self._consecutive_new_delegation_rounds = 0
                else:
                    self._consecutive_new_delegation_rounds = 0

                # 控制决策终止（continue_thinking(false)）
                if control_decision is not None and not control_decision.should_continue:
                    self.logger.info(f"第{round_num}轮：continue_thinking 终止")
                    break

                # 文本 fallback：模型输出 JSON 形式 continue_thinking（主管/专家无原生工具支持时）
                if '\"continue\": false' in current_thought or '\"continue\":false' in current_thought:
                    self.logger.info(f"第{round_num}轮：文本 continue_thinking(false) 终止")
                    break

                # 调用回调
                if callback:
                    try:
                        await callback(result)
                    except Exception as e:
                        self.logger.error("回调执行失败: %s", e)

                # 等待秒数由内部控制工具决定；文本标记只作为旧格式兼容
                wait_seconds = self.interval
                if control_decision and control_decision.wait_seconds is not None:
                    wait_seconds = float(control_decision.wait_seconds)
                    self.logger.info(
                        f"第{round_num}轮：continue_thinking 决定等待 {wait_seconds}s"
                    )
                elif self._pending_delegations:
                    # 有待处理委托时，自动延长等待（让专家有时间完成）
                    wait_seconds = max(wait_seconds, 8.0)
                else:
                    wait_seconds = self._parse_wait_seconds(current_thought)

                # 智能等待：检测到委托但工具未给等待时间时自动延长
                if has_delegation and wait_seconds <= self.interval:
                    wait_seconds = max(wait_seconds, 8.0)
                    self.logger.info(
                        f"第{round_num}轮：检测到委托，自动延长等待至 {wait_seconds}s"
                    )

                # 检测重复思考（与上一轮内容相似）→ 延长等待
                if len(results) >= 2:
                    prev_thought = str(results[-2].get("thought", "") or "").strip()
                    if prev_thought and current_thought:
                        similarity = self._jaccard_similarity(prev_thought, current_thought)
                        avg_len = (len(prev_thought) + len(current_thought)) / 2
                        dup_threshold = 0.30 if avg_len < 80 else (0.40 if avg_len < 200 else 0.50)
                        if similarity > dup_threshold:
                            wait_seconds = max(wait_seconds, 15.0)
                            self.logger.info(
                                f"第{round_num}轮：检测到重复思考 (sim={similarity:.2f}, 阈值={dup_threshold})，"
                                f"延长等待至 {wait_seconds}s 避免空转"
                            )

                # 等待间隔
                if i < rounds - 1 and self._running:
                    await asyncio.sleep(wait_seconds)
            
            self.logger.info(
                "连续思考完成，共%d轮，总耗时: %.2fs",
                len(results),
                sum(float(r.get("duration_ms", 0) or 0) for r in results) / 1000
            )

            await self._finalize_thinking_results(question, results)
            if task_context is not None:
                self._task_context = previous_task_context

            self._running = False
            return results

        except Exception as e:
            self.logger.error("连续思考失败: %s", e)
            try:
                await self._finalize_thinking_results(question, results, error=e)
            except Exception as finalize_error:
                self.logger.error("连续思考异常后的最终整合也失败: %s", finalize_error)
                final_result = self._select_final_result(results, self._last_control_decision)
                self._last_process_snapshot = self._process_collector.complete(
                    final_result=final_result,
                    control_decision=self._last_control_decision,
                    metadata={"rounds": len(results), "error": str(e), "finalize_error": str(finalize_error)},
                )
                await self._notify_return_target(self._task_context, final_result)
            if task_context is not None:
                self._task_context = previous_task_context
        self._running = False
        return results

    def reset_for_continuation(self) -> None:
        """重置状态用于被唤醒后的继续（保留记忆和委托状态）

        在总指挥被主管回复唤醒时调用，用于重新启动 continuous_think 循环。
        保留 _pending_delegations 以便继续等待其他未完成的委托。
        """
        self._consecutive_new_delegation_rounds = 0
        # 保留最近 5 条历史思考，防止记忆过多
        self.history_thoughts = self.history_thoughts[-5:] if self.history_thoughts else []
        self.logger.debug("[ContinuousThinker] 已重置状态用于继续任务")

    def write_final_response(self, content: str) -> None:
        """向共享对话框写入最终回复（供外部调用者使用）"""
        if self._get_dialog and self._model_id and content:
            try:
                self._get_dialog.write_response(
                    model_id=self._model_id,
                    tier=self._tier,
                    content=content,
                )
            except Exception as e:
                self.logger.debug(f"[Blackboard] 写入回复失败 (非致命): {e}")

    def produce_intermediate_response(self, max_length: int = 500) -> str:
        """从已完成的思考轮次中提取当前最佳中间回复

        用于中途回复场景：大模型在主管/专家仍在工作时，
        可以先给用户一个初步答案。

        策略：
        1. 取最近一轮有实质内容的思考
        2. 提取"回答"或"结论"相关段落
        3. 截断到 max_length 并加 [preliminary] 标记

        Returns:
            中间回复文本，如果没有足够内容则返回空字符串
        """
        if not self.history_thoughts:
            return ""

        from modules.thinking.context.compression import get_compression_engine
        engine = get_compression_engine()
        max_tokens = max(max_length // 4, 50)

        # 从最新到最旧找有实质内容的思考
        for thought in reversed(self.history_thoughts):
            thought = thought.strip()
            if len(thought) < 30:
                continue

            # 尝试提取结构化段落
            import re
            # 寻找"回答"、"结论"、"建议"、"总结"等段落
            patterns = [
                r'(?:【回答】|【结论】|【建议】|【初步回复】|【当前答案】)\s*(.+?)(?:\n\n|\n【|$)',
                r'(?:回答[：:]|结论[：:]|建议[：:]|初步回复[：:])\s*(.+?)(?:\n\n|\n(?:回答|结论|建议|初步)|$)',
            ]
            for pat in patterns:
                matches = re.findall(pat, thought, re.DOTALL)
                if matches:
                    combined = " ".join(m.strip() for m in matches if m.strip())
                    if len(combined) > 20:
                        truncated = engine._truncate_to_tokens(combined, max_tokens)
                        return f"[preliminary] {truncated}"

            # 回退：取末尾最像结论的段落（最后一段非工具调用内容）
            # 移除工具调用和标记
            cleaned = re.sub(r'【[^】]+】', '', thought)
            cleaned = re.sub(r'<tool_use>.*?</tool_use>', '', cleaned, flags=re.DOTALL)
            paragraphs = [p.strip() for p in cleaned.split('\n\n') if len(p.strip()) > 20]
            if paragraphs:
                last = paragraphs[-1]
                if len(last) > 30:
                    truncated = engine._truncate_to_tokens(last, max_tokens)
                    return f"[preliminary] {truncated}"

        return ""

    async def close(self):
        """关闭资源"""
        self.clear_memory()
        self.clear_external_prompts()
        self.logger.info("ContinuousThinker 已关闭")
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
