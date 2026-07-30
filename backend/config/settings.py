"""
Global configuration — loads from .env + optional ~/.cortex-mini/settings.json override.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Set
import os
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
    MODEL_API_KEY: str = ""
    MODEL_API_URL: str = ""
    PROXY_URL: str = ""
    MODEL_NAME: str = ""
    MODEL_API_FORMAT: str = ""  # "openai" / "anthropic" / "dashscope" / auto-detect
    MODEL_MAX_TOKENS: int = 4096
    MODEL_TEMPERATURE: float = 0.7

    # ── Embedding ──
    EMBEDDING_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_CACHE_FOLDER: str = "data/embeddings"
    EMBEDDING_LOCAL_FILES_ONLY: bool = False
    HF_MIRROR: str = ""

    # ── Memory System ──
    MEMORY_DB_PATH: str = "data/memory.db"
    MEMORY_FAISS_INDEX: str = "data/events_faiss.index"
    MEMORY_ID_MAP: str = "data/events_id_map.json"
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
