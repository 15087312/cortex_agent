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
        """单轮 ReAct：一次完整 think→tool→observe→answer，不计时不循环"""
        cfg = engine.config
        max_tokens = max(1, int(cfg.get("max_output_tokens") or 512))
        tools_enabled = bool(cfg.get("tools_enabled", True))

        context = await engine.build_context(text)
        combined = "\n\n".join(x for x in [context, extra_system] if x)
        messages = engine.build_messages(text, extra_system=combined)
        native_tools = CustomLoop._native_tools() if tools_enabled else []
        if tools_enabled:
            listing = CustomLoop._tool_listing()
            if listing:
                messages.append(_tool_message(listing))

        loop = CustomLoop()
        reply = await loop._react_cycle(
            engine, messages, max_tokens, native_tools, tools_enabled,
            on_token, on_reasoning, None,
        )
        if reply:
            if on_message is not None:
                on_message(reply)
            logger.info(f"[Pet] 对话: {text[:40]} → {reply[:40]}")
        return reply


class CustomLoop(PetReactLoop):
    """桌宠专属 ReAct 循环（用户设计的规格落地）

    设计规格（8k 本地模型约束下）：
    - 模型：本地 8k 上下文，单次思考最大输出 512 token（max_output_tokens）。
    - 思考间隔（min_think_interval）：每轮思考结束后的静默时间，
      从「前一次思考结束」时刻起算（now - last_think_end >= 间隔）。
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

    # 空拍退避默认倍率上限（参考 Cortico Heartbeat）：连续空拍间隔按 2^n 增长，到上限封顶。
    # 可通过配置 idle_backoff_max 覆盖（≥1，设 1 = 关闭退避）；循环永不因空拍终止。
    IDLE_BACKOFF_MAX_DEFAULT = 8

    @staticmethod
    def _idle_backoff_mult(empty_streak: int, cap: float = IDLE_BACKOFF_MAX_DEFAULT) -> float:
        """空拍退避倍率：连续空输出 n 次 → 2^n（封顶 cap）。

        任意真实产出（正文/工具调用）复位为 1；用户发消息会开新循环，同样复位。
        cap <= 1 时恒为 1（退避关闭）。
        """
        cap = max(1.0, float(cap))
        # 指数先钉顶再取幂：2.0**1024 会 OverflowError（先乘后 min 挡不住），
        # 永不终止循环下空拍计数无上界，必须防溢出。2^60 已远超任何倍率上限。
        exp = min(max(0, int(empty_streak)), 60)
        return min(2.0 ** exp, cap)
    # 桌宠工具白名单（基础工具复用项目 ToolManager，只开放轻量查询/操作）
    PET_TOOL_WHITELIST = {
        "web_search", "web_fetch",
        "calc", "calc_advanced", "calc_sum", "calc_avg",
        "todo", "open_app", "list_directory",
        # 感知工具（已注册在 infra/tool_manager/tools/perception_tools.py）
        "understand_screen", "detect_ui_elements", "transcribe_audio",
        # 环境查询
        "list_windows", "get_mouse_position",
        # 记忆回忆
        "event_query",
    }

    _TOOL_JSON_RE = re.compile(r"\{\s*\"tool\"\s*:\s*\"([^\"}]+)\"", re.S)
    _TOOL_BRACKET_RE = re.compile(r"\[tool\s*:\s*([A-Za-z0-9_\-]+)\s*\]", re.I)

    def __init__(self):
        self._last_think_end = 0.0  # 上次思考结束时刻（跨 chat 生效）

    # ── 主入口 ──

    # @link:block PetReactCycle = Loop(Think · (Tool · Observe | Emit))
    async def _react_cycle(
        self,
        engine: "PetEngine",
        messages: list,
        max_tokens: int,
        native_tools: list,
        tools_enabled: bool,
        on_token: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
        max_steps: int = 10,
    ) -> str:
        """标准 ReAct 单轮：think → tool → observe → think → … → 纯文本答案返回。

        工具轮之间不等待，快速连续；拿到纯文本（非工具调用）即返回本轮答案。
        空输出直接继续（cycle 内不等，由外层轮间计时控制）。
        返回本轮文本答案（空串表示本轮无产出）。
        """
        from infra.model.base_model import ChatMessage

        obs = ""
        for step in range(1, max_steps + 1):
            # 上下文 8k 预算保护（首步必试，避免 mock/短上下文误杀）
            if step > 1 and self._context_overflow(
                messages,
                extra_chars=len(json.dumps(native_tools, ensure_ascii=False)) if native_tools else 0,
            ):
                logger.info("[Pet] ReAct 内上下文接近 8k 上限，结束本轮")
                break
            # @link:block PetThink = Think
            out = await self._think(
                engine, messages, max_tokens, step, obs,
                on_token, on_reasoning, tools=native_tools,
            )
            if out.get("tool"):
                if tools_enabled and out["tool"] in self.PET_TOOL_WHITELIST:
                    # @link:block PetTool = Tool
                    obs = await self._run_tool(engine, out["tool"], out.get("params"))
                else:
                    obs = f"工具 {out.get('tool')} 不可用（未启用或不在桌宠白名单）"
                # @link:block PetObserve = Observe
                obs = self._observe(obs)
                if out.get("tool_call_id"):
                    messages.append(ChatMessage(role="tool", content=obs, tool_call_id=out["tool_call_id"]))
                else:
                    messages.append(_tool_message(f"【工具观察】{obs}"))
                # 工具轮的 on_message(obs) 是回调上报，调用名不在 Emit 原子证据表内；
                # 该出口已由外层 PetReactCycle 的 (Tool · Observe | Emit) 声明覆盖，
                # 这里不再单独起块名（同名块会触发 E005，且无证据触发 E002）。
                if on_message:
                    on_message(obs)
                continue  # 工具轮：不等待，立即下一 think
            text = (out.get("text") or "").strip()
            if text:
                messages.append(ChatMessage(role="assistant", content=text))
                # 不在这里 on_message：由外层 run() 做重复检查后再上报，避免复述也被持久化
                # @link:block PetReactEmit = Emit
                return text
            # 空输出：cycle 内直接继续
        return ""

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
        # 空拍退避上限（倍率）：配置项 idle_backoff_max，≥1；1 = 关闭退避
        try:
            backoff_cap = max(1.0, float(cfg.get("idle_backoff_max") or 1))
        except (TypeError, ValueError):
            backoff_cap = float(self.IDLE_BACKOFF_MAX_DEFAULT)
        tools_enabled = bool(cfg.get("tools_enabled", True))
        # max_loop_rounds=0（默认）= 持续思考循环：每次思考之后再次思考，
        # 直到被外部停止（SSE 断开/取消）、上下文接近 8k 上限。
        # 空输出不终止循环，触发空拍退避（×2/×4/… 封顶 idle_backoff_max）后继续等待新想法。
        rounds = int(cfg.get("max_loop_rounds") or 0)
        max_tokens = max(1, int(cfg.get("max_output_tokens") or 512))

        # 2. 上下文：人设 + 历史(8) + 独立记忆/感知 + 用户输入
        context = await engine.build_context(text)
        combined = "\n\n".join(x for x in [context, extra_system] if x)
        messages = engine.build_messages(text, extra_system=combined)
        # 工具描述：优先完整 OpenAI tools 数组（原生协议）；
        # 文本清单始终注入（兜底格式说明）：spark 等本地模型不支持原生 tool_calls，
        # 必须靠文本格式 {"tool":"名","params":{}} 触发；支持原生的模型两边都能用。
        native_tools = self._native_tools() if tools_enabled else []
        if tools_enabled:
            listing = self._tool_listing()
            if listing:
                messages.append(_tool_message(listing))

        # 3. 整轮循环：一轮完整 ReAct → 轮间计时 → 下一轮 ReAct……
        # max_loop_rounds=0（默认）= 无限轮，直到外部停止/上下文满/内容重复。
        # 空轮（无产出）不终止，触发空拍退避后继续。
        parts: list = []
        empty_streak = 0
        cycle = 0
        while True:
            cycle += 1
            if rounds and cycle > rounds:
                logger.info(f"[Pet] 达到 max_loop_rounds={rounds}，停止思考循环")
                break
            if self._context_overflow(messages, extra_chars=len(json.dumps(native_tools, ensure_ascii=False)) if native_tools else 0):
                logger.info("[Pet] 上下文接近 8k 上限，停止思考循环")
                break
            # 轮间计时：从上轮「结束」起算 min_think_interval（空拍 ×退避，封顶 idle_backoff_max）。
            # 从结束时刻起算才能保证每轮之间真的有静默间隔；若从开始时刻起算，
            # 单轮耗时超过间隔时（本地思考模型常见）等待恒为 0 → 看起来在「一直思考」，
            # 间隔设置形同失效。interval=0 且已有空拍时用 1s 地板，避免空转热循环
            if interval > 0 or empty_streak > 0:
                base = interval if interval > 0 else 1.0
                backoff = self._idle_backoff_mult(empty_streak, backoff_cap)
                wait = self._last_think_end + base * backoff - time.time()
                if wait > 0:
                    label = f"空拍×{backoff:g}" if backoff > 1 else "正常"
                    logger.info(f"[Pet] 轮间等待（{label}），{wait:.1f}s 后开始第{cycle}轮")
                    await asyncio.sleep(wait)
            # 一轮完整 ReAct（内含 think→tool→observe 微循环，无等待）
            answer = await self._react_cycle(
                engine, messages, max_tokens, native_tools, tools_enabled,
                on_token, on_reasoning, on_message,
            )
            self._last_think_end = time.time()
            if not answer:
                empty_streak += 1
                logger.info(
                    f"[Pet] 第{cycle}轮无产出（空拍第{empty_streak}次），"
                    f"下次间隔 ×{self._idle_backoff_mult(empty_streak, backoff_cap):g}"
                )
                continue
            # 重复护栏：与上一轮答案完全相同 → 模型陷入复述，终止
            if parts and answer == parts[-1]:
                logger.info("[Pet] 本轮答案与上一轮重复，停止思考循环")
                break
            empty_streak = 0
            parts.append(answer)
            if on_message:
                on_message(answer)
            # 汇总到 SSE 层用于 round_complete 语意检测（本轮完整回复已产出）
            if on_token:
                on_token("\x00ROUND_DONE\x00")
        # 汇总全部轮次产出（落库由 engine 层 on_message 通道完成，见 PetPersist）
        reply = "\n".join(parts)
        if reply:
            logger.info(f"[Pet] Custom 循环完成: {text[:30]} → {reply[:60]}")
        # @link:block PetEmit = Emit
        return reply

    @staticmethod
    def _context_overflow(messages, budget_chars: int = 20000, extra_chars: int = 0) -> bool:
        """上下文预算：对话累计字符数 + 额外开销（tools schema）粗估超限即停

        20000 字符≈13000 token（中文 1.5 token/字），适配 DeepSeek 等大上下文 API。
        本地 8k 小模型场景可调小。schema 不随轮次增长，只计一次。
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
            # 观察已由 run() 回填进 messages（原生 role=tool / 文本 user【工具观察】）；
            # 这里只在两者都缺失时补一条，避免重复
            recent = messages[-2:] if len(messages) >= 2 else messages
            has_tool = any(getattr(m, "role", "") == "tool" for m in recent)
            has_obs_text = any("【工具观察】" in (getattr(m, "content", "") or "") for m in recent)
            if not has_tool and not has_obs_text:
                msgs.append(_tool_message(f"【工具观察】{obs}"))
        collected: list = []
        # 正文 token 实时转发到前端（与 DirectLoop 一致），边生成边显示。
        # 若最终判定为工具调用，JSON 片段已可见但下一轮会续接同一气泡，
        # 体验远优于"等半天突然冒出一整句"。
        response = await engine.client.chat_stream(
            messages=msgs,
            max_tokens=max_tokens,
            on_token=lambda t: (collected.append(t), on_token(t) if on_token else None),
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
            from infra.model.base_model import ChatMessage
            tc = response.message.tool_calls[0]
            params: Dict = {}
            try:
                parsed = json.loads(tc.arguments) if tc.arguments else {}
                if isinstance(parsed, dict):
                    params = parsed
            except Exception:
                params = {}
            # OpenAI 兼容协议要求 role="tool" 必须紧跟一条带 tool_calls 的
            # assistant 消息，否则下一轮请求会被 400 拒绝（桌宠表现为一直
            # 收不到回复）。这里回填进真实历史 messages（msgs 只是本轮请求副本）。
            messages.append(ChatMessage(
                role="assistant",
                content=raw or None,
                tool_calls=[tc],
            ))
            return {"tool": tc.name, "params": params, "tool_call_id": tc.id, "text": ""}
        if not raw:
            return {"text": ""}
        # 解析：JSON 工具调用 / [tool: name] / 纯文本
        parsed = self._parse_tool_call(raw)
        if parsed:
            # 工具轮：token 已实时转发（JSON 片段可见），下一轮续接同一气泡
            return parsed
        # 纯文本：token 已在 chat_stream 中实时转发，无需重放
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
            except Exception as _swallowed_err:
                logger.debug(f"[_parse_tool_call] ignored Exception: {_swallowed_err}")
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
