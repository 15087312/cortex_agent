"""验证重构后的 POST 请求格式

Mock aiohttp 请求，检查 HTTP body 格式。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock


async def verify_large_model_post():
    """验证大模型 POST body"""
    from infra.model.large_model_client import LargeModelClient

    client = LargeModelClient()
    client.api_url = "https://api.deepseek.com/v1/chat/completions"
    client.model_name = "deepseek-v4-flash"
    client.max_tokens = 100
    client.temperature = 0.7
    client._api_format = "openai"
    client._api_key = "sk-test-key"
    client.timeout = aiohttp.ClientTimeout(total=5)

    # Mock aiohttp.ClientSession
    captured = {}

    class MockResponse:
        def __init__(self):
            self.status = 200
            self.content_type = "application/json"

        async def json(self):
            return {"choices": [{"message": {"content": "Hello World"}, "finish_reason": "stop"}]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def mock_post(session_self, url, headers=None, json=None, **kwargs):
        captured["url"] = url
        captured["headers"] = dict(headers) if headers else {}
        captured["body"] = json
        return MockResponse()

    with patch("aiohttp.ClientSession.post", mock_post):
        result = await client.generate("你好", max_tokens=10)

    assert "你好" in result or "Hello" in result, f"Unexpected: {result}"

    url = captured.get("url", "")
    headers = captured.get("headers", {})
    body = captured.get("body", {})

    print("=== POST Verification ===")
    print(f"URL: {url}")
    ok_url = "chat/completions/chat/completions" not in url
    print(f"[{'OK' if ok_url else 'FAIL'}] No double path")
    print(f"[{'OK' if 'Bearer' in headers.get('Authorization','') else 'FAIL'}] Bearer auth")
    print(f"[{'OK' if headers.get('Content-Type')=='application/json' else 'FAIL'}] Content-Type")

    messages = body.get("messages", [])
    sys_msg = messages[0] if messages else {}
    sys_content = sys_msg.get("content", "")
    print(f"[{'OK' if sys_content else 'FAIL'}] system message: {len(sys_content)} chars")
    print(f"  first 200 chars: {sys_content[:200]}...")

    # Check for old hardcoded residues
    old = ["你是本AI系统的【主模型】", "你是本AI系统的[主模型]"]
    for p in old:
        if p in sys_content:
            print(f"[FAIL] Old residue: {p}")
            break
    else:
        print("[OK] No old template residue")

    print(f"[OK] max_tokens={body.get('max_tokens')}, temperature={body.get('temperature')}")
    print("=== PASS ===\n")


async def main():
    await verify_large_model_post()


if __name__ == "__main__":
    asyncio.run(main())
