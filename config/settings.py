"""
全局配置类 - 加载.env、管理所有模块的配置
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional
import os

# 记忆库切换互斥锁（模块级，避免作为类属性参与 pydantic pickle）
_MEMORY_SWITCH_LOCK = __import__("threading").RLock()


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
        "CAUSAL_MAX_NODES", "CAUSAL_MAX_ANCHORS", "CAUSAL_MAX_NEIGHBORS_PER_HOP",
        "CAUSAL_MAX_TREE_DEPTH", "CAUSAL_MAX_EVENTS_RECALL", "CAUSAL_MIN_CONFIDENCE",
        "CAUSAL_MIN_COOCCUR", "CAUSAL_HOT_CACHE_TTL",
        "CAUSAL_CONFIDENCE_BOOST_DELTA", "CAUSAL_CONFIDENCE_MAX",
        "CAUSAL_UPDATE_STATS_INTERVAL",
        "EXECUTION_MODE",
        "CORTEX_MODE",   # 对话模式: agent(智能体) / chatonly(纯对话)
        "USER_NAME",     # 用户称呼（大模型如何称呼用户）
        "PERSONA_PROMPTS",# 自定义人设提示词（JSON: {role: prompt}）
        "SYSTEM_PROMPT_OVERRIDES",  # 完整系统提示词覆盖（JSON: {role: prompt}，高级设置）
        "OUTPUT_TTS_ENABLED",    # TTS 语音输出总开关

        # ── 感知系统模块开关 ──
        "PERCEPTION_ENABLED", "PERCEPTION_SCREEN_ENABLED",
        "PERCEPTION_FILE_ENABLED", "PERCEPTION_MCP_ENABLED",
        "PERCEPTION_INTERNAL_ENABLED", "SCREEN_DIFF_ENABLED",
        "PERCEPTION_TRIGGER_THINK",
        "PERCEPTION_TRIGGER_COOLDOWN", "PERCEPTION_TRIGGER_MIN_INTENSITY",

        # ── 语音识别（Whisper）──
        "PERCEPTION_VOICE_ENABLED",
        "PERCEPTION_VOICE_MODEL", "PERCEPTION_VOICE_MODE", "PERCEPTION_VOICE_HOTKEY",
        "PERCEPTION_VOICE_LANGUAGE", "PERCEPTION_VOICE_WAKE_PREFIX", "PERCEPTION_VOICE_WAKE_SUFFIX",
        "PERCEPTION_VOICE_ENERGY_THRESHOLD", "PERCEPTION_VOICE_TIMEOUT",
        "PERCEPTION_VOICE_MAX_DURATION", "PERCEPTION_VOICE_END_STOP",
        "PERCEPTION_VOICE_BACKEND", "PERCEPTION_VOICE_API_KEY", "PERCEPTION_VOICE_API_URL",
        "PERCEPTION_VOICE_API_MODEL",
        "OUTPUT_TTS_BACKEND", "OUTPUT_TTS_API_KEY", "OUTPUT_TTS_API_URL",
        "OUTPUT_TTS_API_MODEL", "OUTPUT_TTS_API_VOICE",

        # ── 视觉模型 ──
        "VISION_BACKEND", "VISION_API_FORMAT", "VISION_API_URL", "VISION_API_KEY",
        "VISION_API_MODEL", "VISION_LOCAL_MODEL", "VISION_MLX_MODEL",

        # ── 桌面宠物 ──
        "DESKTOP_PET_ENABLED", "DESKTOP_PET_SESSION_ID",
    }

    # 用户身份
    USER_NAME: str = "用户"  # 大模型知道谁在跟它说话
    PERSONA_PROMPTS: str = "{}"  # 自定义人设提示词（JSON: {role: prompt}，覆盖各角色默认【人格】）
    SYSTEM_PROMPT_OVERRIDES: str = "{}"  # 完整系统提示词覆盖（JSON: {role: prompt}，高级设置，完全控制 system prompt）

    # API 配置
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE_URL: str = "https://api.openai.com/v1"

    # 大模型（必须在 .env 或 ~/.cortex/settings.json 中配置）
    LARGE_MODEL_API_KEY: str = ""
    LARGE_MODEL_API_URL: str = ""
    LARGE_MODEL_NAME: str = ""
    LARGE_MODEL_API_FORMAT: str = ""  # "dashscope" / "openai" / 留空自动检测

    # 中模型（必须在 .env 或 ~/.cortex/settings.json 中配置）
    MEDIUM_MODEL_API_KEY: str = ""
    MEDIUM_MODEL_API_URL: str = ""
    MEDIUM_MODEL_NAME: str = ""

    # 小模型（必须在 .env 或 ~/.cortex/settings.json 中配置）
    SMALL_MODEL_API_KEY: str = ""
    SMALL_MODEL_API_URL: str = ""
    SMALL_MODEL_NAME: str = ""



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
    IMAGE_MODEL_NAME: str = ""
    QWEN_VL_MODEL_NAME: str = "Qwen/Qwen2-VL-2B-Instruct"  # 本地视觉模型（transformers 路径）
    QWEN_VL_MLX_MODEL_NAME: str = "mlx-community/Qwen2-VL-7B-Instruct-4bit"  # Apple Silicon MLX 路径

    # Embedding/RAG 配置
    EMBEDDING_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_CACHE_FOLDER: str = "data/memory/embeddings/models"
    EMBEDDING_LOCAL_FILES_ONLY: bool = False
    HF_MIRROR: str = ""

    # SQLite 数据库配置（默认，可直接打包）
    SQLITE_PATH: str = str(Path(__file__).resolve().parents[1] / "data" / "memory.db")

    # ── 事件记忆库（支持多记忆库切换/命名，见 get_memory_libs / switch_memory_lib）──
    MEMORY_DB_PATH: str = str(Path(__file__).resolve().parents[1] / "data" / "memory.db")
    MEMORY_FAISS_INDEX: str = str(Path(__file__).resolve().parents[1] / "data" / "events_faiss.index")
    MEMORY_ID_MAP: str = str(Path(__file__).resolve().parents[1] / "data" / "events_id_map.json")

    MCP_SERVERS: str = ""  # JSON object: {"server": {"command": "...", "args": []}}

    # 系统配置
    # Q-9: Require explicit environment configuration in production
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    MAX_WORKERS: int = 10

    # 执行模式 — 控制安全门控行为
    # "plan":    只读 — 禁止所有写操作
    # "edit":    确认 — 写操作前需用户确认（LLM + 用户）
    # "yolo":    宽松 — 仅安全专家检测，跳过用户确认
    # "control": 用户完全控制 — MEDIUM+工具需用户单独确认，无LLM参与
    # 注意：身份/工具/人格/风格等由激活的 Skill 控制，执行模式仅决定安全门控策略
    EXECUTION_MODE: str = "edit"

    # 对话路线模式 — 由对话网关 (modules/thinking/chat_gateway.py) 在每条对话开始时读取
    # "agent":    完整多模型编排路线（默认，走 modules/thinking/api_stream）
    # "chatonly": 简化单模型对话路线（走 backend/，懒加载，不影响其他模块）
    CORTEX_MODE: str = "agent"

    # 安全审查模式: "llm"=安全专家LLM审批, "user"=用户手动审批, "auto"=LLM可用时用LLM否则拒绝

    # 上下文窗口配置
    # CONTEXT_WINDOW_SIZE: 大模型上下文窗口大小（token 数）
    #   qwen-max: 128K, deepseek-v4: 128K, gpt-4o: 128K, claude-3.5: 200K
    #   按实际使用的模型设置，不要低于模型真实窗口
    CONTEXT_WINDOW_SIZE: int = 128000
    # CONTEXT_COMPRESS_RATIO: 触发压缩时，压缩到窗口的百分比
    #   0.2 = 压缩到窗口的 20%（如 128K 窗口 → 压缩到 ~25K）
    CONTEXT_COMPRESS_RATIO: float = 0.2

    # API 认证
    SIMPLE_API_KEY: str = ""                   # HTTP API 认证密钥（所有外部端点统一使用 X-API-Key）
    ALLOWED_CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000"

    # 服务端口
    SERVER_PORT: int = 8080

    # 日志
    LOGGING_ENABLED: bool = True

    # ── 纯对话（chatonly）兼容字段（原 backend/config/settings 并入，统一基础设施）──
    MODEL_MAX_TOKENS: int = 4096
    MODEL_TEMPERATURE: float = 0.7
    EMBEDDING_DEVICE: str = "cpu"
    MEMORY_REDUCE_ENABLED: bool = True
    CHAT_MAX_ROUNDS: int = 5
    CHAT_CONTEXT_MAX_MESSAGES: int = 50
    CHAT_CONTEXT_MAX_TOKENS: int = 8000
    CHAT_CONTEXT_MAX_CHARS: int = 6000  # 会话记忆总上限字数（超出部分由 LLM 总结）

    # ── 因果记忆（backend 并入）──
    CAUSAL_DB_PATH: str = "data/causal.db"
    CAUSAL_MAX_ANCHORS: int = 3
    CAUSAL_MAX_NEIGHBORS_PER_HOP: int = 10
    CAUSAL_MAX_TREE_DEPTH: int = 4
    CAUSAL_MAX_EVENTS_RECALL: int = 30
    CAUSAL_MIN_CONFIDENCE: float = 0.2

    # ── 服务（backend/main 并入，统一入口后以 api/main 为准）──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:5173"

    # ── 身份（backend 并入）──
    ASSISTANT_NAME: str = "Cortex"

    # ── 桌面宠物（桌宠）──
    DESKTOP_PET_ENABLED: bool = True            # 是否启用桌宠（语音触发与主会话对话）
    DESKTOP_PET_SESSION_ID: str = "pet_main"     # 桌宠主会话（永不删除，对话记忆延续）

    # ── 前端 UI 设置（原 backend ~/.cortex-mini/settings.json 持久化，并入主 settings）──
    launch_at_startup: bool = True
    prevent_sleep: bool = False
    show_filename_in_gallery: bool = False
    allow_geolocation: bool = False
    shortcut_keys: str = "⌥ + T"
    storage_path: str = ""

    @property
    def effective_execution_mode(self) -> str:
        """实际执行模式"""
        return self.EXECUTION_MODE

    @property

    @property
    def is_delegation_available(self) -> bool:
        """委托是否可用（始终可用，由 Skill ToolRules 控制可见性）"""
        return True

    def get_persona(self, role: str) -> str:
        """获取指定角色的自定义人设（未设置返回空串）。

        存储：~/.cortex/personas.yaml（personas: {role: prompt}）；
        兼容旧数据：settings.json 的 PERSONA_PROMPTS。
        """
        data = self._load_personas_yaml()
        val = (data.get("personas", {}).get(role) or "").strip()
        if val:
            return val
        import json
        try:
            old = json.loads(self.PERSONA_PROMPTS or "{}")
            return (old.get(role) or "").strip()
        except Exception:
            return ""

    def set_persona(self, role: str, prompt: str) -> str:
        """设置指定角色的自定义人设（prompt 为空则清除），写入 ~/.cortex/personas.yaml"""
        data = self._load_personas_yaml()
        personas = data.setdefault("personas", {})
        prompt = (prompt or "").strip()
        if prompt:
            personas[role] = prompt
        else:
            personas.pop(role, None)
        self._save_personas_yaml(data)
        return personas.get(role, "")

    def get_system_override(self, role: str) -> str:
        """获取指定角色的完整系统提示词覆盖（未设置返回空串）"""
        data = self._load_personas_yaml()
        return (data.get("system_overrides", {}).get(role) or "").strip()

    def set_system_override(self, role: str, prompt: str) -> str:
        """设置指定角色的完整系统提示词覆盖（prompt 为空则清除），写入 yaml"""
        data = self._load_personas_yaml()
        overrides = data.setdefault("system_overrides", {})
        prompt = (prompt or "").strip()
        if prompt:
            overrides[role] = prompt
        else:
            overrides.pop(role, None)
        self._save_personas_yaml(data)
        return overrides.get(role, "")

    @property
    def _personas_yaml_path(self):
        return Path.home() / ".cortex" / "personas.yaml"

    def _load_personas_yaml(self) -> dict:
        """读取 ~/.cortex/personas.yaml"""
        import yaml
        import time
        for _ in range(2):
            try:
                if self._personas_yaml_path.exists():
                    data = yaml.safe_load(self._personas_yaml_path.read_text(encoding="utf-8")) or {}
                    return data if isinstance(data, dict) else {}
                break
            except Exception:
                # 并发写可能读到半写 → 稍等后重试一次
                time.sleep(0.05)
        return {}

    def _save_personas_yaml(self, data: dict) -> None:
        """写入 ~/.cortex/personas.yaml（原子写，防读端读到半写）"""
        import yaml
        import os
        import sys
        try:
            path = self._personas_yaml_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception as e:
            print(f"[WARNING] 人设 yaml 保存失败: {e}", file=sys.stderr)

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
        """视觉 API 模型名（VISION_API_MODEL → IMAGE_MODEL_NAME）

        根据 API URL 自动调整模型名：
        - DeepSeek API → deepseek-v4-flash
        - OpenAI API → gpt-4o
        - 其他 → IMAGE_MODEL_NAME
        """
        if self.VISION_API_MODEL:
            return self.VISION_API_MODEL

        api_url = (self.VISION_API_URL or self.OPENAI_API_BASE_URL or "").lower()

        # 根据 API URL 自动调整模型名
        if "deepseek" in api_url:
            return "deepseek-v4-flash"
        elif "openai" in api_url or "openai" not in api_url:
            # OpenAI 或未知 API 使用 gpt-4o
            return self.IMAGE_MODEL_NAME

        return self.IMAGE_MODEL_NAME

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
            raise ValueError(f"EXECUTION_MODE must be one of plan/edit/yolo/control, got '{v}'")
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

        # 任何环境下都校验模型配置完整性
        missing_model = []
        for tier in ("LARGE", "MEDIUM", "SMALL"):
            for field in ("API_KEY", "API_URL", "NAME"):
                key = f"{tier}_MODEL_{field}"
                if not getattr(self, key, ""):
                    missing_model.append(key)
        if missing_model:
            import sys
            print(
                f"[WARNING] 以下模型配置项未设置，请在 .env 中配置: {', '.join(missing_model)}",
                file=sys.stderr,
            )

    # 模型配置
    MODEL_TIMEOUT: int = 180  # 模型 HTTP 请求超时（秒），可被各模型配置覆盖

    # 感知系统总开关
    PERCEPTION_ENABLED: bool = True

    # ── 被动感知（环境数据采集） ──────────────────────────
    # 负责从环境中采集原始数据：截图、OCR、文件监控、对话监控、语音
    PERCEPTION_SCREEN_ENABLED: bool = True             # 屏幕感知（帧差+OCR+UI+窗口）
    PERCEPTION_FILE_ENABLED: bool = True               # 文件变化感知（watchdog）
    PERCEPTION_MCP_ENABLED: bool = True                # MCP 资源感知（通过 MCP 协议获取外部数据）
    PERCEPTION_VOICE_ENABLED: bool = False             # 语音感知（麦克风+Whisper STT）
    PERCEPTION_VOICE_MODE: str = "hotkey"              # 语音触发模式: hotkey=热键开关 / wakeword=唤醒词监听
    PERCEPTION_VOICE_HOTKEY: str = "f8"                # 热键模式全局热键（pynput 格式: f8 / <ctrl>+<space>）
    PERCEPTION_VOICE_DEVICE: Optional[int] = None      # 麦克风设备索引（None=系统默认）
    PERCEPTION_VOICE_MODEL: str = "tiny"               # Whisper 模型大小 (tiny/base/small/medium/large)
    PERCEPTION_VOICE_LANGUAGE: str = "zh"              # 语音识别语言
    PERCEPTION_VOICE_ENERGY_THRESHOLD: int = 300       # 静音能量阈值（越低越灵敏）
    PERCEPTION_VOICE_TIMEOUT: float = 10.0             # 单次录音超时（秒）
    PERCEPTION_VOICE_WAKE_PREFIX: str = "科特"           # 语音触发大模型时的唤醒前缀
    PERCEPTION_VOICE_WAKE_SUFFIX: str = "完毕"           # 语音触发大模型时的结束后缀（同时作为停止词）
    PERCEPTION_VOICE_END_STOP: bool = True             # 结束词（完毕）作为停止词: 说完即止
    PERCEPTION_VOICE_MAX_DURATION: float = 60.0        # 单次录音最大时长（秒）
    PERCEPTION_VOICE_END_CHECK_INTERVAL: float = 3.0   # 录音中检测结束词的间隔（秒）

    # ── 语音识别（STT）本地/云端 ──────────────────────────
    PERCEPTION_VOICE_BACKEND: str = "local"            # local=本地 Whisper / api=云端 API（OpenAI 兼容）
    PERCEPTION_VOICE_API_KEY: str = ""                 # 云端 STT API Key
    PERCEPTION_VOICE_API_URL: str = ""                 # 云端 STT 地址（留空用 https://api.openai.com/v1/audio/transcriptions）
    PERCEPTION_VOICE_API_MODEL: str = ""               # 云端 STT 模型名（留空用 whisper-1）

    # ── 语音输出（TTS） ──────────────────────────────────────
    OUTPUT_TTS_ENABLED: bool = True                      # TTS 输出总开关（文本转语音）
    OUTPUT_TTS_LANGUAGE: str = "zh"                      # TTS 合成语言
    OUTPUT_TTS_OUTPUT_DIR: str = "data/output"           # TTS 音频输出目录
    OUTPUT_TTS_BACKEND: str = "local"                    # local=gTTS(内置) / api=云端 API（OpenAI 兼容 /audio/speech）
    OUTPUT_TTS_API_KEY: str = ""                         # 云端 TTS API Key
    OUTPUT_TTS_API_URL: str = ""                         # 云端 TTS 地址（留空用 https://api.openai.com/v1/audio/speech）
    OUTPUT_TTS_API_MODEL: str = ""                       # 云端 TTS 模型名（留空用 tts-1）
    OUTPUT_TTS_API_VOICE: str = ""                       # 云端 TTS 音色（留空用 alloy）

    # ── 主动感知（差异检测 → 触发响应） ────────────────────
    # 负责分析被动感知数据，检测变化并触发思考/搭话等响应
    DIFFERENCE_DETECTOR_ENABLED: bool = True           # 差异检测器（1Hz 心跳扫描）
    PERCEPTION_INTERNAL_ENABLED: bool = True           # 内部状态源（未完成任务、失败任务等）

    # 差异 → 思考触发
    PERCEPTION_TRIGGER_THINK: bool = True              # 差异是否触发单次思考
    PERCEPTION_TRIGGER_MIN_INTENSITY: float = 50.0     # 触发思考的最小差异强度 (0-100)
    PERCEPTION_TRIGGER_COOLDOWN: int = 60              # 触发冷却（秒）

    # MCP 屏幕差异检测（像素级帧差）
    SCREEN_DIFF_ENABLED: bool = True                   # 屏幕帧差检测
    SCREEN_DIFF_INTERVAL: float = 1.0                  # 检测间隔（秒）
    SCREEN_DIFF_CHANGE_THRESHOLD: float = 0.01         # 最小变化面积比例 (1%)

    # 主动搭话（空闲时主动与用户交互）
    PROACTIVE_OUTREACH_ENABLED: bool = True            # 是否启用自动搭话
    PROACTIVE_OUTREACH_COOLDOWN_MINUTES: int = 15      # 搭话冷却时间（分钟）
    PROACTIVE_OUTREACH_IDLE_MINUTES: int = 15          # 触发搭话的空闲阈值（分钟）
    PROACTIVE_OUTREACH_WORK_PROMPT: str = ""           # 工作模式自定义提示词（为空则用默认）

    # 记忆配置
    MEMORY_TTL_SHORT: int = 3600  # 1 小时
    MEMORY_TTL_LONG: int = 86400  # 24 小时

    # ── 因果系统配置 ──
    CAUSAL_MAX_NODES: int = 500             # 因果图最大节点数
    CAUSAL_MAX_ANCHORS: int = 3             # 深度回忆最大锚点数
    CAUSAL_MAX_NEIGHBORS_PER_HOP: int = 10  # 每跳最大邻居数
    CAUSAL_MAX_TREE_DEPTH: int = 4          # 因果树最大深度
    CAUSAL_MAX_EVENTS_RECALL: int = 30      # 单轮事件召回上限
    CAUSAL_MIN_CONFIDENCE: float = 0.2      # 最小因果置信度（低于此的边不参与推理）
    CAUSAL_MIN_COOCCUR: int = 2             # 共现统计最小阈值
    CAUSAL_HOT_CACHE_TTL: int = 300         # 热缓存 TTL（秒）
    CAUSAL_CONFIDENCE_BOOST_DELTA: float = 0.05  # 每次回忆边置信度增量
    CAUSAL_CONFIDENCE_MAX: float = 0.99     # 最大置信度上限
    CAUSAL_UPDATE_STATS_INTERVAL: int = 10  # 增量更新统计推送（秒）

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

    _USER_CONFIG_PATH = Path.home() / ".cortex" / "settings.json"
    _USER_CONFIG_TEMPLATE = {
        "_comment": "Cortex 用户级配置，覆盖 .env 中的同名项。删除此项可恢复 .env 配置。",
        "LARGE_MODEL_API_KEY": "",
        "LARGE_MODEL_API_URL": "",
        "LARGE_MODEL_NAME": "",
        "MEDIUM_MODEL_API_KEY": "",
        "MEDIUM_MODEL_API_URL": "",
        "MEDIUM_MODEL_NAME": "",
        "SMALL_MODEL_API_KEY": "",
        "SMALL_MODEL_API_URL": "",
        "SMALL_MODEL_NAME": "",
    }

    def _ensure_user_config(self) -> None:
        """确保 ~/.cortex/settings.json 存在，不存在则自动创建模板"""
        import json
        cortex_dir = self._USER_CONFIG_PATH.parent
        if not cortex_dir.exists():
            cortex_dir.mkdir(parents=True, exist_ok=True)
            print(f"[INFO] 已创建用户配置目录: {cortex_dir}", file=sys.stderr)
        if not self._USER_CONFIG_PATH.exists():
            self._USER_CONFIG_PATH.write_text(
                json.dumps(self._USER_CONFIG_TEMPLATE, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[INFO] 已创建用户配置模板: {self._USER_CONFIG_PATH}", file=sys.stderr)
            print("[INFO] 可编辑此文件覆盖 .env 中的模型配置", file=sys.stderr)

    def model_post_init(self, __context) -> None:
        """创建必要的数据目录，并加载用户级配置覆盖"""
        db_dir = os.path.dirname(self.SQLITE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._ensure_user_config()
        self._load_user_config()
        self._apply_current_memory_lib()

    def _apply_current_memory_lib(self) -> None:
        """启动时应用 memory_libs.json 的 current 库到运行时路径。

        否则后端重启后 EventStore 会回落默认库（data/memory.db），
        即使上次已切换到其他记忆库 —— 导致"切换的库读不到/读错库"。
        backend（纯对话）的记忆路径通过委托本 settings 实时读取，无需单独同步。
        """
        try:
            data = self.get_memory_libs()
            lib = data.get("libs", {}).get(data.get("current", ""))
            if not lib:
                return
            object.__setattr__(self, "MEMORY_DB_PATH", lib["db"])
            object.__setattr__(self, "MEMORY_FAISS_INDEX", lib["faiss"])
            object.__setattr__(self, "MEMORY_ID_MAP", lib["id_map"])
        except Exception:
            pass

    def _load_user_config(self):
        """加载 ~/.cortex/settings.json，覆盖 .env 中的同名配置

        优先级: 用户 settings.json > 项目 .env > 硬编码默认值
        只覆盖 Settings 类中已定义的字段，忽略 JSON 中的未知 key。
        """
        if not self._USER_CONFIG_PATH.exists():
            return

        try:
            import json
            user_config = json.loads(self._USER_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            import sys
            print(f"[WARNING] 用户配置文件解析失败 ({self._USER_CONFIG_PATH}): {e}", file=sys.stderr)
            return

        overridden = []
        for key, value in user_config.items():
            if key.startswith("_"):
                continue
            if hasattr(type(self), 'model_fields') and key in type(self).model_fields:
                setattr(self, key, value)
                overridden.append(key)

        if overridden:
            import sys
            print(f"[INFO] 已应用用户配置 ({self._USER_CONFIG_PATH}): {', '.join(overridden)}", file=sys.stderr)

    def save_user_config(self, keys: Optional[list] = None) -> bool:
        """将可持久化配置（含自定义人设/系统提示词覆盖）写回 ~/.cortex/settings.json。

        Args:
            keys: 要保存的 key 列表；None 时保存人设相关 key（PERSONA_PROMPTS / SYSTEM_PROMPT_OVERRIDES）
        """
        import json
        import sys
        try:
            if keys is None:
                keys = ["PERSONA_PROMPTS", "SYSTEM_PROMPT_OVERRIDES"]
            path = self._USER_CONFIG_PATH
            data = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            for key in keys:
                if hasattr(type(self), "model_fields") and key in type(self).model_fields:
                    data[key] = getattr(self, key)
            # 原子写：先写临时文件再替换，防读端读到半写
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            import os as _os
            _os.replace(tmp, path)
            return True
        except Exception as e:
            print(f"[WARNING] 用户配置保存失败: {e}", file=sys.stderr)
            return False

    # ------------------------------------------------------------------
    # 记忆库管理（多记忆库切换 + 命名）
    # 配置存 ~/.cortex/memory_libs.json：
    #   {"current": "默认", "libs": {"默认": {"db": "...", "faiss": "...", "id_map": "..."}, ...}}
    # ------------------------------------------------------------------

    @property
    def _memory_libs_path(self):
        return Path.home() / ".cortex" / "memory_libs.json"

    def get_memory_libs(self) -> dict:
        """读取记忆库配置；无配置/读取失败时返回默认记忆库（当前 settings 的路径）。

        并发写（切换记忆库）可能读到半写内容 → JSON 解析失败 → 重试一次。
        """
        import json
        import time
        for _ in range(2):
            try:
                if self._memory_libs_path.exists():
                    data = json.loads(self._memory_libs_path.read_text(encoding="utf-8"))
                    if data.get("libs"):
                        return data
                # 无配置或 libs 为空 → 返回默认库（不必重试）
                break
            except Exception:
                # 读到半写/损坏内容 → 稍等后重试一次
                time.sleep(0.05)
        return {
            "current": "默认",
            "libs": {
                "默认": {
                    "db": self.MEMORY_DB_PATH,
                    "faiss": self.MEMORY_FAISS_INDEX,
                    "id_map": self.MEMORY_ID_MAP,
                }
            },
        }

    def _save_memory_libs(self, data: dict) -> None:
        import json
        import os
        import sys
        try:
            path = self._memory_libs_path
            path.parent.mkdir(parents=True, exist_ok=True)
            # 原子写：先写临时文件再 os.replace，读端永远不会看到半写内容
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as e:
            print(f"[WARNING] 记忆库配置保存失败: {e}", file=sys.stderr)

    def _reset_memory_singletons(self) -> None:
        """切换记忆库后重置事件记忆相关单例，使其按新路径重新加载"""
        try:
            import modules.memory.event_store as es_mod
            es_mod.EventStore._instance = None
        except Exception:
            pass
        try:
            import modules.memory.event_retrieval as er_mod
            er_mod.EventRetrieval._instance = None
            # 生产检索单例（get_event_retrieval() 返回），漏重置会导致切库后仍读旧库
            er_mod._retrieval_instance = None
        except Exception:
            pass
        # Embedding 模型与记忆库无关（同一个模型），切库保留实例，避免反复重载模型
        # （旧 backend 时代因两套单例需重置；合并后唯一单例可复用）
        # 事件提取器缓存旧 EventStore，需一并重置
        try:
            import modules.memory.event_reducer as red_mod
            red_mod._reducer_instance = None
        except Exception:
            pass

    def switch_memory_lib(self, name: str) -> bool:
        """切换到指定记忆库（更新运行时路径 + 重置记忆单例，原子化）"""
        with _MEMORY_SWITCH_LOCK:
            data = self.get_memory_libs()
            lib = data.get("libs", {}).get(name)
            if not lib:
                return False
            data["current"] = name
            self._save_memory_libs(data)
            object.__setattr__(self, "MEMORY_DB_PATH", lib["db"])
            object.__setattr__(self, "MEMORY_FAISS_INDEX", lib["faiss"])
            object.__setattr__(self, "MEMORY_ID_MAP", lib["id_map"])
            self._reset_memory_singletons()
        return True

    def create_memory_lib(self, name: str) -> Optional[dict]:
        """创建并命名一个新记忆库（默认路径 data/memory_{safe}.*），并切换过去"""
        import re
        data = self.get_memory_libs()
        if name in data.get("libs", {}):
            return None
        base = str(Path(self.MEMORY_DB_PATH).parent)
        safe = re.sub(r'[^0-9A-Za-z_\-\u4e00-\u9fff]', '', name) or "lib"
        lib = {
            "db": str(Path(base) / f"memory_{safe}.db"),
            "faiss": str(Path(base) / f"events_faiss_{safe}.index"),
            "id_map": str(Path(base) / f"events_id_map_{safe}.json"),
        }
        data.setdefault("libs", {})[name] = lib
        data["current"] = name
        self._save_memory_libs(data)
        self.switch_memory_lib(name)
        return lib

    def rename_memory_lib(self, old_name: str, new_name: str) -> bool:
        """重命名记忆库"""
        data = self.get_memory_libs()
        libs = data.get("libs", {})
        if old_name not in libs or new_name in libs:
            return False
        libs[new_name] = libs.pop(old_name)
        if data.get("current") == old_name:
            data["current"] = new_name
        self._save_memory_libs(data)
        return True

    def delete_memory_lib(self, name: str) -> bool:
        """删除记忆库（允许删除任意库，包括默认库）。

        删除后的兜底保证至少有一个记忆库可用：
        - 删的是当前库 → 自动切到剩余库（优先"默认"，否则第一个）
        - 删到最后一个库 → 自动重新生成一个新的"默认"库
        """
        data = self.get_memory_libs()
        libs = data.get("libs", {})
        if name not in libs:
            return False
        libs.pop(name, None)
        # 兜底：删到最后一个库 → 重新生成默认库
        if not libs:
            base = str(Path(self.MEMORY_DB_PATH).parent)
            libs["默认"] = {
                "db": str(Path(base) / "memory.db"),
                "faiss": str(Path(base) / "events_faiss.index"),
                "id_map": str(Path(base) / "events_id_map.json"),
            }
            data["current"] = "默认"
        elif data.get("current") == name:
            # 删的是当前库 → 切到剩余库（优先默认，否则任意一个）
            if "默认" in libs:
                data["current"] = "默认"
            else:
                data["current"] = sorted(libs.keys())[0]
        self._save_memory_libs(data)
        self.switch_memory_lib(data["current"])
        return True
        return True

    def memory_lib_event_count(self, name: str) -> int:
        """返回指定记忆库的事件数量（用于列表展示）"""
        import os
        lib = self.get_memory_libs().get("libs", {}).get(name)
        if not lib:
            return 0
        # DB 文件不存在（新建未用过）→ 直接返回 0，避免 sqlite3.connect 创建空 DB
        if not os.path.exists(lib["db"]):
            return 0
        try:
            import sqlite3
            conn = sqlite3.connect(lib["db"])
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM events")
                return cur.fetchone()[0]
            finally:
                conn.close()
        except Exception:
            return 0

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
