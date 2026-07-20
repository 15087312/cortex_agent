"""~/.cordex/ 存储管理 — 日志、编辑历史、技能、计划、TODO"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

OPENER_DIR = Path.home() / ".cordex"
DIRS = {
    "debug": OPENER_DIR / "debug",
    "skills": OPENER_DIR / "skills",
    "plans": OPENER_DIR / "plans",
    "todos": OPENER_DIR / "todos",
    "edits": OPENER_DIR / "edits",
    "memories": OPENER_DIR / "memories",
    "projects": OPENER_DIR / "projects",
}

for d in DIRS.values():
    d.mkdir(parents=True, exist_ok=True)


def write_debug_log(entry: str, session_id: str = ""):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{session_id[:8]}" if session_id else ""
    path = DIRS["debug"] / f"debug_{ts}{tag}.log"
    path.write_text(entry, encoding="utf-8")


def write_edit_history(session_id: str, edits: List[Dict[str, Any]]):
    path = DIRS["edits"] / f"{session_id}.json"
    path.write_text(json.dumps(edits, ensure_ascii=False, indent=2), encoding="utf-8")


def read_edit_history(session_id: str) -> List[Dict[str, Any]]:
    path = DIRS["edits"] / f"{session_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def read_todo() -> List[Dict[str, Any]]:
    path = DIRS["todos"] / "todos.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []
