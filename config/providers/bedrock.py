"""
AWS Bedrock 适配器（模型调用 + SigV4 签名）

Claude 等模型在 Bedrock 的请求/响应本质是 Anthropic Messages 格式，但端点
为 {bedrock-runtime}.{region}.amazonaws.com/model/{modelId}/invoke，且需要
AWS Signature Version 4 认证。本适配器：
- 复用 AnthropicProvider 的请求体构建 / 响应解析；
- 用标准库（hmac / hashlib）实现 SigV4 签名（无需 boto3）；
- 凭据从 settings/env 读取：AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
  AWS_SESSION_TOKEN / AWS_REGION（未配置时回退环境变量）。

注意：SigV4 需先有最终请求体（载荷哈希参与签名），故本适配器在 build_headers
时无法提前拿到 body。约定调用顺序为 build_headers() → build_request() →
sign(payload, host, uri)，由上层把 sign() 输出并入请求头。
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from config.providers.anthropic import AnthropicProvider


class BedrockProvider(AnthropicProvider):
    """AWS Bedrock 模型调用（Anthropic 请求体 + SigV4 认证）"""

    format_name = "bedrock"
    #: Anthropic 请求体需带 anthropic_version
    anthropic_version: str = "bedrock-2023-05-31"
    service = "bedrock"
    region = "us-east-1"

    def __init__(self, api_key: str, base_url: str, model_name: str):
        super().__init__(api_key, base_url, model_name)
        self.access_key = self.api_key or os.getenv("AWS_ACCESS_KEY_ID", "")
        self.secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        self.session_token = os.getenv("AWS_SESSION_TOKEN", "")
        self.region = os.getenv("AWS_REGION", self.region)

    # ------------------------------------------------------------------
    # 请求体：Anthropic 格式 + anthropic_version 头
    # ------------------------------------------------------------------

    def build_request(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Any] = None,
        stream: bool = False,
        top_p: Optional[float] = None,
        reasoning_effort: str = "",
    ) -> Dict[str, Any]:
        payload = super().build_request(
            messages, max_tokens, temperature, tools, tool_choice, stream, top_p, reasoning_effort,
        )
        payload.setdefault("anthropic_version", self.anthropic_version)
        return payload

    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        model_id = quote(self.model_name, safe="")
        return f"{base}/model/{model_id}/invoke"

    # ------------------------------------------------------------------ 认证
    # 说明：bedrock 的 SigV4 签名依赖请求体哈希，无法在 build_headers() 里
    # 独立完成。上层调用顺序约定为：
    #   1. build_headers() → 返回基础头（含 content-type）
    #   2. build_request() → 得到 payload
    #   3. sign(payload, headers) → 对 payload 签名，返回完整认证头
    # 若上层走 aiohttp session.post(headers=headers)，请把 sign() 的输出并入。
    # 保持向后兼容：build_headers() 仍返回基础头，供未启用签名时降级使用。

    def build_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def sign(self, payload: Dict[str, Any], host: str, uri_path: str, method: str = "POST") -> Dict[str, str]:
        """对给定 payload 计算 AWS SigV4 认证头"""
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        service = self.service
        region = self.region

        payload_hash = hashlib.sha256(
            payload if isinstance(payload, bytes) else
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        canonical_headers = (
            f"content-type:application/json\n"
            f"host:{host}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "content-type;host;x-amz-date"
        if self.session_token:
            canonical_headers += f"x-amz-security-token:{self.session_token}\n"
            signed_headers += ";x-amz-security-token"

        canonical_request = (
            f"{method}\n{quote(uri_path)}\n\n{canonical_headers}\n"
            f"{signed_headers}\n{payload_hash}"
        )

        scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _hmac(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = _hmac(("AWS4" + self.secret_key).encode(), date_stamp)
        k_region = _hmac(k_date, region)
        k_service = _hmac(k_region, service)
        k_signing = _hmac(k_service, "aws4_request")

        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Authorization": authorization,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.session_token:
            headers["x-amz-security-token"] = self.session_token
        return headers

    # ------------------------------------------------------------------ 流式
    def parse_stream_line(self, line: str) -> Optional[Dict[str, Any]]:
        # Bedrock 流式事件为 data: {"type":"content_block_delta",...}（与 Anthropic 一致）
        return super().parse_stream_line(line)