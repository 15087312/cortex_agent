"""YAML 配置加载器 — 缓存 + 热重载"""
from pathlib import Path
from typing import Dict, Any, Optional
import threading


class PromptLoader:
    """加载 config/prompts/*.yaml，缓存，支持 reload()"""

    def __init__(self):
        self._base_dir = Path(__file__).resolve().parent
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def load(self, name: str) -> Optional[Dict[str, Any]]:
        """加载指定 YAML 文件（缓存）"""
        if name in self._cache:
            return self._cache[name]
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            data = self._read_yaml(name)
            if data is not None:
                self._cache[name] = data
            return data

    def reload(self, name: str = None):
        """热重载：清除缓存，下次 load 重新读取"""
        with self._lock:
            if name:
                self._cache.pop(name, None)
            else:
                self._cache.clear()

    def _read_yaml(self, name: str) -> Optional[Dict[str, Any]]:
        path = self._base_dir / f"{name}.yaml"
        if not path.exists():
            return None
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            import sys
            print(f"[WARNING] 加载 prompt 配置失败 ({path}): {e}", file=sys.stderr)
            return None


_loader = None
_loader_lock = threading.Lock()


def get_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        with _loader_lock:
            if _loader is None:
                _loader = PromptLoader()
    return _loader
