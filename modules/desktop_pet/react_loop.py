"""桌宠独立 ReAct 循环骨架 — 循环本体由用户亲自设计

桌宠页面的对话不再走「会话 + 全局模型」的旧链路，而是运行一个独立的
ReAct 循环。本文件定义接口、默认直答实现（direct）与注册表：

- PetReactLoop  抽象基类：所有循环实现的统一契约
- DirectLoop    默认实现：上下文 + 历史 + 人设 → LLM 直答（保持原行为）
- CustomLoop    桌宠专属循环（用户设计）：8k 本地模型 / 512 输出 / 思考间隔
                / 独立工具管控，见类 docstring
- build_react_loop()  按 PetConfig.loop_type 实例化循环
"""
import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Dict, Optional, Type

from utils.logger import setup_logger

if TYPE_CHECKING:
    from modules.desktop_pet.pet_engine import PetEngine
    from infra.model.base_model import ChatMessage

logger = setup_logger("pet_react_loop")


class PetReactLoop(ABC):
    """桌宠 ReAct 循环契约"""

    name: str = "base"

    @abstractmethod
    async def run(
        self,
        engine: "PetEngine",
        text: str,
        on_token: Optional[Callable[[str], None]] = None,
        extra_system: str = "",
    ) -> str:
        """执行一次完整循环，返回桌宠回复文本（空串表示放弃回复）。

        on_token 非 None 时表示需要流式产出：循环应把产出逐 token 回调给
        on_token（同步回调），并在结束时返回完整文本。
        extra_system 为外部注入的系统提示（如互动动作 prompt、状态描述）。
        """


class DirectLoop(PetReactLoop):
    """默认直答循环：上下文 + 历史 + 人设 → LLM → 返回回复

    - 非流式：等价于旧 pet_engine.chat 的行为（持久化由 engine.chat() 的 save_pair 负责）
    - 流式：逐 token 回调 on_token、思考过程回调 on_reasoning、
      完整回复经 on_message 上报（由 engine 层持久化 + 注入轮次完成标记）
    存储与配置来源均为桌宠独立设施（pet.db / PetConfig）。
    """

    name = "direct"

    async def run(
        self,
        engine: "PetEngine",
        text: str,
        on_token: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
        extra_system: str = "",
    ) -> str:
        context = await engine.build_context(text)
        combined = "\n\n".join(x for x in [context, extra_system] if x)
        messages = engine.build_messages(text, extra_system=combined)

        def _reply_of(resp, collected: list) -> str:
            """取回复文本：content 优先；为空时用 reasoning_content 兜底
            （spark 系 abliterated 推理模型把正式输出全放在 reasoning_content，content 恒空）。
            """
            if resp is None or not getattr(resp, "message", None):
                return ""
            msg = resp.message
            reply = (msg.content or "").strip()
            if not reply:
                reply = (msg.reasoning_content or "").strip()
            if not reply:
                reply = "".join(collected).strip()
            return reply

        if on_token is None:
            response = await engine.client.chat(messages=messages)
            reply = _reply_of(response, [])
        else:
            collected: list = []
            response = await engine.client.chat_stream(
                messages=messages,
                on_token=lambda t: (collected.append(t), on_token(t)),
                on_reasoning=on_reasoning,
                tools=CustomLoop._native_tools() or None,
            )
            reply = _reply_of(response, collected)
        if reply:
            # 流式：on_message 负责持久化 + 轮次完成标记（PetPersist）；
            # 非流式：engine.chat() 负责 save_pair —— 这里不重复落库
            if on_message is not None:
                on_message(reply)
            logger.info(f"[Pet] 对话: {text[:40]} → {reply[:40]}")
        return reply


