"""文件差异源 — 监控工作目录文件变化（mtime/大小），由 PERCEPTION_FILE_ENABLED 控制"""
from pathlib import Path
from typing import List

from modules.perception.difference.models import Difference
from modules.perception.difference.sources.base import DifferenceSource

_IGNORE = {".git", "node_modules", ".venv", "data", ".pytest_cache", "__pycache__", "dist", ".next"}


class FileDifferenceSource(DifferenceSource):
    """监控项目根一级文件 + 一级子目录文件的变化（避免深度扫描的性能开销）"""

    def __init__(self, root: str = None):
        super().__init__()
        root = root or str(Path(__file__).resolve().parents[4])
        self._root = Path(root)
        self._known: dict = {}
        self._first = True

    @property
    def source_type(self) -> str:
        return "file"

    def _iter_files(self):
        try:
            for p in self._root.iterdir():
                if p.name in _IGNORE or p.name.startswith("."):
                    continue
                if p.is_file():
                    yield p
                elif p.is_dir():
                    for child in p.iterdir():
                        if child.is_file() and child.name not in _IGNORE:
                            yield child
        except Exception:
            return

    def detect(self) -> List[Difference]:
        diffs: List[Difference] = []
        state: dict = {}
        for p in self._iter_files():
            try:
                st = p.stat()
                state[str(p)] = (st.st_mtime, st.st_size)
            except Exception:
                continue
        if not self._first:
            for path, sig in state.items():
                prev = self._known.get(path)
                if prev is not None and prev != sig:
                    diffs.append(Difference(
                        source_type="file",
                        category="file_modified",
                        intensity=15.0,
                        payload={"path": path, "name": Path(path).name},
                    ))
            for path in list(self._known.keys()):
                if path not in state:
                    diffs.append(Difference(
                        source_type="file",
                        category="file_deleted",
                        intensity=20.0,
                        payload={"path": path, "name": Path(path).name},
                    ))
        self._known = state
        self._first = False
        return diffs
