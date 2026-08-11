"""config/providers 格式层测试（接线 infra/model 后首次有专项覆盖）

覆盖：
- registry URL 推断 / 显式格式选择
- 三格式 build_request（openai top_p / anthropic system 提取 / dashscope input）
- chat_url 归一化（/chat/completions、/messages、dashscope 原样）
- parse_response（openai reasoning_content、anthropic content blocks、dashscope choices/text）
"""
import pytest

from config.providers.registry import ProviderRegistry, get_provider
from config.providers.openai import OpenAIProvider
from config.providers.anthropic import AnthropicProvider
from config.providers.dashscope import DashScopeProvider

_registry = ProviderRegistry()


# ── registry：URL 推断 ─────────────────────────────────────────────────────

def test_registry_detects_dashscope():
    assert _registry.get_provider_class("https://dashscope.aliyuncs.com/api/v1") is DashScopeProvider
    assert _registry.get_provider_class("https://modelscope.cn/api/v1") is DashScopeProvider


def test_registry_detects_anthropic():
    assert _registry.get_provider_class("https://api.anthropic.com/v1") is AnthropicProvider
    assert _registry.get_provider_class("https://api.claude.ai") is AnthropicProvider


def test_registry_defaults_openai():
    assert _registry.get_provider_class("https://api.deepseek.com/v1") is OpenAIProvider
    assert _registry.get_provider_class("") is OpenAIProvider


def test_registry_explicit_format_overrides_url():
    # 显式格式优先于 URL 推断
    assert _registry.get_provider_class_by_format("anthropic") is AnthropicProvider
    assert _registry.get_provider_class_by_format("dashscope") is DashScopeProvider
    assert _registry.get_provider_class_by_format("openai") is OpenAIProvider
    assert _registry.get_provider_class_by_format("") is None
    assert _registry.get_provider_class_by_format("unknown") is None


def test_get_provider_respects_explicit_format():
    p = get_provider("m", "k", "https://api.openai.com/v1", api_format="anthropic")
    assert isinstance(p, AnthropicProvider)


# ── chat_url 归一化（§26/§27 回归：消除 /v1 直接 404）────────────────────────

def test_openai_chat_url_normalizes():
    assert OpenAIProvider("k", "https://api.openai.com/v1", "m").chat_url() == "https://api.openai.com/v1/chat/completions"
    assert OpenAIProvider("k", "https://openrouter.ai/api/v1/chat/completions", "m").chat_url() == "https://openrouter.ai/api/v1/chat/completions"
    assert OpenAIProvider("k", "https://api.deepseek.com/v1", "m").chat_url() == "https://api.deepseek.com/v1/chat/completions"


def test_anthropic_chat_url_messages():
    assert AnthropicProvider("k", "https://api.anthropic.com/v1", "m").chat_url() == "https://api.anthropic.com/v1/messages"
    assert AnthropicProvider("k", "https://api.anthropic.com/v1/messages", "m").chat_url() == "https://api.anthropic.com/v1/messages"


def test_dashscope_chat_url_raw():
    assert DashScopeProvider("k", "https://dashscope.aliyuncs.com/api/v1/apps/x", "m").chat_url() == "https://dashscope.aliyuncs.com/api/v1/apps/x"


# ── build_request 三格式 ────────────────────────────────────────────────────

def test_openai_build_request_with_top_p():
    p = OpenAIProvider("k", "u", "model-x")
    body = p.build_request(
        [{"role": "user", "content": "hi"}],
        max_tokens=10, temperature=0.5, tools=[{"function": {"name": "t", "description": "d"}}],
        tool_choice="required", top_p=0.9,
    )
    assert body["model"] == "model-x"
    assert body["max_tokens"] == 10
    assert body["top_p"] == 0.9
    assert body["tools"][0]["function"]["name"] == "t"
    assert body["tool_choice"] == "required"


def test_anthropic_build_request_extracts_system():
    p = AnthropicProvider("k", "u", "claude-x")
    body = p.build_request(
        [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}],
        max_tokens=10, temperature=0.2,
    )
    assert body["system"] == "你是助手"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert "temperature" not in body  # anthropic 顶层无 temperature


def test_dashscope_build_request_wraps_input():
    p = DashScopeProvider("k", "u", "qwen-x")
    body = p.build_request(
        [{"role": "user", "content": "hi"}], max_tokens=10, temperature=0.1, stream=True,
    )
    assert body["input"]["messages"] == [{"role": "user", "content": "hi"}]
    assert body["parameters"]["max_tokens"] == 10
    assert body["stream"] is True


# ── parse_response 三格式 ───────────────────────────────────────────────────

def test_openai_parse_response_with_reasoning():
    p = OpenAIProvider("k", "u", "m")
    r = p.parse_response({
        "choices": [{"message": {"content": "答", "reasoning_content": "思考", "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })
    assert r["content"] == "答"
    assert r["reasoning_content"] == "思考"
    assert r["tool_calls"][0]["name"] == "t"
    assert r["finish_reason"] == "tool_calls"
    assert r["usage"]["prompt_tokens"] == 5


def test_anthropic_parse_response_blocks():
    p = AnthropicProvider("k", "u", "m")
    r = p.parse_response({
        "content": [{"type": "text", "text": "你好"}, {"type": "tool_use", "id": "t1", "name": "calc", "input": {"a": 1}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })
    assert r["content"] == "你好"
    assert r["tool_calls"][0]["name"] == "calc"
    assert r["finish_reason"] == "tool_calls"
    assert r["usage"]["prompt_tokens"] == 10


def test_dashscope_parse_response_choices():
    p = DashScopeProvider("k", "u", "m")
    r = p.parse_response({
        "output": {"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}]},
        "usage": {"total_tokens": 8},
    })
    assert r["content"] == "好"
    assert r["finish_reason"] == "stop"


def test_dashscope_parse_response_text_fallback():
    p = DashScopeProvider("k", "u", "m")
    r = p.parse_response({"output": {"text": "纯文本"}})
    assert r["content"] == "纯文本"


# ── stream 解析（单行）──────────────────────────────────────────────────────

def test_openai_stream_line():
    p = OpenAIProvider("k", "u", "m")
    assert p.parse_stream_line("data: {") is None  # 非 JSON
    r = p.parse_stream_line('data: {"choices":[{"delta":{"content":"加"}}]}')
    assert r == {"content": "加"}


def test_anthropic_stream_line():
    p = AnthropicProvider("k", "u", "m")
    r = p.parse_stream_line('data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"加"}}')
    assert r == {"content": "加"}
