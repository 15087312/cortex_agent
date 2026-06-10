"""
全局配置类 - 加载.env、管理所有模块的配置
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional
import os


class Settings(BaseSettings):
    """全局配置类"""

    # 运行时可通过 API 修改的配置项（api/main.py 的 /config/{key} 端点使用）
    _MODIFIABLE_FIELDS: set = {
        "LOG_LEVEL", "DEBUG", "MAX_WORKERS", "LOGGING_ENABLED",
        "ATTENTION_WEIGHT_THRESHOLD", "INTERRUPT_URGENCY_THRESHOLD",
        "ATTENTION_IMPORTANCE_ENABLED", "ATTENTION_IMPORTANCE_MODEL_ENABLED",
        "ATTENTION_FORCE_STATIC_LEVEL", "ATTENTION_THRESHOLD_BASE",
        "ATTENTION_THRESHOLD_SLOPE", "ATTENTION_THRESHOLD_MIN",
        "ATTENTION_THRESHOLD_MAX", "ATTENTION_MAX_RECALL_LOW",
        "ATTENTION_MAX_RECALL_MEDIUM", "ATTENTION_MAX_RECALL_HIGH",
        "PROACTIVE_OUTREACH_ENABLED", "PROACTIVE_OUTREACH_COOLDOWN_MINUTES",
        "PROACTIVE_OUTREACH_IDLE_MINUTES",
        "MEMORY_TTL_SHORT", "MEMORY_TTL_LONG",
        "EXECUTION_MODE", "COMPANION_MODE",
    }

    # 用户身份
    USER_NAME: str = "用户"  # 大模型知道谁在跟它说话

    # API 配置
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE_URL: str = "https://api.openai.com/v1"

    # 大模型
    LARGE_MODEL_API_KEY: str = ""
    LARGE_MODEL_API_URL: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    LARGE_MODEL_NAME: str = "deepseek-v4-flash"
    LARGE_MODEL_API_FORMAT: str = ""  # "dashscope" / "openai" / 留空自动检测

    # 中模型
    MEDIUM_MODEL_API_KEY: str = ""
    MEDIUM_MODEL_API_URL: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    MEDIUM_MODEL_NAME: str = "deepseek-v4-flash"

    # 小模型
    SMALL_MODEL_API_KEY: str = ""
    SMALL_MODEL_API_URL: str = ""
    SMALL_MODEL_NAME: str = "deepseek-v4-flash"

    # 轻量模型
    EXPERT_MODEL_NAME: str = "qwen2.5-7b-instruct"

    # 视觉模型配置
    # VISION_BACKEND: 后端选择 — api / mlx / transformers / mock / auto
    #   auto:          按优先级自动检测（api > mlx > transformers > mock）
    #   api:           云端 API（OpenAI / DashScope / 兼容接口）
    #   mlx:           Apple Silicon 本地 MLX-VLM（4-bit 量化）
    #   transformers:  本地 transformers + CUDA/MPS/CPU
    #   mock:          模拟模式
    VISION_BACKEND: str = "auto"
    VISION_API_URL: str = ""                       # 视觉 API 地址（留空则复用 OPENAI_API_BASE_URL）
    VISION_API_KEY: str = ""                       # 视觉 API Key（留空则复用 OPENAI_API_KEY）
    VISION_API_FORMAT: str = ""                    # API 格式: openai / dashscope / 留空自动检测
    VISION_API_MODEL: str = ""                     # 云端视觉模型名（如 gpt-4o, qwen-vl-max）
    VISION_LOCAL_MODEL: str = ""                   # 本地 transformers 模型名（留空用默认）
    VISION_MLX_MODEL: str = ""                     # MLX 模型名（留空用默认）

    # 默认模型名（不建议修改，优先用上面的 VISION_* 配置）
    IMAGE_MODEL_NAME: str = "gpt-4o"
    QWEN_VL_MODEL_NAME: str = "Qwen/Qwen2-VL-2B-Instruct"  # 本地视觉模型（transformers 路径）
    QWEN_VL_MLX_MODEL_NAME: str = "mlx-community/Qwen2-VL-7B-Instruct-4bit"  # Apple Silicon MLX 路径

    # Embedding/RAG 配置
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_CACHE_FOLDER: str = "data/memory/embeddings/models"
    EMBEDDING_LOCAL_FILES_ONLY: bool = True

    # SQLite 数据库配置（默认，可直接打包）
    SQLITE_PATH: str = str(Path(__file__).resolve().parents[1] / "data" / "memory.db")

    # diskcache 缓存配置
    CACHE_DIR: str = "data/cache"
    CACHE_SIZE_LIMIT: int = 100 * 1024 * 1024  # 100MB

    # 向量数据库配置（可选）
    VECTOR_DB_HOST: str = "localhost"
    VECTOR_DB_PORT: int = 6333
    VECTOR_DB_DIMENSION: int = 768

    # 插件系统配置
    PLUGINS_DIR: str = "data/plugins"
    PLUGIN_ENGINE_ENABLED: bool = True
    PLUGIN_REQUIRE_SIGNATURES: bool = True
    PLUGIN_REQUIRE_ENFORCED_SANDBOX: bool = True
    PLUGIN_SANDBOX_BACKEND: str = "auto"

    # 工具/MCP 配置
    TOOL_BACKEND: str = "mcp"  # legacy / mcp / hybrid
    MCP_SERVERS: str = ""  # JSON object: {"server": {"command": "...", "args": []}}

    # 系统配置
    # Q-9: Require explicit environment configuration in production
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    MAX_WORKERS: int = 10

    # 运行模式配置
    # COMPANION_MODE: True=陪伴模式（情绪+价值观全开，工具只读，不开委托）
    #                  False=工作模式（完整委托链，情绪/价值观关闭）
    COMPANION_MODE: bool = False

    # 安全审查模式: "llm"=安全专家LLM审批, "user"=用户手动审批, "auto"=LLM可用时用LLM否则拒绝
    SECURITY_REVIEW_MODE: str = "auto"

    # 执行模式（仅工作模式有效，陪伴模式强制 plan）
    # "plan":    只读 — 禁止所有写操作
    # "edit":    确认 — 写操作前需用户确认（LLM + 用户）
    # "yolo":    宽松 — 仅安全专家检测，跳过用户确认
    # "control": 用户完全控制 — MEDIUM+工具需用户单独确认，无LLM参与
    EXECUTION_MODE: str = "edit"

    # 上下文窗口配置
    # CONTEXT_WINDOW_SIZE: 大模型上下文窗口大小（token 数）
    #   qwen-max: 128K, deepseek-v4: 128K, gpt-4o: 128K, claude-3.5: 200K
    #   按实际使用的模型设置，不要低于模型真实窗口
    CONTEXT_WINDOW_SIZE: int = 128000
    # CONTEXT_COMPRESS_RATIO: 触发压缩时，压缩到窗口的百分比
    #   0.2 = 压缩到窗口的 20%（如 128K 窗口 → 压缩到 ~25K）
    CONTEXT_COMPRESS_RATIO: float = 0.2

    # API 认证
    SIMPLE_API_KEY: str = ""                   # HTTP API 认证密钥
    ALLOWED_CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000"

    # 内部服务 Token
    TOOL_API_TOKEN: str = ""                   # 工具管理 API Token
    INTERNAL_API_TOKEN: str = ""               # 内部管理 API Token
    PLUGIN_API_TOKEN: str = ""                 # 插件系统 API Token

    # 服务端口
    SERVER_PORT: int = 8080

    # 日志
    LOGGING_ENABLED: bool = True

    @property
    def effective_execution_mode(self) -> str:
        """实际执行模式（陪伴模式强制 plan）"""
        if self.COMPANION_MODE:
            return "plan"
        return self.EXECUTION_MODE

    @property
    def effective_security_review_mode(self) -> str:
        """有效的审批模式（control 模式强制 user）"""
        if self.effective_execution_mode == "control":
            return "user"
        return self.SECURITY_REVIEW_MODE

    @property
    def is_delegation_available(self) -> bool:
        """委托是否可用（陪伴模式下强制关闭）"""
        return not self.COMPANION_MODE

    @property
    def effective_emotion_enabled(self) -> bool:
        """情绪是否启用（陪伴模式下开启）"""
        return self.COMPANION_MODE

    @property
    def effective_values_enabled(self) -> bool:
        """价值观是否启用（陪伴模式下开启）"""
        return self.COMPANION_MODE

    @property
    def is_expert_pipeline_enabled(self) -> bool:
        """专家流水线是否需要运行"""
        return self.COMPANION_MODE

    @property
    def effective_vision_api_url(self) -> str:
        """视觉 API 地址（VISION_API_URL → OPENAI_API_BASE_URL）"""
        return self.VISION_API_URL or self.OPENAI_API_BASE_URL

    @property
    def effective_vision_api_key(self) -> str:
        """视觉 API Key（VISION_API_KEY → OPENAI_API_KEY）"""
        return self.VISION_API_KEY or self.OPENAI_API_KEY

    @property
    def effective_vision_api_model(self) -> str:
        """视觉 API 模型名（VISION_API_MODEL → IMAGE_MODEL_NAME）"""
        return self.VISION_API_MODEL or self.IMAGE_MODEL_NAME

    @property
    def effective_vision_local_model(self) -> str:
        """本地 transformers 模型名（VISION_LOCAL_MODEL → QWEN_VL_MODEL_NAME）"""
        return self.VISION_LOCAL_MODEL or self.QWEN_VL_MODEL_NAME

    @property
    def effective_vision_mlx_model(self) -> str:
        """MLX 模型名（VISION_MLX_MODEL → QWEN_VL_MLX_MODEL_NAME）"""
        return self.VISION_MLX_MODEL or self.QWEN_VL_MLX_MODEL_NAME

    @field_validator("SERVER_PORT")
    @classmethod
    def validate_server_port(cls, v: int) -> int:
        if not (1024 <= v <= 65535):
            raise ValueError(f"SERVER_PORT must be between 1024 and 65535, got {v}")
        return v

    @field_validator("CONTEXT_COMPRESS_RATIO")
    @classmethod
    def validate_compress_ratio(cls, v: float) -> float:
        if not (0.05 <= v <= 0.95):
            raise ValueError(f"CONTEXT_COMPRESS_RATIO must be between 0.05 and 0.95, got {v}")
        return v

    @field_validator("EXECUTION_MODE")
    @classmethod
    def validate_execution_mode(cls, v: str) -> str:
        allowed = {"plan", "edit", "yolo", "control"}
        if v.lower() not in allowed:
            raise ValueError(f"EXECUTION_MODE must be one of {allowed}, got '{v}'")
        return v.lower()

    def validate_production(self) -> None:
        """Q-9: Validate critical settings for production deployment"""
        if self.APP_ENV == "production":
            critical_settings = {
                "LARGE_MODEL_API_KEY": "Large model API key",
                "SIMPLE_API_KEY": "HTTP API authentication key",
            }
            missing = [name for name, desc in critical_settings.items()
                      if not getattr(self, name, "")]
            if missing:
                raise ValueError(
                    f"Production deployment requires: {', '.join(missing)}"
                )

    # 模型配置
    MODEL_TIMEOUT: int = 180  # 模型 HTTP 请求超时（秒），可被各模型配置覆盖

    # 感知系统总开关
    PERCEPTION_ENABLED: bool = True

    # ── 被动感知（环境数据采集） ──────────────────────────
    # 负责从环境中采集原始数据：截图、OCR、文件监控、对话监控、语音
    PERCEPTION_SCREEN_ENABLED: bool = True             # 屏幕感知（帧差+OCR+UI+窗口）
    PERCEPTION_FILE_ENABLED: bool = True               # 文件变化感知（watchdog）
    PERCEPTION_DIALOG_ENABLED: bool = True             # 对话变化感知
    PERCEPTION_VOICE_ENABLED: bool = False             # 语音感知（麦克风+Whisper STT）
    PERCEPTION_VOICE_DEVICE: Optional[int] = None      # 麦克风设备索引（None=系统默认）
    PERCEPTION_VOICE_MODEL: str = "tiny"               # Whisper 模型大小 (tiny/base/small/medium/large)
    PERCEPTION_VOICE_LANGUAGE: str = "zh"              # 语音识别语言
    PERCEPTION_VOICE_ENERGY_THRESHOLD: int = 300       # 静音能量阈值（越低越灵敏）
    PERCEPTION_VOICE_TIMEOUT: float = 10.0             # 单次录音超时（秒）

    # ── 主动感知（差异检测 → 触发响应） ────────────────────
    # 负责分析被动感知数据，检测变化并触发思考/搭话等响应
    DIFFERENCE_DETECTOR_ENABLED: bool = True           # 差异检测器（1Hz 心跳扫描）
    PERCEPTION_INTERNAL_ENABLED: bool = True           # 内部状态源（未完成任务、失败任务等）

    # 差异 → 思考触发
    PERCEPTION_TRIGGER_THINK: bool = True              # 差异是否触发单次思考
    PERCEPTION_TRIGGER_MIN_INTENSITY: float = 50.0     # 触发思考的最小差异强度 (0-100)
    PERCEPTION_TRIGGER_COOLDOWN: int = 60              # 触发冷却（秒）

    # 主动搭话（空闲时主动与用户交互）
    PROACTIVE_OUTREACH_ENABLED: bool = True            # 是否启用自动搭话
    PROACTIVE_OUTREACH_COOLDOWN_MINUTES: int = 15      # 搭话冷却时间（分钟）
    PROACTIVE_OUTREACH_IDLE_MINUTES: int = 15          # 触发搭话的空闲阈值（分钟）
    PROACTIVE_OUTREACH_COMPANION_PROMPT: str = ""      # 陪伴模式自定义提示词（为空则用默认）
    PROACTIVE_OUTREACH_WORK_PROMPT: str = ""           # 工作模式自定义提示词（为空则用默认）

    # 记忆配置
    MEMORY_TTL_SHORT: int = 3600  # 1 小时
    MEMORY_TTL_LONG: int = 86400  # 24 小时
    MEMORY_VECTOR_DIMENSION: int = 768

    # 注意力配置
    ATTENTION_WEIGHT_THRESHOLD: float = 0.7
    INTERRUPT_URGENCY_THRESHOLD: float = 0.9
    ATTENTION_IMPORTANCE_ENABLED: bool = True
    ATTENTION_IMPORTANCE_MODEL_ENABLED: bool = False
    ATTENTION_FORCE_STATIC_LEVEL: Optional[float] = None
    ATTENTION_THRESHOLD_BASE: float = 0.6
    ATTENTION_THRESHOLD_SLOPE: float = 0.5
    ATTENTION_THRESHOLD_MIN: float = 0.1
    ATTENTION_THRESHOLD_MAX: float = 0.6
    ATTENTION_MAX_RECALL_LOW: int = 5
    ATTENTION_MAX_RECALL_MEDIUM: int = 10
    ATTENTION_MAX_RECALL_HIGH: int = 20

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[1] / ".env"),
        case_sensitive=False,
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        """创建必要的数据目录"""
        db_dir = os.path.dirname(self.SQLITE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    @property
    def sqlite_url(self) -> str:
        """获取 SQLite 连接 URL"""
        return f"sqlite:///{self.SQLITE_PATH}"


# 全局配置实例
try:
    settings = Settings()
except Exception as e:
    import sys
    print(f"[WARNING] Failed to load settings from .env: {e}", file=sys.stderr)
    print("[WARNING] Using default settings. Create a .env file for production.", file=sys.stderr)
    settings = Settings(_env_file=None)
