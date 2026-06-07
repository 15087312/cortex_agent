"""
预期差异源

检测内容：
- 注册的预期检查函数返回 False
- 预期 vs 实际状态不一致
"""
import time
import threading
from typing import List, Dict, Optional, Callable, Any

from modules.difference_detector.sources.base import DifferenceSource
from modules.difference_detector.models import Difference

EXPECTATION_TTL = 30 * 60  # 30 分钟


class ExpectationDifferenceSource(DifferenceSource):
    """预期差异源 — 注册检查函数，检测预期 vs 实际不一致"""

    def __init__(self):
        super().__init__()
        self._checks: Dict[str, Callable[[], bool]] = {}
        self._labels: Dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def source_type(self) -> str:
        return "expectation"

    def register_check(self, name: str, check_fn: Callable[[], bool], label: str = "") -> None:
        """注册预期检查函数

        Args:
            name: 检查名称 (唯一标识)
            check_fn: 检查函数，返回 True=符合预期, False=预期差异
            label: 人类可读标签
        """
        with self._lock:
            self._checks[name] = check_fn
            self._labels[name] = label or name

    def unregister_check(self, name: str) -> bool:
        with self._lock:
            if name in self._checks:
                del self._checks[name]
                del self._labels[name]
                return True
        return False

    def detect(self) -> List[Difference]:
        differences = []

        with self._lock:
            checks = dict(self._checks)
            labels = dict(self._labels)

        for name, check_fn in checks.items():
            try:
                ok = check_fn()
                if not ok:
                    differences.append(Difference(
                        source_type="expectation",
                        category=f"expectation_failed:{name}",
                        intensity=35.0,
                        ttl=EXPECTATION_TTL,
                        payload={
                            "check_name": name,
                            "label": labels.get(name, name),
                            "expected": True,
                            "actual": False,
                        },
                    ))
            except Exception as e:
                differences.append(Difference(
                    source_type="expectation",
                    category=f"expectation_error:{name}",
                    intensity=30.0,
                    ttl=EXPECTATION_TTL,
                    payload={
                        "check_name": name,
                        "label": labels.get(name, name),
                        "error": str(e),
                    },
                ))

        return differences

    @property
    def check_count(self) -> int:
        with self._lock:
            return len(self._checks)
