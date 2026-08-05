"""
Global configuration — loads from .env + optional ~/.cortex-mini/settings.json override.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Set
import json


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Fields modifiable at runtime via PUT /config/{key}
    _MODIFIABLE_FIELDS: Set[str] = {
        "LOG_LEVEL",
        "MODEL_TEMPERATURE",
        "MODEL_MAX_TOKENS",
        "CHAT_MAX_ROUNDS",
        "MEMORY_REDUCE_ENABLED",
        # Frontend UI settings (persisted to settings.json)
        "launch_at_startup",
        "prevent_sleep",
        "show_filename_in_gallery",
        "allow_geolocation",
        "shortcut_keys",
        "storage_path",
    }

    # ── Model Provider ──
    # 模型 API 配置（key/url/name/format）不再在 backend 保存副本——
    # 通过下方 property 实时委托主 settings 的 LARGE_MODEL_*（单一事实来源），
    # 避免"双 settings 各自持有运行时状态 + 构造期互相 import"的循环依赖。
    MODEL_MAX_TOKENS: int = 4096
    MODEL_TEMPERATURE: float = 0.7

    # ── Embedding ──
    EMBEDDING_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_CACHE_FOLDER: str = "data/embeddings"
    EMBEDDING_LOCAL_FILES_ONLY: bool = False
    HF_MIRROR: str = ""

    # ── Memory System ──
    # 记忆库路径同模型配置：委托主 settings 的 MEMORY_DB_PATH/FAISS/ID_MAP，
    # 切换记忆库后纯对话链路自动跟随当前库，无需手动同步。
    MEMORY_REDUCE_ENABLED: bool = True

    # ── Causal System ──
    CAUSAL_DB_PATH: str = "data/causal.db"
    CAUSAL_MAX_ANCHORS: int = 3
    CAUSAL_MAX_NEIGHBORS_PER_HOP: int = 10
    CAUSAL_MAX_TREE_DEPTH: int = 4
    CAUSAL_MAX_EVENTS_RECALL: int = 30
    CAUSAL_MIN_CONFIDENCE: float = 0.2

    # ── Chat Engine ──
    CHAT_MAX_ROUNDS: int = 5
    CHAT_CONTEXT_MAX_MESSAGES: int = 50
    CHAT_CONTEXT_MAX_TOKENS: int = 8000

    # ── Server ──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:5173"
    LOG_LEVEL: str = "INFO"

    # ── Identity ──
    ASSISTANT_NAME: str = "Cortex"
    USER_NAME: str = "User"

    # ── Frontend UI (persisted via PUT /config/{key}) ──
    launch_at_startup: bool = True
    prevent_sleep: bool = False
    show_filename_in_gallery: bool = False
    allow_geolocation: bool = False
    shortcut_keys: str = "⌥ + T"
    storage_path: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._load_user_overrides()

    # ------------------------------------------------------------------
    # 委托主 settings（单一事实来源）
    # 模型 API 配置与记忆库路径实时读主 settings，改配置/切库即时生效，
    # 无需手动同步；backend 独立部署时主 settings 不可用则回退默认值。
    # ------------------------------------------------------------------

    @staticmethod
    def _main():
        try:
            import config.settings as _ms
            return getattr(_ms, "settings", None)
        except Exception:
            return None

    @property
    def MODEL_API_KEY(self) -> str:
        ms = self._main()
        return (ms.LARGE_MODEL_API_KEY or "") if ms is not None else ""

    @property
    def MODEL_API_URL(self) -> str:
        ms = self._main()
        return (ms.LARGE_MODEL_API_URL or "") if ms is not None else ""

    @property
    def MODEL_NAME(self) -> str:
        ms = self._main()
        return (ms.LARGE_MODEL_NAME or "") if ms is not None else ""

    @property
    def MODEL_API_FORMAT(self) -> str:
        ms = self._main()
        return (ms.LARGE_MODEL_API_FORMAT or "") if ms is not None else ""

    @property
    def MEMORY_DB_PATH(self) -> str:
        ms = self._main()
        if ms is not None:
            return getattr(ms, "MEMORY_DB_PATH", "") or "data/memory.db"
        return "data/memory.db"

    @property
    def MEMORY_FAISS_INDEX(self) -> str:
        ms = self._main()
        if ms is not None:
            return getattr(ms, "MEMORY_FAISS_INDEX", "") or "data/events_faiss.index"
        return "data/events_faiss.index"

    @property
    def MEMORY_ID_MAP(self) -> str:
        ms = self._main()
        if ms is not None:
            return getattr(ms, "MEMORY_ID_MAP", "") or "data/events_id_map.json"
        return "data/events_id_map.json"

    def _load_user_overrides(self):
        """Load user-level overrides from ~/.cortex-mini/settings.json"""
        user_settings_path = Path.home() / ".cortex-mini" / "settings.json"
        if user_settings_path.exists():
            try:
                with open(user_settings_path, "r") as f:
                    overrides = json.load(f)
                for key, value in overrides.items():
                    if hasattr(self, key) and key != "_MODIFIABLE_FIELDS":
                        setattr(self, key, value)
            except Exception:
                pass

    def save_overrides(self):
        """Persist modifiable fields from _MODIFIABLE_FIELDS to ~/.cortex-mini/settings.json"""
        user_settings_path = Path.home() / ".cortex-mini" / "settings.json"
        user_settings_path.parent.mkdir(parents=True, exist_ok=True)
        overrides = {
            key: getattr(self, key)
            for key in self._MODIFIABLE_FIELDS
            if hasattr(self, key)
        }
        try:
            with open(user_settings_path, "w", encoding="utf-8") as f:
                json.dump(overrides, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    @field_validator("PORT")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1024 <= v <= 65535):
            raise ValueError("PORT must be between 1024 and 65535")
        return v

    def is_modifiable(self, field_name: str) -> bool:
        return field_name in self._MODIFIABLE_FIELDS


# Global singleton
settings = Settings()
