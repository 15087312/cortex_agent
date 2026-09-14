"""CustomLoop 测试 — 桌宠专属 ReAct 循环（8k/512/思考间隔/独立工具管控）

覆盖：纯文本回复、思考间隔节流、工具调用（只返回工具名）、白名单拦截、
缺参引导补齐、tools_enabled 开关、max_tokens 透传、注册表挂载。
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.desktop_pet.pet_engine import PetEngine
from modules.desktop_pet.react_loop import CustomLoop, build_react_loop


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    old = PetEngine._instance
    PetEngine._instance = None
    yield
    PetEngine._instance = old


@pytest.fixture(autouse=True)
def _pet_db(tmp_path, monkeypatch):
    """隔离桌宠独立存储"""
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_test.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    yield store
    store_mod.PetStore._instance = None


def _engine(monkeypatch, cfg_patch=None):
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore.get_instance()
    patch = {"loop_type": "custom"}
    if cfg_patch:
        patch.update(cfg_patch)
    store.update_config(patch)
    e = PetEngine.__new__(PetEngine)
    e._client = None
    e._client_cfg = None
    e._event_bus = None
    e._sub_id = ""
    e._running = False
    e._reply_listeners = []
    e.last_reply = None
    e._loop = None
    # 替换独立 client（只读 property 的缓存槽位；fingerprint 与默认空配置一致）
    e._client = _FakeClient()
    e._client_cfg = ("", "", "", "", "")
    return e


class _FakeClient:
    """返回预设内容的流式 client（on_token 同步回放收集）"""

    def __init__(self, outputs=None):
        self.outputs = list(outputs or ["你好呀"])
        self.calls = []

    async def chat_stream(self, messages=None, max_tokens=None, on_token=None, **kw):
        self.calls.append({"messages": messages, "max_tokens": max_tokens, **kw})
        content = self.outputs.pop(0) if self.outputs else ""
        collected = []
        for ch in content:
            collected.append(ch)
            if on_token:
                on_token(ch)
        return SimpleNamespace(message=SimpleNamespace(content="".join(collected)))


class _ToolCallsClient(_FakeClient):
    """模拟原生 tool_calls 协议的 client：首个响应返回 tool_calls，后续返回文本"""

    def __init__(self, tool_name="calc", arguments='{"expr":"1+1"}', tool_id="call_1", outputs=None):
        super().__init__(outputs=outputs)
        self.tool_name = tool_name
        self.arguments = arguments
        self.tool_id = tool_id

    async def chat_stream(self, messages=None, max_tokens=None, on_token=None, **kw):
        if not getattr(self, "_used_tool", False):
            self._used_tool = True
            self.calls.append({"messages": messages, "max_tokens": max_tokens, **kw})
            return SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[SimpleNamespace(id=self.tool_id, name=self.tool_name, arguments=self.arguments)],
                ),
                finish_reason="tool_calls",
            )
        return await super().chat_stream(messages=messages, max_tokens=max_tokens, on_token=on_token, **kw)


def _patch_tool_manager(monkeypatch, tools=None, results=None):
    """替换 ToolManager：get_tool_info / list_available_tools / call_tool"""
    import importlib
    tm_mod = importlib.import_module("infra.tool_manager.tool_manager")
    tools = tools or {
        "calc": {"name": "calc", "description": "计算器", "params": {"expr": "表达式"}},
        "noarg": {"name": "noarg", "description": "无参工具", "params": {}},
    }
    results = results or {
        "calc": {"success": True, "result": "2"},
        "noarg": {"success": True, "result": "ok"},
    }
    fake = MagicMock()
    fake.get_tool_info.side_effect = lambda n: tools.get(n)
    fake.list_available_tools.return_value = tools
    fake.call_tool = AsyncMock(side_effect=lambda n, p, caller_role="": results.get(n))
    monkeypatch.setattr(tm_mod, "ToolManager", lambda: fake)
    return fake


# ── 注册表 / 基础 ─────────────────────────────────────────────────────────

def test_custom_loop_registered():
    loop = build_react_loop("custom")
    assert isinstance(loop, CustomLoop)
    assert loop.name == "custom"


def test_unknown_loop_falls_back_to_direct():
    loop = build_react_loop("nope")
    assert loop.name == "direct"


# ── 纯文本回复 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plain_text_reply(monkeypatch, _pet_db):
    e = _engine(monkeypatch)
    loop = CustomLoop()
    got_msgs = []
    reply = await loop.run(e, "今天天气怎么样", on_message=lambda m: got_msgs.append(m))
    assert reply == "你好呀"
    # 持续循环：文本产出后仍继续思考，直到连续两轮空输出停止
    assert got_msgs == ["你好呀"]


@pytest.mark.asyncio
async def test_repeated_output_stops_loop(monkeypatch, _pet_db):
    """重复护栏：模型陷入复述（本轮与上一轮产出完全相同）→ 不再上报，提前终止

    旧行为会无限重复落库同一句话，页面历史被同一回复刷屏。
    """
    e = _engine(monkeypatch)
    e._client = _FakeClient(["固定的话", "固定的话", "固定的话", "新的话"])
    got_msgs = []
    loop = CustomLoop()
    reply = await loop.run(e, "hi", on_message=lambda m: got_msgs.append(m))
    # 第一个「固定的话」正常上报；第二个重复 → 终止且不上报，「新的话」永远不被消费
    assert got_msgs == ["固定的话"]
    assert reply == "固定的话"
    # 模型只被调用了两次（第二次复述后即终止）
    assert len(e.client.calls) == 2


# ── 思考间隔（从上次思考开始时刻起算） ────────────────────────────────────

@pytest.mark.asyncio
async def test_think_interval_throttles_second_round(monkeypatch, _pet_db):
    e = _engine(monkeypatch, {"min_think_interval": 0.3})
    loop = CustomLoop()
    t0 = time.time()
    await loop.run(e, "hi")
    await loop.run(e, "hi again")
    elapsed = time.time() - t0
    # 第二次 run 因距上次思考开始 < 0.3s 而等待
    assert elapsed >= 0.25


@pytest.mark.asyncio
async def test_think_interval_zero_skips_wait(monkeypatch, _pet_db):
    e = _engine(monkeypatch, {"min_think_interval": 0})
    loop = CustomLoop()
    t0 = time.time()
    await loop.run(e, "hi")
    await loop.run(e, "hi again")
    assert time.time() - t0 < 0.2


# ── 工具调用（只返回工具名） ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_call_then_text(monkeypatch, _pet_db):
    fake = _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient([
        '{"tool":"calc","params":{"expr":"1+1"}}',
        "结果是 2",
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "1+1 等于几")
    assert reply == "结果是 2"
    # 工具真的被执行（caller_role=pet）
    assert fake.call_tool.called
    assert fake.call_tool.call_args.args[0] == "calc"


@pytest.mark.asyncio
async def test_tool_whitelist_rejects_dangerous(monkeypatch, _pet_db):
    fake = _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient([
        '{"tool":"exec_command","params":{"cmd":"rm -rf /"}}',
        "我不能执行这个",
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "帮我执行命令")
    assert reply == "我不能执行这个"
    # exec_command 不在白名单 → 不触达 ToolManager
    assert not fake.call_tool.called


@pytest.mark.asyncio
async def test_tool_missing_params_guides_next_round(monkeypatch, _pet_db):
    fake = _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient([
        '{"tool":"calc"}',                          # 只给工具名，无 params
        '{"tool":"calc","params":{"expr":"2*3"}}',  # 观察引导后补齐
        "等于 6",
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "2*3 等于几")
    assert reply == "等于 6"
    assert fake.call_tool.call_args.args[0] == "calc"


@pytest.mark.asyncio
async def test_tool_no_params_runs_directly(monkeypatch, _pet_db):
    monkeypatch.setattr(CustomLoop, "PET_TOOL_WHITELIST", CustomLoop.PET_TOOL_WHITELIST | {"noarg"})
    fake = _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient([
        '{"tool":"noarg"}',
        "完成",
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "执行 noarg")
    assert reply == "完成"
    assert fake.call_tool.called


# ── tools_enabled 开关 / max_tokens ───────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_disabled_rejects_tool_output(monkeypatch, _pet_db):
    _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch, {"tools_enabled": False})
    e._client = _FakeClient([
        '{"tool":"calc","params":{"expr":"1+1"}}',
        "工具已关闭，我直接回答",
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "1+1")
    assert reply == "工具已关闭，我直接回答"


@pytest.mark.asyncio
async def test_max_tokens_passed_to_client(monkeypatch, _pet_db):
    e = _engine(monkeypatch, {"max_output_tokens": 512})
    loop = CustomLoop()
    await loop.run(e, "hi")
    assert e.client.calls[0]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_bracket_tool_syntax(monkeypatch, _pet_db):
    monkeypatch.setattr(CustomLoop, "PET_TOOL_WHITELIST", CustomLoop.PET_TOOL_WHITELIST | {"noarg"})
    fake = _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient([
        "[tool: noarg]",
        "算完了",
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "算一下")
    assert reply == "算完了"
    assert fake.call_tool.called


# ── 边界：解析 / 观察截断 / 工具清单降级 ──────────────────────────────────

def test_parse_tool_call_json():
    loop = CustomLoop()
    assert loop._parse_tool_call('{"tool":"calc","params":{"expr":"1"}}')["tool"] == "calc"
    assert loop._parse_tool_call('{"tool":"calc","params":{"expr":"1"}}')["params"] == {"expr": "1"}
    assert loop._parse_tool_call('{"tool":"noarg"}')["params"] == {}
    assert loop._parse_tool_call("[tool: web_search]")["tool"] == "web_search"
    assert loop._parse_tool_call("直接回答") is None
    assert loop._parse_tool_call('{"foo":1}') is None
    assert loop._parse_tool_call("not json at all") is None


def test_observe_truncates_800():
    long = "x" * 2000
    out = CustomLoop._observe(long)
    assert len(out) == 801 and out.endswith("…")
    assert CustomLoop._observe("  short  ") == "short"
    assert CustomLoop._observe("") == ""


def test_fmt_result_truncates():
    big = {"success": True, "result": "y" * 1500}
    out = CustomLoop._fmt_result("t", big)
    assert len(out) <= 810
    assert out.startswith("[t 结果]")


def test_tool_listing_falls_back_empty(monkeypatch):
    import importlib
    tm_mod = importlib.import_module("infra.tool_manager.tool_manager")
    monkeypatch.setattr(tm_mod, "ToolManager", lambda: (_ for _ in ()).throw(RuntimeError("no tools")))
    assert CustomLoop._tool_listing() == ""


# ── 原生 tools 协议（完整 OpenAI tools 数组 + role=tool 观察） ─────────

def test_native_tools_builds_full_openai_schema(monkeypatch):
    """_native_tools 生成完整 OpenAI tools 数组：name/description/JSON Schema 参数"""
    import importlib
    tm_mod = importlib.import_module("infra.tool_manager.tool_manager")
    fake = MagicMock()
    fake.list_available_tools.return_value = {
        "calc": {
            "description": "计算器，支持四则运算\n支持函数",
            "params": {"expr": {"type": "string", "description": "表达式"}},
        },
    }
    monkeypatch.setattr(tm_mod, "ToolManager", lambda: fake)
    tools = CustomLoop._native_tools()
    assert len(tools) == 1
    t = tools[0]
    assert t["type"] == "function"
    assert t["function"]["name"] == "calc"
    assert t["function"]["description"].startswith("计算器")
    props = t["function"]["parameters"]["properties"]
    assert props["expr"]["type"] == "string"
    assert props["expr"]["description"] == "表达式"


def test_native_tools_empty_when_no_tools(monkeypatch):
    """工具库不可用 / 白名单内无已注册工具 → 返回 []，调用方降级文本协议"""
    import importlib
    tm_mod = importlib.import_module("infra.tool_manager.tool_manager")
    monkeypatch.setattr(tm_mod, "ToolManager", lambda: (_ for _ in ()).throw(RuntimeError("no tools")))
    assert CustomLoop._native_tools() == []
    fake = MagicMock()
    fake.list_available_tools.return_value = {"unrelated_tool": {"description": "x", "params": {}}}
    monkeypatch.setattr(tm_mod, "ToolManager", lambda: fake)
    assert CustomLoop._native_tools() == []


@pytest.mark.asyncio
async def test_native_tools_passed_to_chat_stream(monkeypatch, _pet_db):
    """工具启用时完整 tools 数组透传给 chat_stream（不再是文本工具名清单）"""
    fake = _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    loop = CustomLoop()
    await loop.run(e, "你好")
    tools_arg = e.client.calls[0].get("tools")
    assert tools_arg, "tools 数组应透传给 chat_stream"
    names = [t["function"]["name"] for t in tools_arg]
    assert "calc" in names
    # 完整 schema：function.parameters.properties 存在
    calc_tool = next(t for t in tools_arg if t["function"]["name"] == "calc")
    assert "expr" in calc_tool["function"]["parameters"]["properties"]
    # 不再注入文本工具清单（清单只作为无 schema 时的兑底）
    text_dump = "\n".join(m.content or "" for m in e.client.calls[0]["messages"])
    assert "【可用工具】" not in text_dump
    assert fake.list_available_tools.called


@pytest.mark.asyncio
async def test_native_tool_call_roundtrip(monkeypatch, _pet_db):
    """原生 tool_calls 响应：执行工具 + role=tool 观察回填（带 tool_call_id）+ 继续思考"""
    _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _ToolCallsClient(outputs=["算好了，1加1等于2"])
    loop = CustomLoop()
    reply = await loop.run(e, "1+1等于几")
    assert reply == "算好了，1加1等于2"
    # 第二次调用（工具执行后的思考）应包含 role=tool 观察消息，且关联 tool_call_id
    second = e.client.calls[1]
    tool_msgs = [m for m in second["messages"] if getattr(m, "role", "") == "tool"]
    assert tool_msgs, "工具观察应以 role=tool 消息回填"
    assert tool_msgs[0].tool_call_id == "call_1"
    assert "calc" in tool_msgs[0].content and "2" in tool_msgs[0].content  # 观察内容包含工具结果


@pytest.mark.asyncio
async def test_native_tools_fallback_to_text_listing(monkeypatch, _pet_db):
    """无 schema 可用时降级文本协议：注入文本清单，模型仍可用 JSON 工具名格式"""
    monkeypatch.setattr(CustomLoop, "PET_TOOL_WHITELIST", CustomLoop.PET_TOOL_WHITELIST | {"noarg"})
    _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    # 让 _native_tools 失败 → 降级
    import importlib
    tm_mod = importlib.import_module("infra.tool_manager.tool_manager")
    real_tm = tm_mod.ToolManager

    def _boom():
        raise RuntimeError("no tools")

    # _tool_listing 内部也用 ToolManager；mock list_available_tools 抛错前的行为不可行，
    # 直接把 _native_tools 打桩为空
    import modules.desktop_pet.react_loop as rl
    monkeypatch.setattr(CustomLoop, "_native_tools", classmethod(lambda cls: []))
    e._client = _FakeClient(["你好呀"])
    loop = CustomLoop()
    await loop.run(e, "你好")
    text_dump = "\n".join(m.content or "" for m in e.client.calls[0]["messages"])
    assert "【可用工具】" in text_dump  # 降级后走文本清单


# ── 循环边界：轮数耗尽 / 空回复 / on_token ────────────────────────────────

@pytest.mark.asyncio
async def test_loop_rounds_exhausted_uses_last_text(monkeypatch, _pet_db):
    _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch, {"max_loop_rounds": 2})
    e._client = _FakeClient([
        '{"tool":"calc","params":{"expr":"1"}}',  # 第 1 轮：工具
        '{"tool":"calc","params":{"expr":"2"}}',  # 第 2 轮：工具（轮数耗尽）
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "hi")
    assert reply == ""  # 无文本可兜底


@pytest.mark.asyncio
async def test_loop_rounds_exhausted_last_text_fallback(monkeypatch, _pet_db):
    monkeypatch.setattr(CustomLoop, "PET_TOOL_WHITELIST", CustomLoop.PET_TOOL_WHITELIST | {"noarg"})
    _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch, {"max_loop_rounds": 2})
    e._client = _FakeClient([
        '{"tool":"noarg"}',  # 工具（有观察）
        "最后一轮直接回答",  # 第 2 轮文本
    ])
    loop = CustomLoop()
    reply = await loop.run(e, "hi")
    assert reply == "最后一轮直接回答"


@pytest.mark.asyncio
async def test_empty_output_returns_empty(monkeypatch, _pet_db):
    e = _engine(monkeypatch)
    e._client = _FakeClient([""])
    loop = CustomLoop()
    assert await loop.run(e, "hi") == ""


@pytest.mark.asyncio
async def test_on_token_receives_full_reply(monkeypatch, _pet_db):
    e = _engine(monkeypatch)
    e._client = _FakeClient(["你好呀"])
    tokens = []
    loop = CustomLoop()
    reply = await loop.run(e, "hi", on_token=lambda t: tokens.append(t))
    assert reply == "你好呀"
    assert "".join(tokens) == "你好呀"


# ── 流式粒度：文本逐片段转发 / 工具轮不转发 ──────────────────────────────

@pytest.mark.asyncio
async def test_on_token_streams_by_chunk(monkeypatch, _pet_db):
    """文本轮：on_token 收到与模型回调一致的多个片段，而非一次性整段"""
    e = _engine(monkeypatch)
    e._client = _FakeClient(["你好呀"])
    tokens = []
    loop = CustomLoop()
    reply = await loop.run(e, "hi", on_token=lambda t: tokens.append(t))
    assert reply == "你好呀"
    # 逐片段（FakeClient 逐字符回调 → 重放同粒度），非一次性
    assert len(tokens) >= 2
    assert "".join(tokens) == "你好呀"


@pytest.mark.asyncio
async def test_on_token_skips_tool_round(monkeypatch, _pet_db):
    """工具轮：模型输出工具 JSON 时不得把片段流给前端 on_token"""
    _patch_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient([
        '{"tool":"calc","params":{"expr":"1+1"}}',  # 工具轮
        "最终答案 2",
    ])
    tokens = []
    loop = CustomLoop()
    reply = await loop.run(e, "hi", on_token=lambda t: tokens.append(t))
    assert reply == "最终答案 2"
    assert "".join(tokens) == "最终答案 2"
    assert '{"tool"' not in "".join(tokens)


# ── 思考过程透传 ──────────────────────────────────────────────────────────

class _ReasoningClient:
    """输出 reasoning 片段 + 正文文本的 client（仅第一轮有输出，后续空）"""

    def __init__(self, reasoning: str, text: str):
        self.reasoning, self.text = reasoning, text
        self._done = False

    async def chat_stream(self, messages=None, max_tokens=None, on_token=None, on_reasoning=None, **kw):
        if on_reasoning and not self._done:
            on_reasoning(self.reasoning)
        if self._done:
            return SimpleNamespace(message=SimpleNamespace(content=""))
        self._done = True
        for ch in self.text:
            if on_token:
                on_token(ch)
        return SimpleNamespace(message=SimpleNamespace(content=self.text))


@pytest.mark.asyncio
async def test_on_reasoning_forwarded(monkeypatch, _pet_db):
    """思考过程逐段回调给 on_reasoning，正文仍逐段给 on_token"""
    e = _engine(monkeypatch)
    e._client = _ReasoningClient("先判断一下再回答", "答案")
    got_reason = []
    got_tokens = []
    loop = CustomLoop()
    reply = await loop.run(e, "hi", on_token=lambda t: got_tokens.append(t), on_reasoning=lambda r: got_reason.append(r))
    assert reply == "答案"
    assert "".join(got_reason) == "先判断一下再回答"
    assert "".join(got_tokens) == "答案"


# ── 持续思考循环（用户设计：每次思考之后再次思考） ──────────────────────

@pytest.mark.asyncio
async def test_continuous_loop_keeps_thinking_after_text(monkeypatch, _pet_db):
    """文本输出后不停止，继续下一轮思考；所有轮次产出都经 on_message 上报"""
    e = _engine(monkeypatch)
    e._client = _FakeClient(["第一轮", "第二轮", "第三轮"])
    got_msgs = []
    loop = CustomLoop()
    reply = await loop.run(e, "hi", on_message=got_msgs.append)
    assert got_msgs == ["第一轮", "第二轮", "第三轮"]
    assert reply == "第一轮\n第二轮\n第三轮"


@pytest.mark.asyncio
async def test_continuous_loop_rounds_cap(monkeypatch, _pet_db):
    """max_loop_rounds>0 时达到轮数即停（配置化上限）"""
    e = _engine(monkeypatch, {"max_loop_rounds": 2})
    e._client = _FakeClient(["a", "b", "c", "d"])
    got = []
    loop = CustomLoop()
    reply = await loop.run(e, "hi", on_message=got.append)
    assert got == ["a", "b"]
    assert reply == "a\nb"


@pytest.mark.asyncio
async def test_interval_applies_between_rounds(monkeypatch, _pet_db):
    """间隔从前一次思考开始时刻起算：循环内每两轮之间至少间隔 min_think_interval"""
    e = _engine(monkeypatch, {"min_think_interval": 0.3})
    e._client = _FakeClient(["r1", "r2"])
    loop = CustomLoop()
    t0 = time.time()
    await loop.run(e, "hi")
    assert time.time() - t0 >= 0.25


@pytest.mark.asyncio
async def test_continuous_loop_tool_then_text_keeps_going(monkeypatch, _pet_db):
    """工具轮后文本轮，文本轮后仍继续（工具→文本→文本）"""
    e = _engine(monkeypatch)
    _patch_tool_manager(monkeypatch, results={"calc": "42"})
    e._client = _FakeClient(['{"tool": "calc", "params": {"expr": "1+1"}}', "结果是 42", "还需要别的吗"])
    got = []
    loop = CustomLoop()
    reply = await loop.run(e, "算一下", on_message=got.append)
    assert got == ["结果是 42", "还需要别的吗"]
    assert reply == "结果是 42\n还需要别的吗"


# ── DirectLoop（默认直答循环：流式 kwargs / 思考过程 / 持久化） ──────────

class _FakeBothClient(_FakeClient):
    """同时支持 chat（非流式）与 chat_stream（流式）"""

    async def chat(self, messages=None, **kw):
        content = self.outputs.pop(0) if self.outputs else ""
        return SimpleNamespace(message=SimpleNamespace(content=content))


@pytest.mark.asyncio
async def test_direct_loop_streaming_accepts_engine_kwargs(monkeypatch, _pet_db):
    """流式对话：engine._stream_task 会传 on_token/on_reasoning/on_message，
    DirectLoop 必须接受这些 kwargs（回归：签名缺失 → TypeError → 流式必失败）"""
    from modules.desktop_pet.react_loop import DirectLoop
    e = _engine(monkeypatch)
    e._client = _FakeClient(["你好呀"])
    got_tokens, got_msgs = [], []
    loop = DirectLoop()
    reply = await loop.run(
        e, "hi",
        on_token=lambda t: got_tokens.append(t),
        on_reasoning=lambda r: None,
        on_message=lambda m: got_msgs.append(m),
    )
    assert reply == "你好呀"
    assert "".join(got_tokens) == "你好呀"
    assert got_msgs == ["你好呀"]


@pytest.mark.asyncio
async def test_direct_loop_streaming_forwards_reasoning(monkeypatch, _pet_db):
    """思考过程逐段回调给 on_reasoning（与 CustomLoop 一致，前端 thinking 帧来源）"""
    from modules.desktop_pet.react_loop import DirectLoop
    e = _engine(monkeypatch)
    e._client = _ReasoningClient("先想一想", "答案")
    got_reason, got_msgs = [], []
    loop = DirectLoop()
    reply = await loop.run(
        e, "hi",
        on_token=lambda t: None,
        on_reasoning=lambda r: got_reason.append(r),
        on_message=lambda m: got_msgs.append(m),
    )
    assert reply == "答案"
    assert "".join(got_reason) == "先想一想"
    assert got_msgs == ["答案"]


@pytest.mark.asyncio
async def test_direct_loop_engine_chat_saves_once(monkeypatch, _pet_db):
    """非流式 engine.chat 路径：历史只落一次 user+assistant
    （DirectLoop.run 不再自己 save_pair，避免与 engine.chat 双重落库）"""
    import modules.desktop_pet.store as store_mod
    store_mod.PetStore.get_instance().update_config({"loop_type": "direct"})
    from modules.desktop_pet.react_loop import DirectLoop
    e = _engine(monkeypatch)
    e._loop = DirectLoop()
    e._client = _FakeBothClient(["回复"])
    reply = await e.chat("问")
    assert reply == "回复"
    hist = _pet_db.get_history(limit=10)
    assert [m["content"] for m in hist] == ["问", "回复"]


# ── DirectLoop 持续思考（连续对话模式：rounds>0 时每轮直接流式产出） ──

@pytest.mark.asyncio
async def test_direct_loop_rounds_positive_streams_and_reports_each_round(monkeypatch, _pet_db):
    """direct + max_loop_rounds>0（连续对话模式）：不预生成完整文本再回放，
    token 逐个实时回调，每轮文本产出即经 on_message 上报（落库 + 前端轮次推进）"""
    from modules.desktop_pet.react_loop import DirectLoop
    import modules.desktop_pet.store as store_mod
    store_mod.PetStore.get_instance().update_config({"loop_type": "direct", "max_loop_rounds": 1})
    e = _engine(monkeypatch)
    e._client = _FakeClient(["你好呀"])
    got_tokens, got_msgs = [], []
    loop = DirectLoop()
    reply = await loop.run(
        e, "hi",
        on_token=lambda t: got_tokens.append(t),
        on_reasoning=lambda r: None,
        on_message=lambda m: got_msgs.append(m),
    )
    assert reply == "你好呀"
    assert "".join(got_tokens) == "你好呀"
    assert got_msgs == ["你好呀"]

