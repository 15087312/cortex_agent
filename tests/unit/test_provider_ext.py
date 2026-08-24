"""测试：config/providers/azure.py / bedrock.py / cohere.py / ollama.py。"""
from config.providers.azure import AzureProvider
from config.providers.cohere import CohereProvider
from config.providers.ollama import OllamaProvider
from config.providers.bedrock import BedrockProvider


class TestAzure:
    def test_headers(self):
        p = AzureProvider("k", "u", "gpt-4o")
        assert p.build_headers()["api-key"] == "k"

    def test_chat_url_root(self):
        p = AzureProvider("k", "https://my-resource.openai.azure.com/openai/deployments/gpt-4o", "gpt-4o")
        assert p.chat_url() == \
            "https://my-resource.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-06-01"

    def test_chat_url_already_full(self):
        p = AzureProvider("k", "https://x.openai.azure.com/d/gpt-4o/chat/completions", "gpt-4o")
        assert p.chat_url() == \
            "https://x.openai.azure.com/d/gpt-4o/chat/completions?api-version=2024-06-01"

    def test_chat_url_with_query(self):
        p = AzureProvider("k", "https://x.openai.azure.com/d/gpt-4o?api-version=2023-03-01", "gpt-4o")
        assert p.chat_url() == "https://x.openai.azure.com/d/gpt-4o?api-version=2023-03-01"

    def test_build_request_openai_shape(self):
        p = AzureProvider("k", "u", "gpt-4o")
        body = p.build_request([{"role": "user", "content": "hi"}], 10, 0.5)
        assert body["model"] == "gpt-4o"
        assert body["messages"] == [{"role": "user", "content": "hi"}]


class TestOllama:
    def test_no_auth_header(self):
        p = OllamaProvider("", "http://localhost:11434", "llama3.1")
        h = p.build_headers()
        assert "Authorization" not in h
        assert h["Content-Type"] == "application/json"

    def test_chat_url_v1(self):
        assert OllamaProvider("", "http://localhost:11434", "m").chat_url() == \
            "http://localhost:11434/v1/chat/completions"

    def test_chat_url_already_v1(self):
        assert OllamaProvider("", "http://localhost:11434/v1", "m").chat_url() == \
            "http://localhost:11434/v1/chat/completions"


class TestCohere:
    def test_headers(self):
        p = CohereProvider("k", "https://api.cohere.com/v2", "command-r")
        assert p.build_headers()["Authorization"] == "Bearer k"

    def test_chat_url(self):
        p = CohereProvider("k", "https://api.cohere.com/v2", "command-r")
        assert p.chat_url() == "https://api.cohere.com/v2/chat"

    def test_build_request_roles_uppercased(self):
        p = CohereProvider("k", "u", "command-r")
        body = p.build_request(
            [{"role": "system", "content": "s"}, {"role": "assistant", "content": "a"},
             {"role": "user", "content": "u"}], 100, 0.5)
        assert body["messages"] == [
            {"role": "SYSTEM", "content": "s"},
            {"role": "CHATBOT", "content": "a"},
            {"role": "USER", "content": "u"},
        ]
        assert body["max_tokens"] == 100

    def test_parse_text_and_tool(self):
        p = CohereProvider("k", "u", "command-r")
        out = p.parse_response({
            "message": {"content": [{"type": "text", "text": "hello"},
                                    {"type": "tool_calls", "tool_calls": [
                                        {"id": "t1", "function": {"name": "calc", "arguments": {"a": 1}}}]}]},
            "finish_reason": "COMPLETE",
            "usage": {"tokens": {"inputTokens": 5, "outputTokens": 3}},
        })
        assert out["content"] == "hello"
        assert out["tool_calls"][0]["name"] == "calc"
        assert out["finish_reason"] == "stop"
        assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 3}


class TestBedrock:
    def test_request_has_anthropic_version(self):
        p = BedrockProvider("k", "https://bedrock-runtime.us-east-1.amazonaws.com", "anthropic.claude-3-5-sonnet")
        body = p.build_request([{"role": "user", "content": "hi"}], 100, 0.5)
        assert body["anthropic_version"] == "bedrock-2023-05-31"
        assert body["max_tokens"] == 100

    def test_chat_url(self):
        p = BedrockProvider("k", "https://bedrock-runtime.us-east-1.amazonaws.com",
                            "anthropic.claude-3-5-sonnet-v2:0")
        assert p.chat_url() == \
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-3-5-sonnet-v2%3A0/invoke"

    def test_sign_produces_auth_header(self):
        p = BedrockProvider("k", "u", "m")
        p.access_key = "AK"
        p.secret_key = "SK"
        headers = p.sign({"model": "m"}, "bedrock-runtime.us-east-1.amazonaws.com", "/model/m/invoke")
        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/")
        assert "x-amz-date" in headers
        assert "x-amz-content-sha256" in headers

    def test_stream_line_delegates_to_anthropic(self):
        p = BedrockProvider("k", "u", "m")
        assert p.parse_stream_line(
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"加"}}') == {"content": "加"}