class CustomLoop(PetReactLoop):
    """桌宠专属 ReAct 循环（用户设计的规格落地）

    设计规格（8k 本地模型约束下）：
    - 模型：本地 8k 上下文，单次思考最大输出 512 token（max_output_tokens）。
    - 思考间隔（min_think_interval）：每次思考之间的最短停止时间，
      从「前一次思考开始」时刻起算（now - last_think_start >= 间隔）。
    - 思考循环：思考 → 输出工具名则执行工具 → 观察结果 → 再次思考，
      最多 max_loop_rounds 轮；思考输出为纯文本时结束循环。
    - 工具系统独立管控：桌宠白名单（PET_TOOL_WHITELIST）+ tools_enabled 开关；
      优先用完整 OpenAI tools 数组（含 JSON Schema 参数描述）走原生 tool_calls
      协议（role=tool 观察回填）；provider 不支持时降级为「模型只输出工具名」
      的文本协议（文本清单 + JSON/括号解析）。基础工具复用项目 ToolManager。
    - 上下文管理独立：最近 max_history 条对话（默认 8，≤4096 token，用户/AI
      各算）；记忆复用 PetMemory 只取 top2；感知复用感知系统（传感排序稍后设计）。

    挂载：注册为 "custom"，PetConfig.loop_type 设为 "custom" 即启用。
    """

    name = "custom"

    # 桌宠工具白名单（基础工具复用项目 ToolManager，只开放轻量查询/操作）
    PET_TOOL_WHITELIST = {
        "web_search", "web_fetch",
        "calc", "calc_advanced", "calc_sum", "calc_avg",
        "todo", "open_app", "list_directory",
    }

    _TOOL_JSON_RE = re.compile(r"\{\s*\"tool\"\s*:\s*\"([^\"}]+)\"", re.S)
    _TOOL_BRACKET_RE = re.compile(r"\[tool\s*:\s*([A-Za-z0-9_\-]+)\s*\]", re.I)

    def __init__(self):
        self._last_think_start = 0.0  # 上次思考开始时刻（跨 chat 生效）

    # ── 主入口 ──

    # @link:block PetLoop
    async def run(
        self,
        engine: "PetEngine",
        text: str,
        on_token: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
        extra_system: str = "",
    ) -> str:
        cfg = engine.config

        # @link:block PetThrottle = Throttle
        interval = float(cfg.get("min_think_interval") or 0)
        tools_enabled = bool(cfg.get("tools_enabled", True))
        # max_loop_rounds=0（默认）= 持续思考循环：每次思考之后再次思考，
        # 直到被外部停止（SSE 断开/取消）、上下文接近 8k 上限。
        # 空输出不终止循环，触发双倍冷却后继续等待新想法。
        rounds = int(cfg.get("max_loop_rounds") or 0)
        max_tokens = max(1, int(cfg.get("max_output_tokens") or 512))

        # 2. 上下文：人设 + 历史(8) + 独立记忆/感知 + 用户输入
        context = await engine.build_context(text)
        combined = "\n\n".join(x for x in [context, extra_system] if x)
        messages = engine.build_messages(text, extra_system=combined)
        # 工具描述：优先完整 OpenAI tools 数组（原生协议）；
        # 拿不到 schema（工具库不可用/白名单全空）时才注入文本清单（兜底格式说明）
        native_tools = self._native_tools() if tools_enabled else []
        if tools_enabled and not native_tools:
            listing = self._tool_listing()
            if listing:
                messages.append(_tool_message(listing))

        # 3. 持续思考循环：思考 → 工具？ → 观察 → 再思考；文本输出后仍继续下一轮
        parts: list = []
        empty_streak = 0
        obs = ""
        i = 0
        while True:
            i += 1
            if rounds and i > rounds:
                logger.info(f"[Pet] 达到 max_loop_rounds={rounds}，停止思考循环")
                break
            # 上下文 8k 预算保护：对话 + tools schema 累计超出安全线则停止
            # （每轮输出 ≤ max_output_tokens；schema 不随轮次增长）
            if self._context_overflow(messages, extra_chars=len(json.dumps(native_tools, ensure_ascii=False)) if native_tools else 0):
                logger.info("[Pet] 上下文接近 8k 上限，停止思考循环")
                break
            # 思考间隔：从前一次思考开始时刻起算（两次思考开始之间至少间隔 min_think_interval）
            # 空输出后触发双倍冷却，让桌宠安静一会儿再继续
            if interval > 0:
                effective_interval = interval * (2 if empty_streak > 0 else 1)
                wait = self._last_think_start + effective_interval - time.time()
                if wait > 0:
                    label = "双倍" if empty_streak > 0 else "正常"
                    logger.info(f"[Pet] 思考间隔冷却中（{label}），等待 {wait:.1f}s")
                    await asyncio.sleep(wait)
            self._last_think_start = time.time()
            # @link:block PetThink = Think
            out = await self._think(engine, messages, max_tokens, i, obs, on_token, on_reasoning, tools=native_tools)
            if out.get("tool"):
                if tools_enabled and out["tool"] in self.PET_TOOL_WHITELIST:
                    # @link:block PetTool = Tool
                    obs = await self._run_tool(engine, out["tool"], out.get("params"))
                else:
                    obs = f"工具 {out.get('tool')} 不可用（未启用或不在桌宠白名单）"
                # @link:block PetObserve = Observe
                obs = self._observe(obs)
                # 观察回填：原生协议 tool 结果以 role=tool 关联 tool_call_id（OpenAI 格式）；
                # 文本协议无 id，退回 user 角色观察消息
                if out.get("tool_call_id"):
                    from infra.model.base_model import ChatMessage
                    messages.append(ChatMessage(role="tool", content=obs, tool_call_id=out["tool_call_id"]))
                else:
                    messages.append(_tool_message(f"【工具观察】{obs}"))
                empty_streak = 0
                continue
            out_text = (out.get("text") or "").strip()
            if not out_text:
                empty_streak += 1
                # 空输出不终止循环，触发双倍冷却后继续等待新想法
                logger.info(f"[Pet] 本轮无输出（空输出第{empty_streak}次），进入双倍冷却")
                continue
            # 重复护栏：与上一轮产出完全相同 → 模型陷入复述，不再上报/落库，直接终止
            if parts and out_text == parts[-1]:
                logger.info("[Pet] 本轮输出与上一轮重复，停止思考循环")
                break
            empty_streak = 0
            # 文本产出：进入上下文供下一轮思考引用，并通知上层持久化/展示
            parts.append(out_text)
            from infra.model.base_model import ChatMessage
            messages.append(ChatMessage(role="assistant", content=out_text))
            if on_message:
                on_message(out_text)
        # 汇总本轮全部产出（落库由 engine 层 on_message 通道完成，见 PetPersist）
        reply = "\n".join(parts)
        if reply:
            logger.info(f"[Pet] Custom 循环完成: {text[:30]} → {reply[:60]}")
        # @link:block PetEmit = Emit
        return reply

    @staticmethod
    def _context_overflow(messages, budget_chars: int = 6000, extra_chars: int = 0) -> bool:
        """8k 上下文预算：对话累计字符数 + 额外开销（tools schema）粗估超限即停

        中文约 1.5 token/字，6000 字符≈4000 token。schema 不随轮次增长，只计一次。
        """
        total = extra_chars
        for m in messages:
            total += len(getattr(m, "content", "") or "")
        return total > budget_chars

    @staticmethod
    def _messages_chars(messages) -> int:
        """对话累计字符数（8k 预算粗估用）"""
        return sum(len(getattr(m, "content", "") or "") for m in messages)

    # ── 观察（工具结果归一化，进入下一轮思考） ──

    @staticmethod
    def _observe(raw: str) -> str:
        """观察：截断/归一化工具结果，控制 8k 上下文占用"""
        raw = (raw or "").strip()
        if len(raw) > 800:
            raw = raw[:800] + "…"
        return raw

    # ── 思考（单轮） ──

    async def _think(
        self,
        engine: "PetEngine",
        messages: list,
        max_tokens: int,
        round_idx: int,
        obs: str,
        on_token: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        tools: Optional[list] = None,
    ) -> Dict:
        msgs = list(messages)
        # spark 等本地服务器对「assistant 结尾续写」返回空；持续循环每轮思考后
        # messages 以 assistant 产出结尾，补一条弱引导 user 消息触发本轮生成
        # （仅本轮请求使用，不持久化；下轮以该引导 + 新产出的 assistant 续接）。
        if msgs and getattr(msgs[-1], "role", "") != "user":
            from infra.model.base_model import ChatMessage
            msgs = msgs + [ChatMessage(
                role="user",
                content="（请根据当前对话历史自言自语地思考、自然延续话题、反思，或者什么都不说表示你想安静一会儿。）",
            )]
        if obs:
            # 原生协议：观察已作为 role=tool 消息进入 messages；
            # 文本协议：追加 user 观察消息
            if not any(m.role == "tool" for m in messages[-2:] if hasattr(m, "role")):
                msgs.append(_tool_message(f"【工具观察】{obs}"))
        collected: list = []
        # 思考过程实时转发（过程性内容，无需等确认；正文 token 才延迟确认）
        response = await engine.client.chat_stream(
            messages=msgs,
            max_tokens=max_tokens,
            on_token=lambda t: collected.append(t),
            on_reasoning=on_reasoning,
            tools=tools or None,
        )
        raw = (response.message.content or "").strip() if response and response.message else ""
        # spark 系模型 content 恒空（输出全在 reasoning_content），兜底取之
        if not raw and response is not None and getattr(response, "message", None) is not None:
            raw = (getattr(response.message, "reasoning_content", None) or "").strip()
        if not raw:
            raw = "".join(collected)
        raw = raw.strip()
        # 清洗模型可能混入正文的 thinking 标签（qwen/deepseek 系模型偶发），
        # 避免 </think> 污染历史上下文导致后续轮次异常
        raw = re.sub(r"<think\b[^>]*>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
        raw = re.sub(r"</?think\b[^>]*>", "", raw, flags=re.IGNORECASE).strip()
        # 原生 tool_calls：客户端已流式累积（OpenAI/Anthropic 格式）
        if response is not None and getattr(response.message, "tool_calls", None):
            tc = response.message.tool_calls[0]
            params: Dict = {}
            try:
                parsed = json.loads(tc.arguments) if tc.arguments else {}
                if isinstance(parsed, dict):
                    params = parsed
            except Exception:
                params = {}
            return {"tool": tc.name, "params": params, "tool_call_id": tc.id, "text": ""}
        if not raw:
            return {"text": ""}
        # 解析：JSON 工具调用 / [tool: name] / 纯文本
        parsed = self._parse_tool_call(raw)
        if parsed:
            # 工具轮：不把工具 JSON 片段流给前端
            return parsed
        # 文本回复确认后才逐片段重放（真实流式粒度），避免前端一次性等待
        if on_token is not None:
            for t in collected:
                on_token(t)
        return {"text": raw}

    # ── 工具执行（独立管控：白名单 + 模板按需查询） ──

    async def _run_tool(self, engine: "PetEngine", tool_name: str, params: Optional[Dict]) -> str:
        try:
            from infra.tool_manager.tool_manager import ToolManager
            tm = ToolManager()
            info = tm.get_tool_info(tool_name)
            if info is None:
                available = tm.list_available_tools()
                if tool_name not in available:
                    return f"工具 {tool_name} 不存在"
                info = available[tool_name]
            spec = info.get("params") if isinstance(info, dict) else {}
            spec = spec or {}
            params = params or {}
            # 只返回工具名 → 模板不预置；执行前查询模板，缺必填参数则引导补齐
            missing = [k for k in spec if k not in params]
            if missing:
                hint = json.dumps(spec, ensure_ascii=False)[:240]
                return (
                    f"工具 {tool_name} 需要参数：{', '.join(missing)}。"
                    f"参数说明：{hint}。请把 {', '.join(missing)} 补进 params 再调用。"
                )
            result = await tm.call_tool(tool_name, params, caller_role="pet")
            return self._fmt_result(tool_name, result)
        except Exception as e:
            logger.error(f"[Pet] 工具 {tool_name} 执行失败: {e}")
            return f"工具 {tool_name} 执行失败：{e}"

    @staticmethod
    def _fmt_result(tool_name: str, result: Dict) -> str:
        """工具结果截断为观察（≤800 字符，控制 8k 上下文占用）"""
        try:
            if not isinstance(result, dict):
                text = str(result)
            elif result.get("success"):
                text = str(result.get("result") or result.get("data") or result)
            else:
                text = f"失败：{result.get('error') or result}"
        except Exception:
            text = str(result)
        text = (text or "").strip()
        if len(text) > 800:
            text = text[:800] + "…"
        return f"[{tool_name} 结果] {text}"

    # ── 工具清单 / 原生 tools 数组 ──

    @classmethod
    def _tool_listing(cls) -> str:
        try:
            from infra.tool_manager.tool_manager import ToolManager
            available = ToolManager().list_available_tools()
        except Exception as e:
            logger.debug(f"[Pet] 工具列表不可用: {e}")
            return ""
        lines = []
        for name in sorted(cls.PET_TOOL_WHITELIST):
            spec = available.get(name)
            if not spec:
                continue
            desc = spec.get("description", "") if isinstance(spec, dict) else ""
            one_line = (desc or "").split("\n")[0][:60]
            lines.append(f"- {name}：{one_line or '无描述'}")
        if not lines:
            return ""
        return (
            "【可用工具】（桌宠独立管控，基础能力来自项目工具库）\n"
            + "\n".join(lines)
            + "\n需要调用工具时，只输出工具名，格式：{\"tool\":\"工具名\",\"params\":{...}}，"
              "params 可省略（参数说明在执行时才提供）。不需要工具时直接回答。"
        )

    @classmethod
    def _native_tools(cls) -> list:
        """构建完整 OpenAI tools 数组（白名单内工具，含 JSON Schema 参数）。

        工具库不可用或白名单内无任何已注册工具时返回 []（调用方降级文本协议）。
        params 已是 dict（ParamSchema/字符串混合）→ 直接作为 properties（值归一为 {type, description}）。
        """
        try:
            from infra.tool_manager.tool_manager import ToolManager
            available = ToolManager().list_available_tools()
        except Exception as e:
            logger.debug(f"[Pet] 原生工具列表不可用: {e}")
            return []
        tools = []
        for name in sorted(cls.PET_TOOL_WHITELIST):
            spec = available.get(name)
            if not spec:
                continue
            desc = spec.get("description", "") if isinstance(spec, dict) else ""
            props = {}
            params = spec.get("params") or {} if isinstance(spec, dict) else {}
            for pname, pspec in params.items():
                if isinstance(pspec, dict):
                    prop = {k: v for k, v in pspec.items() if k in ("type", "description", "enum", "items")}
                else:
                    prop = {"type": "string", "description": str(pspec)}
                props[pname] = prop
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": (desc or "无描述").split("\n")[0][:200],
                    "parameters": {"type": "object", "properties": props},
                },
            })
        return tools

    # ── 输出解析（文本协议兑底：模型未走原生 tool_calls 时） ──

    def _parse_tool_call(self, raw: str) -> Optional[Dict]:
        """解析模型输出中的工具调用：只认工具名（不预置完整模板）"""
        m = self._TOOL_JSON_RE.search(raw)
        if m:
            tool = m.group(1).strip()
            params = {}
            try:
                obj = json.loads(raw[m.start():].strip())
                params = obj.get("params") or {}
            except Exception:
                pass
            return {"tool": tool, "params": params, "text": raw}
        m = self._TOOL_BRACKET_RE.search(raw)
        if m:
            return {"tool": m.group(1).strip(), "params": {}, "text": raw}
        return None


def _tool_message(content: str):
    from infra.model.base_model import ChatMessage
    return ChatMessage(role="user", content=content)


# ── 循环注册表 ──

_REGISTRY: Dict[str, Type[PetReactLoop]] = {
    DirectLoop.name: DirectLoop,
    CustomLoop.name: CustomLoop,
}


def register_loop(loop_cls: Type[PetReactLoop]) -> Type[PetReactLoop]:
    """注册自定义循环（用户设计好后在此挂载）"""
    _REGISTRY[loop_cls.name] = loop_cls
    return loop_cls


def build_react_loop(loop_type: str = "direct") -> PetReactLoop:
    """按 PetConfig.loop_type 实例化循环；未注册时回退直答"""
    cls = _REGISTRY.get(loop_type or "direct")
    if cls is None:
        logger.warning(f"[Pet] 未知循环类型 {loop_type!r}，回退 direct")
        cls = DirectLoop
    try:
        return cls()
    except Exception as e:
        logger.error(f"[Pet] 循环实例化失败 {cls.name}: {e}")
        return DirectLoop()
