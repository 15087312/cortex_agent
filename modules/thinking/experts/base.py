"""
RuntimeExpert — 常驻型专家统一基类

所有需要通过 ModelRunner 常驻运行的专家（安全监察等）
继承此基类，获得统一的生命周期管理、记忆系统、CognitiveBlackboard 接入。

设计原则：
- 同一套代码：所有 RuntimeExpert 子类通过 identity 模板区分行为
- 独立记忆：每个专家有专属的记忆目录 (LongTermMemory.get_expert_dir)
- 统一通信：通过 CognitiveBlackboard 读取请求、写入结果
- 权限集成：通过 ModelIdentity.tool_whitelist 自动控制工具访问

使用方式：
    class MyExpert(RuntimeExpert):
        template_key = "expert_my_expert"  # identity.py 中的模板键

        async def process(self, request_text: str, messages: list) -> str:
            # 处理请求，返回结果
            return result

ModelRunner 检测到 identity.role 匹配时，自动实例化 RuntimeExpert 子类
并调用 run_loop()，无需为每个专家类型写单独的 _think_loop_* 方法。
"""
import inspect
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

from utils.logger import setup_logger

logger = setup_logger("runtime_expert")


class RuntimeExpert(ABC):
    """常驻型专家基类 — 生命周期: init → start → run_cli_mode → stop

    子类只需定义 template_key 和实现 process() 方法。
    框架自动处理身份加载、记忆初始化、CognitiveBlackboard 通信。
    """

    # 子类必须覆盖：identity.py 中的模板键
    template_key: str = ""

    def __init__(
        self,
        model_instance: Any = None,
        blackboard: Any = None,
        session_id: str = "",
        model_id: str = "",
    ):
        self.model_instance = model_instance
        self._blackboard = blackboard             # CognitiveBlackboard
        self.session_id = session_id
        self.model_id = model_id

        # 从模板加载身份
        self.identity = self._load_identity()

        # 启动模式：on_demand（探针激活，空闲退出）或 persistent（常驻，不自动退出）
        self.startup_mode = getattr(self.identity, 'startup', 'on_demand')
        self.is_persistent = (self.startup_mode == "persistent")

        # 专属记忆目录
        self._expert_memory_dir = self._init_expert_memory()
        self._memory_entries: List[Dict[str, Any]] = []
        self._max_memory_entries = 200  # 防止无限增长

        # 创建 expert 专属的 MemoryManager（整个生命周期复用，避免每次 search/save 重复创建）
        self._mm = self._create_memory_manager()

        # 运行状态
        self._running = False
        self._round = 0
        self._started_at: Optional[float] = None
        self._seen_request_entry_ids = set()

        self.logger = setup_logger(f"expert.{self.identity.role}")

        self.logger.info(
            f"[{self.identity.name}] 初始化完成 "
            f"model={self.model_id} role={self.identity.role} "
            f"memory_dir={self._expert_memory_dir}"
        )

    # ------------------------------------------------------------------
    # 对话框/黑板访问
    # ------------------------------------------------------------------

    def _get_dialog(self) -> Any:
        """获取当前有效的 CognitiveBlackboard 实例"""
        return self._blackboard

    # ------------------------------------------------------------------
    # 子类必须实现
    # ------------------------------------------------------------------

    @abstractmethod
    async def process(
        self,
        request_text: str,
        messages: List[Dict[str, Any]],
        dialog_context: str,
    ) -> str:
        """处理请求 — 子类的核心逻辑

        Args:
            request_text: 合并后的请求文本（task_description + messages + dialog）
            messages: MessageBus 消息列表
            dialog_context: Blackboard 上下文

        Returns:
            处理结果文本
        """
        ...

    # ------------------------------------------------------------------
    # 身份 & 记忆
    # ------------------------------------------------------------------

    def _load_identity(self) -> Any:
        """从模板加载 ModelIdentity"""
        if not self.template_key:
            raise ValueError(f"{self.__class__.__name__} 必须定义 template_key")

        from modules.thinking.identity import ModelIdentity
        return ModelIdentity.from_template(self.template_key)

    def _init_expert_memory(self) -> str:
        """初始化专家专属记忆目录（旧版 LongTermMemory 已废弃，使用事件记忆 EventStore）"""
        return ""

    def add_memory(self, category: str, content: str, importance: float = 0.5) -> None:
        """添加一条专属记忆"""
        entry = {
            "category": category,
            "content": content,
            "importance": importance,
            "timestamp": time.time(),
        }
        self._memory_entries.append(entry)
        # 限制大小防止内存泄漏
        if len(self._memory_entries) > self._max_memory_entries:
            self._memory_entries = self._memory_entries[-self._max_memory_entries:]

    def query_memory(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """查询专属记忆（简单关键词匹配，子类可覆盖使用 FAISS）"""
        query_lower = query.lower()
        scored = []
        for entry in self._memory_entries:
            content = entry.get("content", "")
            keywords = query_lower.split()
            hits = sum(1 for kw in keywords if kw in content.lower())
            if hits > 0:
                scored.append((hits / max(len(keywords), 1), entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def _create_memory_manager(self):
        """旧版 MemoryManager 已废弃，返回 None"""
        return None

    async def _load_private_context(self, request_text: str) -> str:
        """加载当前专家可见的 private/global 记忆上下文（已存根，不再使用记忆系统）。"""
        return ""

    def _save_private_memory(self, content: str, source: str = "runtime_expert_response") -> None:
        """保存当前专家私有记忆（已存根，不再使用记忆系统）。"""

    # ------------------------------------------------------------------
    # Blackboard 通信
    # ------------------------------------------------------------------

    def read_requests(self, limit: int = 10) -> List[Dict[str, Any]]:
        """从 Blackboard 读取相关的新请求"""
        if not self._get_dialog():
            return []
        try:
            all_entries = self._get_dialog().read_dialog(limit=limit * 5)
            requests = []
            for e in all_entries:
                entry_id = e.get("entry_id", "")
                metadata = e.get("metadata") or {}
                if entry_id and entry_id in self._seen_request_entry_ids:
                    continue
                if e.get("model_id") == self.model_id:
                    continue
                if e.get("model_id") == "system" or e.get("tier") == "system":
                    continue
                if metadata.get("visibility") == "hidden" or metadata.get("internal_protocol"):
                    continue
                content = e.get("content", "")
                if not self._is_relevant(content):
                    continue
                requests.append(e)
                if entry_id:
                    self._seen_request_entry_ids.add(entry_id)
            return requests[-limit:]
        except Exception as e:
            self.logger.debug(f"读取对话框失败: {e}")
            return []

    def _is_relevant(self, content: str) -> bool:
        """判断对话框内容是否与本专家相关"""
        name = self.identity.name
        role = self.identity.role
        return (
            name in content
            or role in content
            or self.model_id in content
        )

    def write_thought(self, content: str, round_num: int = 0) -> Optional[str]:
        """写入思考过程到 Blackboard"""
        if not self._get_dialog():
            return None
        try:
            entry = self._get_dialog().write_thought(
                model_id=self.model_id,
                tier=self.identity.tier,
                content=str(content),
                round_num=round_num,
            )
            return entry.entry_id if entry else None
        except Exception as e:
            self.logger.debug(f"写入思考失败: {e}")
            return None

    def write_response(self, content: str) -> Optional[str]:
        """写入最终结果到 Blackboard"""
        if not self._get_dialog():
            return None
        try:
            # 注: base.py 的 write_response 走 dialog 路径写入 expert_findings 区段
            # model_runner 的 supervisor 路径仍直接调用 blackboard.write_expert_finding()
            entry = self._get_dialog().write_response(
                model_id=self.model_id,
                tier=self.identity.tier,
                content=str(content),
                metadata={
                    "blackboard_section": "expert_findings",
                    "visibility": "agents_only",
                },
            )
            self._save_private_memory(str(content))
            return entry.entry_id if entry else None
        except Exception as e:
            self.logger.debug(f"写入响应失败: {e}")
            return None

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 主循环 — 由 ModelRunner 调用
    # ------------------------------------------------------------------

    async def run_loop(
        self,
        check_messages_fn,
        task_description: str,
        max_rounds: int = 20,
        max_idle_rounds: int = 5,
        think_interval: float = 2.0,
    ) -> None:
        """统一的专家主循环 — ModelRunner._run_think_loop() 调用此方法

        常驻模式 (startup="persistent"): max_idle_rounds → 无限大，不自动退出
        探针模式 (startup="on_demand"): 空闲 max_idle_rounds 轮后自动停止

        Args:
            check_messages_fn: 检查 MessageBus 的回调 () -> List[Dict]
            task_description: 任务描述
            max_rounds: 最大轮次
            max_idle_rounds: 最大空闲轮次（persistent 专家自动设为无限）
            think_interval: 轮间间隔（秒）
        """
        import asyncio

        # 常驻模式：不限空闲轮次，不限总轮次
        if self.is_persistent:
            max_idle_rounds = 999999
            max_rounds = 999999
            think_interval = max(think_interval, 3.0)  # 常驻模式稍微拉长轮间间隔节约资源
            self.logger.info(
                f"[{self.identity.name}] 常驻模式启动 "
                f"(startup={self.startup_mode})"
            )

        self._running = True
        self._started_at = time.time()
        idle_rounds = 0

        # 写入就绪通知
        self.write_thought(
            f"[{self.identity.name}就绪] 记忆目录: {self._expert_memory_dir}",
            round_num=0,
        )

        # 事件驱动：MessageBus 变动时唤醒（替代轮询）
        message_event = asyncio.Event()
        try:
            from modules.thinking.communication.message_bus import get_message_bus
            bus = get_message_bus()
            await bus.subscribe(self.model_id, lambda _: message_event.set())
        except Exception as e:
            self.logger.debug(f"[MessageBus] 订阅失败，回退轮询模式: {e}")

        try:
            while self._running and self._round < max_rounds:
                self._round += 1

                try:
                    # 1. 检查 MessageBus
                    if inspect.iscoroutinefunction(check_messages_fn):
                        messages = await check_messages_fn()
                    else:
                        messages = check_messages_fn()
                    has_messages = bool(messages)

                    # 2. 读取 Blackboard 对话上下文
                    dialog_requests = self.read_requests(limit=5)
                    dialog_context = ""
                    if self._get_dialog():
                        try:
                            dialog_context = self._get_dialog().format_for_model(
                                limit=10,
                                exclude_tier=self.identity.tier,
                            )
                        except Exception as e:
                            self.logger.debug(f"[Blackboard] format_for_model 失败 (非致命): {e}")

                    # 3. 构建请求文本
                    request_parts = [task_description]
                    for msg in messages:
                        request_parts.append(str(msg.get("content", "")))
                    for req in dialog_requests:
                        request_parts.append(str(req.get("content", "")))
                    private_context = await self._load_private_context(task_description)
                    if private_context:
                        request_parts.append(f"【你的私有记忆上下文】\n{private_context}")
                    combined_request = "\n\n".join(request_parts)

                    # 4. 如果有实质请求（或第一轮），调用子类的 process()
                    if combined_request.strip() and (
                        has_messages or dialog_requests or self._round == 1
                    ):
                        result = await self.process(
                            request_text=combined_request,
                            messages=messages,
                            dialog_context=dialog_context,
                        )

                        if result:
                            self.write_thought(result, round_num=self._round)
                            idle_rounds = 0
                            # 非持久专家完成一轮后自动退出（工具执行一次就结束，不空转）
                            if not self.is_persistent and not has_messages and not dialog_requests:
                                self.logger.info(
                                    f"[{self.identity.name}] 任务完成，自动停止"
                                )
                                break
                        else:
                            idle_rounds += 1
                    else:
                        idle_rounds += 1

                    # 5. 空闲超时退出（但对于 tool_expert 禁用，因为需要保持活跃等待委托）
                    if idle_rounds >= max_idle_rounds:
                        # tool_expert 和 memory_manager 需要持续监听委托，不应自动退出
                        is_persistent_expert = self.identity.role in ("memory_manager",)
                        if not is_persistent_expert:
                            self.logger.info(
                                f"[{self.identity.name}] 连续 {idle_rounds} 轮无请求，自动停止"
                            )
                            break
                        else:
                            # 重置计数器，继续等待新委托
                            self.logger.debug(
                                f"[{self.identity.name}] 连续 {idle_rounds} 轮空闲，但保持活跃等待委托"
                            )
                            idle_rounds = 0

                    # 6. 检查终止信号
                    if has_messages:
                        for msg in messages:
                            content = str(msg.get("content", ""))
                            if "TASK_COMPLETE" in content or "停止委托" in content:
                                self.logger.info(f"[{self.identity.name}] 收到终止信号")
                                self._running = False
                                break

                    # 7. 事件驱动等待（替代固定 sleep）
                    #    有消息到达立即唤醒；无消息时最多等 think_interval*3 秒（兜底）
                    try:
                        await asyncio.wait_for(
                            message_event.wait(), timeout=think_interval * 3,
                        )
                    except asyncio.TimeoutError:
                        pass
                    message_event.clear()
                    # debounce：短时间内多次变化批处理
                    await asyncio.sleep(0.3)
                    message_event.clear()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(
                        f"[{self.identity.name}] 第{self._round}轮异常: {e}"
                    )
                    # 异常记录到 Blackboard — 让其他模型能看到发生了什么
                    try:
                        self.write_thought(f"[专家异常: {str(e)[:200]}]", round_num=self._round)
                    except Exception:
                        pass
                    # 异常时用兜底 sleep 代替 event wait
                    await asyncio.sleep(think_interval)

        finally:
            # 取消订阅
            try:
                await bus.unsubscribe(self.model_id)
            except Exception as e:
                self.logger.debug(f"[MessageBus] 取消订阅失败 (非致命): {e}")

        # 写入最终状态
        status = self.get_status()
        self.write_response(
            f"[{self.identity.name}关闭] 共 {self._round} 轮, "
            f"状态: {status}"
        )

        self.logger.info(
            f"[{self.identity.name}] 循环结束 (共 {self._round} 轮)"
        )

    def stop(self) -> None:
        """停止专家"""
        self._running = False

    # ------------------------------------------------------------------
    # CLI模式 - 主动执行模式（由 Supervisor 调用）
    # ------------------------------------------------------------------

    async def run_cli_mode(
        self,
        task: str,
        max_iterations: int = 10,
        timeout: int = 300,
        round_timeout: int = 60,
    ) -> Dict[str, Any]:
        """
        CLI模式：接收任务，主动连续执行直到完成

        工作流程：
        1. 构建初始提示词
        2. while 循环：思考 → 解析工具调用 → 执行 → 注入结果
        3. 无工具调用 = 任务完成
        4. 返回最终结果

        这是 Expert 的主动执行模式，与被动的 run_loop() 相对。
        由 Supervisor(ModelRunner) 调用。

        Args:
            task: 任务描述
            max_iterations: 最大迭代次数（防止无限循环）
            timeout: 总体超时时间（秒），所有迭代的上限
            round_timeout: 每轮超时时间（秒），每轮独立计时

        Returns:
            {
                'success': bool,
                'result': str,           # 最终答案
                'iterations': int,       # 实际迭代次数
                'tool_calls': int,       # 执行的工具调用数
                'tool_history': [...],   # 完整的工具执行历史
            }
        """
        import asyncio

        self.logger.info(
            f"[{self.identity.name}] CLI模式启动: {task[:80]}..."
        )

        tool_history = []
        iteration = 0
        current_response = None
        start_time = time.time()

        try:
            while iteration < max_iterations:
                # 检查总体超时（保留作为安全上限）
                elapsed_total = time.time() - start_time
                if elapsed_total > timeout:
                    self.logger.warning(
                        f"[{self.identity.name}] 总体超时（{elapsed_total:.1f}s > {timeout}s）"
                    )
                    self.write_thought(f"[专家总体超时: {elapsed_total:.1f}s]", round_num=iteration)
                    return {
                        'success': False,
                        'error': f'Timeout after {timeout}s',
                        'iterations': iteration,
                        'tool_calls': len(tool_history),
                    }

                # 重新计时：每轮独立的超时检查
                round_start_time = time.time()

                iteration += 1
                self.logger.info(
                    f"[{self.identity.name}] 迭代 {iteration}/{max_iterations} (轮超时: {round_timeout}s)"
                )

                # 1️⃣ 构建提示词（注入工具执行历史）
                prompt = self._build_cli_prompt(
                    task=task,
                    tool_history=tool_history,
                    iteration=iteration,
                )

                # 2️⃣ 调用模型生成响应（使用轮级超时）
                try:
                    round_elapsed = time.time() - round_start_time
                    if round_elapsed > round_timeout:
                        self.logger.warning(
                            f"[{self.identity.name}] 本轮超时（{round_elapsed:.1f}s > {round_timeout}s）"
                        )
                        self.write_thought(f"[专家轮次超时: {round_elapsed:.1f}s]", round_num=iteration)
                        return {
                            'success': False,
                            'error': f'Round timeout after {round_timeout}s in iteration {iteration}',
                            'iterations': iteration,
                            'tool_calls': len(tool_history),
                        }

                    # 计算该步骤的可用时间（轮级剩余时间 或 总体剩余时间，取较小值）
                    remaining_round_time = round_timeout - round_elapsed
                    remaining_total_time = timeout - (time.time() - start_time)
                    remaining_time = min(remaining_round_time, remaining_total_time, 60)

                    current_response = await asyncio.wait_for(
                        self._model_generate(prompt),
                        timeout=max(5, remaining_time),  # 确保至少给5s
                    )
                except asyncio.TimeoutError:
                    self.write_thought(f"[专家模型生成超时]", round_num=iteration)
                    return {
                        'success': False,
                        'error': f'Model generation timeout in iteration {iteration}',
                        'iterations': iteration,
                        'tool_calls': len(tool_history),
                    }

                if not current_response:
                    return {
                        'success': False,
                        'error': 'Empty response from model',
                        'iterations': iteration,
                        'tool_calls': len(tool_history),
                    }

                # 3️⃣ 解析工具调用
                tool_calls = self._extract_tool_calls(current_response)

                if not tool_calls:
                    # ✅ 无工具调用 = 任务完成
                    self.logger.info(
                        f"[{self.identity.name}] 完成 "
                        f"({iteration}轮, {len(tool_history)}个工具调用)"
                    )
                    return {
                        'success': True,
                        'result': current_response,
                        'iterations': iteration,
                        'tool_calls': len(tool_history),
                        'tool_history': tool_history,
                    }

                # 4️⃣ 执行工具调用
                for tool_call in tool_calls:
                    try:
                        tool_name = tool_call.get('name', 'unknown')
                        tool_result = await self._execute_tool_call(tool_call)

                        tool_history.append({
                            'iteration': iteration,
                            'tool': tool_name,
                            'input': tool_call.get('arguments', {}),
                            'output': tool_result,
                            'timestamp': time.time(),
                        })

                        self.logger.info(
                            f"[{self.identity.name}] 执行工具: "
                            f"{tool_name} → {str(tool_result)[:100]}"
                        )

                    except Exception as e:
                        self.logger.error(
                            f"[{self.identity.name}] 工具执行异常: {e}"
                        )
                        tool_history.append({
                            'iteration': iteration,
                            'tool': tool_call.get('name', 'unknown'),
                            'input': tool_call.get('arguments', {}),
                            'error': str(e),
                            'timestamp': time.time(),
                        })

            # 达到最大迭代次数（通常表示任务复杂或多轮讨论）
            return {
                'success': True,
                'result': current_response or '',
                'iterations': iteration,
                'tool_calls': len(tool_history),
                'tool_history': tool_history,
                'reached_max_iterations': True,
            }

        except Exception as e:
            self.logger.error(
                f"[{self.identity.name}] CLI模式异常: {e}"
            )
            return {
                'success': False,
                'error': str(e),
                'iterations': iteration,
                'tool_calls': len(tool_history),
            }

    def _build_cli_prompt(
        self,
        task: str,
        tool_history: List[Dict[str, Any]],
        iteration: int,
    ) -> str:
        """构建提示词，注入工具执行历史"""

        # 列出可用工具
        try:
            from infra.tool_manager.tool_registry import ToolRegistry
            all_tools = ToolRegistry.list_tools()
            # 按 tool_whitelist 过滤
            whitelist = self.identity.tool_whitelist if self.identity.tool_whitelist else list(all_tools.keys())
            available = [t for t in whitelist if t in all_tools]
            tool_lines = "\n".join(
                f"  - {name}: {all_tools[name].get('description', '')}"
                for name in available[:15]
            )
        except Exception:
            tool_lines = "  - 使用系统可用工具"

        prompt = f"""你是 {self.identity.name}，角色：{self.identity.role}
专长：{', '.join(self.identity.expertise)}

任务: {task}

【可用工具】
{tool_lines}
"""

        # 注入已执行的工具调用结果
        if tool_history:
            prompt += "\n【已执行的步骤】\n"
            for i, call in enumerate(tool_history, 1):
                tool_name = call.get('tool', '?')
                output = call.get('output', '').strip() if call.get('output') else "(无输出)"
                output_preview = output[:200] if output else "(无输出)"

                prompt += f"{i}. [{tool_name}]\n   结果: {output_preview}\n"

            prompt += "\n"

        prompt += (
            f"第 {iteration} 轮迭代\n\n"
            "现在你可以：\n"
            "1. 继续执行工具调用（如果还需要获取更多信息或完成更多步骤）\n"
            "2. 或者直接输出最终答案（如果任务已完成）\n\n"
            "若需执行工具，使用此格式：\n"
            "<tool>\n"
            "name: <工具名>\n"
            "arguments: <JSON参数>\n"
            "</tool>\n\n"
            "否则直接输出最终答案。"
        )

        return prompt

    async def _model_generate(self, prompt: str) -> str:
        """调用模型生成响应"""
        if not self.model_instance:
            raise RuntimeError("Model instance not available")

        try:
            # 使用模型的 generate() 方法
            response = await self.model_instance.generate(
                prompt=prompt,
                stream=False,
            )
            return response if response else ""
        except Exception as e:
            self.logger.error(f"模型调用异常: {e}")
            raise

    def _extract_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """从模型响应中解析工具调用"""
        import re
        import json

        tool_calls = []

        # 匹配 <tool>name: xxx / arguments: {...}</tool> 格式
        pattern = r"<tool>\s*name:\s*(\w+)\s*\n\s*arguments:\s*({[^}]+})\s*</tool>"
        matches = re.finditer(pattern, response, re.DOTALL)

        for match in matches:
            try:
                tool_name = match.group(1).strip()
                args_str = match.group(2)
                args = json.loads(args_str)

                tool_calls.append({
                    'name': tool_name,
                    'arguments': args,
                })
                self.logger.debug(f"解析工具调用: {tool_name}")
            except (json.JSONDecodeError, IndexError, ValueError) as e:
                self.logger.debug(f"工具调用解析失败: {e}")

        return tool_calls

    async def _execute_tool_call(self, tool_call: Dict[str, Any]) -> str:
        """执行单个工具调用"""
        tool_name = tool_call.get('name', '')
        arguments = tool_call.get('arguments', {})

        # arguments 可能是 JSON 字符串
        if isinstance(arguments, str):
            try:
                import json
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}

        if not tool_name:
            return "Error: tool_name is empty"

        try:
            from infra.tool_manager.tool_registry import ToolRegistry
            func = ToolRegistry.get_func(tool_name)
            if func is None:
                return f"Error: unknown tool '{tool_name}'"

            import inspect
            if inspect.iscoroutinefunction(func):
                import asyncio
                result = await func(**arguments)
            else:
                result = func(**arguments)
            return str(result)
        except Exception as e:
            self.logger.error(f"工具 {tool_name} 执行失败: {e}")
            return f"Error: {str(e)}"

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """获取专家状态"""
        return {
            "name": self.identity.name,
            "role": self.identity.role,
            "tier": self.identity.tier,
            "model_id": self.model_id,
            "session_id": self.session_id,
            "startup": self.startup_mode,
            "persistent": self.is_persistent,
            "round": self._round,
            "running": self._running,
            "memory_dir": self._expert_memory_dir,
            "memory_entries": len(self._memory_entries),
            "has_blackboard": self._blackboard is not None,
            "has_model_instance": self.model_instance is not None,
            "uptime": time.time() - self._started_at if self._started_at else 0,
        }


# ---------------------------------------------------------------------------
# 专家类注册表 — 让 ModelRunner 能根据 role 自动找到对应的 RuntimeExpert 子类
# ---------------------------------------------------------------------------

_RUNTIME_EXPERT_REGISTRY: Dict[str, type] = {}


def register_runtime_expert(role: str, expert_cls: type) -> None:
    """注册 RuntimeExpert 子类, 使其能通过 role 自动激活"""
    _RUNTIME_EXPERT_REGISTRY[role] = expert_cls


def get_runtime_expert_class(role: str) -> Optional[type]:
    """根据 role 获取 RuntimeExpert 子类"""
    return _RUNTIME_EXPERT_REGISTRY.get(role)
