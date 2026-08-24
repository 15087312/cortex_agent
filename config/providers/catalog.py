"""
供应商目录 — 以「供应商名」为中心的声明式配置（opencode 风格）

用户只需指定一个供应商名（如 "deepseek" / "gemini" / "anthropic"），即可
自动获得默认 base_url、API 格式、默认模型与认证方式，无需记忆底层协议。

目录字段见 base.ProviderSpec。绝大多数供应商走 OpenAI 兼容格式（由
OpenAIProvider 统一处理）；少数原生协议（gemini / azure / bedrock /
cohere / ollama / dashscope）由对应格式适配器处理。

端点与模型名以各供应商官方文档为准，命中不上的供应商可自行在 settings 里
用 *_MODEL_API_URL + *_MODEL_API_FORMAT 精确覆盖。
"""
from config.providers.base import ProviderSpec

CATALOG: dict[str, "ProviderSpec | None"] = {
    # ── 国际主流 ───────────────────────────────────────────────────────────
    "openai": ProviderSpec(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_format="openai",
        default_model="gpt-4o",
        env_key="OPENAI_API_KEY",
        doc="OpenAI 官方 API（GPT-4o / o1 等，OpenAI 兼容格式）",
    ),
    "anthropic": ProviderSpec(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_format="anthropic",
        default_model="claude-3-5-sonnet-20241022",
        env_key="ANTHROPIC_API_KEY",
        supports_reasoning=True,
        doc="Anthropic Claude（Messages API，非 OpenAI 兼容）",
    ),
    "gemini": ProviderSpec(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_format="gemini",
        default_model="gemini-2.0-flash",
        env_key="GEMINI_API_KEY",
        openai_compatible=False,
        supports_reasoning=True,
        aliases=["google-ai-studio", "google", "vertexai"],
        doc="Google Gemini（原生 generativelanguage 格式，非 OpenAI 兼容）",
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_format="openai",
        default_model="openrouter/auto",
        env_key="OPENROUTER_API_KEY",
        doc="OpenRouter 聚合网关（OpenAI 兼容，可路由多模型）",
    ),
    "groq": ProviderSpec(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_format="openai",
        default_model="llama-3.3-70b-versatile",
        env_key="GROQ_API_KEY",
        doc="Groq 高速推理（Llama 等，OpenAI 兼容）",
    ),
    "mistral": ProviderSpec(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        api_format="openai",
        default_model="mistral-large-latest",
        env_key="MISTRAL_API_KEY",
        doc="Mistral AI（Mistral Large，OpenAI 兼容）",
    ),
    "xai": ProviderSpec(
        name="xai",
        base_url="https://api.x.ai/v1",
        api_format="openai",
        default_model="grok-2-latest",
        env_key="XAI_API_KEY",
        aliases=["grok"],
        doc="xAI Grok（OpenAI 兼容）",
    ),
    "perplexity": ProviderSpec(
        name="perplexity",
        base_url="https://api.perplexity.ai",
        api_format="openai",
        default_model="sonar",
        env_key="PERPLEXITY_API_KEY",
        doc="Perplexity（联网 Sonar 系列，OpenAI 兼容）",
    ),
    "cohere": ProviderSpec(
        name="cohere",
        base_url="https://api.cohere.com/v2",
        api_format="cohere",
        default_model="command-r-plus",
        env_key="COHERE_API_KEY",
        openai_compatible=False,
        doc="Cohere（原生 Command API，非 OpenAI 兼容）",
    ),
    "aws-bedrock": ProviderSpec(
        name="aws-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_format="bedrock",
        default_model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        env_key="AWS_ACCESS_KEY_ID",
        auth_header="X-Amz-Content-Sha256",
        auth_prefix="",
        openai_compatible=False,
        doc="AWS Bedrock（SigV4 签名 + Claude/ Nova 等原生格式）",
        aliases=["bedrock", "amazon"],
    ),
    "azure": ProviderSpec(
        name="azure",
        base_url="https://YOUR-RESOURCE.openai.azure.com",
        api_format="azure",
        default_model="gpt-4o",
        env_key="AZURE_OPENAI_API_KEY",
        auth_header="api-key",
        auth_prefix="",
        doc="Azure OpenAI（api-key 头 + 部署路径路由）",
        aliases=["azure-openai"],
    ),
    "ollama": ProviderSpec(
        name="ollama",
        base_url="http://localhost:11434",
        api_format="ollama",
        default_model="llama3.1",
        env_key="",
        auth_header="",
        auth_prefix="",
        doc="本地 Ollama（默认走 OpenAI 兼容 /v1 端点，无密钥）",
    ),
    "lmstudio": ProviderSpec(
        name="lmstudio",
        base_url="http://localhost:1234/v1",
        api_format="openai",
        default_model="local-model",
        env_key="",
        doc="本地 LM Studio（OpenAI 兼容）",
        aliases=["local-openai"],
    ),
    "llamacpp": ProviderSpec(
        name="llamacpp",
        base_url="http://localhost:8080/v1",
        api_format="openai",
        default_model="local-model",
        env_key="",
        doc="本地 llama.cpp 服务器（OpenAI 兼容）",
        aliases=["llama.cpp", "llama-cpp"],
    ),

    # ── 国内主流 ───────────────────────────────────────────────────────────
    "deepseek": ProviderSpec(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_format="openai",
        default_model="deepseek-chat",
        env_key="DEEPSEEK_API_KEY",
        supports_reasoning=True,
        doc="DeepSeek（deepseek-chat / deepseek-reasoner，OpenAI 兼容，含 thinking）",
    ),
    "dashscope": ProviderSpec(
        name="dashscope",
        base_url="https://dashscope.aliyuncs.com/api/v1",
        api_format="dashscope",
        default_model="qwen-plus",
        env_key="DASHSCOPE_API_KEY",
        openai_compatible=False,
        doc="阿里云百炼 DashScope（通义千问，原生格式）",
        aliases=["qwen", "aliyun", "aliyun-bailian"],
    ),
    "modelscope": ProviderSpec(
        name="modelscope",
        base_url="https://api-inference.modelscope.cn/v1",
        api_format="dashscope",
        default_model="qwen-plus",
        env_key="MODELSCOPE_API_KEY",
        openai_compatible=False,
        doc="魔搭 ModelScope（DashScope 兼容格式）",
    ),
    "moonshot": ProviderSpec(
        name="moonshot",
        base_url="https://api.moonshot.cn/v1",
        api_format="openai",
        default_model="moonshot-v1-8k",
        env_key="MOONSHOT_API_KEY",
        aliases=["kimi"],
        doc="月之暗面 Kimi（OpenAI 兼容）",
    ),
    "zhipu": ProviderSpec(
        name="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_format="openai",
        default_model="glm-4-plus",
        env_key="ZHIPU_API_KEY",
        aliases=["glm", "bigmodel", "智谱", "智谱ai"],
        doc="智谱 AI（GLM，OpenAI 兼容 v4 端点）",
    ),
    "minimax": ProviderSpec(
        name="minimax",
        base_url="https://api.minimax.chat/v1",
        api_format="openai",
        default_model="abab6.5s-chat",
        env_key="MINIMAX_API_KEY",
        doc="MiniMax（abab 系列，OpenAI 兼容）",
    ),
    "siliconflow": ProviderSpec(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        api_format="openai",
        default_model="Qwen/Qwen2.5-72B-Instruct",
        env_key="SILICONFLOW_API_KEY",
        doc="硅基流动 SiliconFlow（开源模型托管，OpenAI 兼容）",
    ),
    "volcengine": ProviderSpec(
        name="volcengine",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_format="openai",
        default_model="doubao-pro-32k",
        env_key="VOLCENGINE_API_KEY",
        aliases=["volc", "火山引擎", "ark"],
        doc="火山引擎方舟（豆包，OpenAI 兼容 v3）",
    ),
    "baidu": ProviderSpec(
        name="baidu",
        base_url="https://qianfan.baidubce.com/v2",
        api_format="openai",
        default_model="deepseek-v3",
        env_key="BAIDU_API_KEY",
        aliases=["qianfan", "百度", "文心"],
        doc="百度千帆（文心/开源模型，OpenAI 兼容 v2）",
    ),
    "baichuan": ProviderSpec(
        name="baichuan",
        base_url="https://api.baichuan-ai.com/v1",
        api_format="openai",
        default_model="Baichuan4-Turbo",
        env_key="BAICHUAN_API_KEY",
        doc="百川（Baichuan 系列，OpenAI 兼容）",
    ),
    "stepfun": ProviderSpec(
        name="stepfun",
        base_url="https://api.stepfun.com/v1",
        api_format="openai",
        default_model="step-2-16k",
        env_key="STEPFUN_API_KEY",
        aliases=["阶跃", "step"],
        doc="阶跃星辰 StepFun（OpenAI 兼容）",
    ),
    "deepbricks": ProviderSpec(
        name="deepbricks",
        base_url="https://api.deepbricks.ai/v1",
        api_format="openai",
        default_model="gpt-4o-mini",
        env_key="DEEPBRICKS_API_KEY",
        doc="DeepBricks 聚合网关（OpenAI 兼容）",
    ),

    # ── 聚合/开源托管 ──────────────────────────────────────────────────────
    "together": ProviderSpec(
        name="together",
        base_url="https://api.together.xyz/v1",
        api_format="openai",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        env_key="TOGETHER_API_KEY",
        doc="Together AI（开源模型托管，OpenAI 兼容）",
    ),
    "fireworks": ProviderSpec(
        name="fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        api_format="openai",
        default_model="accounts/fireworks/models/llama-v3p1-70b-instruct",
        env_key="FIREWORKS_API_KEY",
        doc="Fireworks AI（快速推理托管，OpenAI 兼容）",
    ),
    "deepinfra": ProviderSpec(
        name="deepinfra",
        base_url="https://api.deepinfra.com/v1/openai",
        api_format="openai",
        default_model="meta-llama/Meta-Llama-3.1-70B-Instruct",
        env_key="DEEPINFRA_API_KEY",
        doc="DeepInfra（开源模型托管，OpenAI 兼容）",
    ),
    "nvidia-nim": ProviderSpec(
        name="nvidia-nim",
        base_url="https://integrate.api.nvidia.com/v1",
        api_format="openai",
        default_model="meta/llama-3.1-405b-instruct",
        env_key="NVIDIA_API_KEY",
        aliases=["nvidia"],
        doc="NVIDIA NIM（GPU 推理托管，OpenAI 兼容）",
    ),
    "sambanova": ProviderSpec(
        name="sambanova",
        base_url="https://api.sambanova.ai/v1",
        api_format="openai",
        default_model="Meta-Llama-3.1-8B-Instruct",
        env_key="SAMBANOVA_API_KEY",
        aliases=["sambada"],
        doc="SambaNova（开源模型托管，OpenAI 兼容）",
    ),
    "cerebras": ProviderSpec(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        api_format="openai",
        default_model="llama-3.3-70b",
        env_key="CEREBRAS_API_KEY",
        doc="Cerebras（极致速度推理，OpenAI 兼容）",
    ),
    "novita": ProviderSpec(
        name="novita",
        base_url="https://api.novita.ai/v3/openai",
        api_format="openai",
        default_model="meta-llama/llama-3.1-8b-instruct",
        env_key="NOVITA_API_KEY",
        doc="Novita AI（开源模型托管，OpenAI 兼容）",
    ),
    "jina": ProviderSpec(
        name="jina",
        base_url="https://api.jina.ai/v1",
        api_format="openai",
        default_model="jina-chat-v3",
        env_key="JINA_API_KEY",
        doc="Jina AI（Chat 系列，OpenAI 兼容）",
    ),
    "groq-cloudflare": ProviderSpec(
        name="groq-cloudflare",
        base_url="https://api.cloudflare.com/v1",
        api_format="openai",
        default_model="meta-llama-3.1-8b-instruct-fp8-fast",
        env_key="CLOUDFLARE_API_KEY",
        aliases=["cloudflare"],
        doc="Cloudflare Workers AI（OpenAI 兼容）",
    ),
}

#: 名称 → 别名映射（含中文别名），用于宽松匹配
_ALIAS_MAP: dict[str, str] = {}
for _name, _spec in CATALOG.items():
    if _spec is None:
        continue
    _ALIAS_MAP[_name.lower()] = _name
    _ALIAS_MAP[_name.lower().replace("-", "")] = _name
    for _alias in _spec.aliases:
        _ALIAS_MAP[str(_alias).strip().lower()] = _name
        _ALIAS_MAP[str(_alias).strip().lower().replace(" ", "").replace("-", "_")] = _name


def get_spec(name: str) -> "ProviderSpec | None":
    """按供应商名/别名查目录，找不到返回 None"""
    if not name:
        return None
    key = name.strip().lower()
    real = _ALIAS_MAP.get(key) or _ALIAS_MAP.get(key.replace("-", "_"))
    if real:
        return CATALOG[real]
    return None


def list_providers() -> list[str]:
    """返回目录中所有供应商名（按名排序）"""
    return sorted(k for k, v in CATALOG.items() if v is not None)