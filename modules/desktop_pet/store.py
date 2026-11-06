"""桌宠独立存储 — 独立 SQLite（data/pet.db），与主库 memory.db 完全隔离

桌宠不再作为 ChatSession（pet_main）存在，改为独立页面形态，因此配置、
对话历史、记忆全部落在自己的库中：

- pet_config  桌宠配置（单行）：独立人设 / 独立 API 配置 / 循环类型
- pet_history 桌宠对话历史（不再写入 sessions 表的 pet_main 会话）
- pet_memory  桌宠独立记忆条目（不共享全局 event_store）

复用 utils/logger 与 config.settings 的路径约定，但连接与表完全独立。
"""
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("pet_store")

# 项目根（独立于 modules.database，避免引入 sqlalchemy 依赖）
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # <project>/modules/desktop_pet/store.py → <project>
PET_DB_PATH = os.environ.get("PET_DB_PATH") or str(PROJECT_ROOT / "data" / "pet.db")

# 桌宠人设默认值（可经 /stream/pet/config 修改）
DEFAULT_PERSONA = (
    "你是桌面上的 AI 桌宠助手，像 Siri 一样用语音和用户交流。"
    "性格友善、可爱、贴心。回答要简短自然（通常一两句话），适合语音朗读。"
    "用户可能会给你送礼物或和你互动，请配合演出、开心地回应。"
    "用中文回答。"
)


class PetStore:
    """桌宠独立 SQLite 存储（单例）"""

    _instance: Optional["PetStore"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = PET_DB_PATH):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()
        self._ensure_default()

    @classmethod
    def get_instance(cls) -> "PetStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 连接 ──

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pet_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    persona TEXT NOT NULL,
                    api_url TEXT NOT NULL DEFAULT '',
                    api_key TEXT NOT NULL DEFAULT '',
                    api_model TEXT NOT NULL DEFAULT '',
                    api_format TEXT NOT NULL DEFAULT '',
                    api_provider TEXT NOT NULL DEFAULT '',
                    loop_type TEXT NOT NULL DEFAULT 'direct',
                    memory_enabled INTEGER NOT NULL DEFAULT 1,
                    min_think_interval REAL NOT NULL DEFAULT 5,
                    tools_enabled INTEGER NOT NULL DEFAULT 1,
                    max_history INTEGER NOT NULL DEFAULT 8,
                    max_output_tokens INTEGER NOT NULL DEFAULT 512,
                    max_loop_rounds INTEGER NOT NULL DEFAULT 0,
                    idle_backoff_max REAL NOT NULL DEFAULT 8,
                    voice_enabled INTEGER NOT NULL DEFAULT 0,
                    realtime_voice INTEGER NOT NULL DEFAULT 0,
                    active_persona_id INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pet_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    persona_id INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pet_personas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pet_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL DEFAULT 'fact',
                    text TEXT NOT NULL,
                    persona_id INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pet_memory_created ON pet_memory(created_at DESC);
                """
            )
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """旧库迁移：pet_config 缺新列时补列（不重建表）"""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pet_config)")}
        additions = [
            ("min_think_interval", "REAL NOT NULL DEFAULT 5"),
            ("tools_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("max_history", "INTEGER NOT NULL DEFAULT 8"),
            ("max_output_tokens", "INTEGER NOT NULL DEFAULT 512"),
            ("max_loop_rounds", "INTEGER NOT NULL DEFAULT 0"),
            ("idle_backoff_max", "REAL NOT NULL DEFAULT 8"),
            ("voice_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("realtime_voice", "INTEGER NOT NULL DEFAULT 0"),
            ("active_persona_id", "INTEGER NOT NULL DEFAULT 1"),
        ]
        for name, ddl in additions:
            if name not in cols:
                conn.execute(f"ALTER TABLE pet_config ADD COLUMN {name} {ddl}")

        hcols = {r["name"] for r in conn.execute("PRAGMA table_info(pet_history)")}
        if "persona_id" not in hcols:
            conn.execute("ALTER TABLE pet_history ADD COLUMN persona_id INTEGER NOT NULL DEFAULT 1")
        mcols = {r["name"] for r in conn.execute("PRAGMA table_info(pet_memory)")}
        if "persona_id" not in mcols:
            conn.execute("ALTER TABLE pet_memory ADD COLUMN persona_id INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pet_memory_persona ON pet_memory(persona_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pet_history_persona ON pet_history(persona_id, id)"
        )

        # 人设表：旧库首次升级时用当前 persona 建默认人设
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pet_personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )"""
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM pet_personas").fetchone()["c"]
        if count == 0:
            row = conn.execute("SELECT persona FROM pet_config WHERE id = 1").fetchone()
            prompt = (row["persona"] if row else "") or DEFAULT_PERSONA
            cur = conn.execute(
                "INSERT INTO pet_personas (name, prompt, created_at) VALUES (?, ?, ?)",
                ("默认人设", prompt, time.time()),
            )
            conn.execute(
                "UPDATE pet_config SET active_persona_id = ? WHERE id = 1",
                (cur.lastrowid,),
            )
        else:
            cfg = conn.execute("SELECT active_persona_id FROM pet_config WHERE id = 1").fetchone()
            pid = int(cfg["active_persona_id"]) if cfg and cfg["active_persona_id"] else 0
            exists = conn.execute("SELECT id FROM pet_personas WHERE id = ?", (pid,)).fetchone()
            if not exists:
                first = conn.execute("SELECT id FROM pet_personas ORDER BY id LIMIT 1").fetchone()
                if first:
                    conn.execute("UPDATE pet_config SET active_persona_id = ? WHERE id = 1", (first["id"],))
        conn.commit()

    # ── 配置（单行） ──

    _DEFAULT_ROW = (
        DEFAULT_PERSONA, "", "", "", "", "", "direct", 1, 5.0, 1, 8, 512, 3, 8.0, 0, 0, 1, 0.0,
    )

    _CONFIG_KEYS = [
        "persona", "api_url", "api_key", "api_model", "api_format", "api_provider",
        "loop_type", "memory_enabled", "min_think_interval", "tools_enabled",
        "max_history", "max_output_tokens", "max_loop_rounds", "idle_backoff_max",
        "voice_enabled", "realtime_voice", "active_persona_id",
    ]

    def get_config(self) -> Dict[str, Any]:
        conn = self._connect()
        try:
            self._migrate(conn)
            row = conn.execute("SELECT * FROM pet_config WHERE id = 1").fetchone()
        finally:
            conn.close()
        if row is None:
            self._ensure_default()
            return {
                "persona": DEFAULT_PERSONA,
                "api_url": "", "api_key": "", "api_model": "",
                "api_format": "", "api_provider": "",
                "loop_type": "direct", "memory_enabled": True,
                "min_think_interval": 5.0, "tools_enabled": True,
                "max_history": 8, "max_output_tokens": 512, "max_loop_rounds": 0,
                "idle_backoff_max": 8.0, "voice_enabled": False,
                "realtime_voice": False, "active_persona_id": 1,
            }
        out = dict(row)
        # 布尔列统一为 bool（get_config 与 update_config 返回结构一致）
        for key in ("memory_enabled", "tools_enabled", "voice_enabled", "realtime_voice"):
            if key in out:
                out[key] = bool(out[key])
        return out

    def _ensure_default(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO pet_config (id, persona, api_url, api_key, api_model,"
                " api_format, api_provider, loop_type, memory_enabled, min_think_interval,"
                " tools_enabled, max_history, max_output_tokens, max_loop_rounds,"
                " idle_backoff_max, voice_enabled, realtime_voice, active_persona_id, updated_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                self._DEFAULT_ROW,
            )
            conn.commit()
        finally:
            conn.close()

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """更新配置（仅更新传入字段），返回合并后的完整配置"""
        cur = self.get_config()
        allowed = set(self._CONFIG_KEYS)
        for k, v in patch.items():
            if k not in allowed:
                continue
            if k == "memory_enabled":
                cur[k] = 1 if v else 0
            elif k == "tools_enabled":
                cur[k] = 1 if v else 0
            elif k == "voice_enabled":
                cur[k] = 1 if v else 0
            elif k == "realtime_voice":
                cur[k] = 1 if v else 0
            elif k in ("min_think_interval",):
                try:
                    cur[k] = max(0.0, float(v or 0))
                except (TypeError, ValueError):
                    cur[k] = 5.0
            elif k == "idle_backoff_max":
                # 空拍退避倍率上限：≥1，1 = 关闭退避
                try:
                    cur[k] = max(1.0, float(v or 1))
                except (TypeError, ValueError):
                    cur[k] = 8.0
            elif k in ("max_history", "max_output_tokens"):
                try:
                    cur[k] = max(1, int(v or 1))
                except (TypeError, ValueError):
                    cur[k] = self._DEFAULT_ROW[self._CONFIG_KEYS.index(k)]
            elif k == "max_loop_rounds":
                # 0 = 持续思考循环（每次思考后再次思考，直到停止/上下文满）；>0 = 轮数上限
                try:
                    cur[k] = max(0, int(v or 0))
                except (TypeError, ValueError):
                    cur[k] = 0
            elif k == "active_persona_id":
                try:
                    cur[k] = max(1, int(v or 1))
                except (TypeError, ValueError):
                    cur[k] = 1
            else:
                cur[k] = str(v or "")
        self._ensure_default()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE pet_config SET persona=?, api_url=?, api_key=?, api_model=?,"
                " api_format=?, api_provider=?, loop_type=?, memory_enabled=?,"
                " min_think_interval=?, tools_enabled=?, max_history=?,"
                " max_output_tokens=?, max_loop_rounds=?, idle_backoff_max=?, voice_enabled=?,"
                " realtime_voice=?, active_persona_id=?, updated_at=? WHERE id=1",
                (
                    cur["persona"], cur["api_url"], cur["api_key"], cur["api_model"],
                    cur["api_format"], cur["api_provider"], cur["loop_type"],
                    cur["memory_enabled"], cur["min_think_interval"],
                    cur["tools_enabled"], cur["max_history"],
                    cur["max_output_tokens"], cur["max_loop_rounds"],
                    cur["idle_backoff_max"], cur["voice_enabled"], cur["realtime_voice"],
                    cur.get("active_persona_id", 1), time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        out = dict(cur)
        out["memory_enabled"] = bool(out["memory_enabled"])
        out["tools_enabled"] = bool(out["tools_enabled"])
        out["voice_enabled"] = bool(out.get("voice_enabled", 0))
        out["realtime_voice"] = bool(out.get("realtime_voice", 0))
        out["min_think_interval"] = float(out["min_think_interval"])
        out["idle_backoff_max"] = float(out.get("idle_backoff_max", 8.0))
        out["max_history"] = int(out["max_history"])
        out["max_output_tokens"] = int(out["max_output_tokens"])
        out["max_loop_rounds"] = int(out["max_loop_rounds"])
        return out

    # ── 对话历史 ──

    # ── 人设 ──

    def list_personas(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, prompt, created_at FROM pet_personas ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def get_active_persona_id(self) -> int:
        """当前人设 id；配置缺失/指向已删除人设时回退到最早的一个。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT active_persona_id FROM pet_config WHERE id = 1"
            ).fetchone()
            pid = int(row["active_persona_id"]) if row and row["active_persona_id"] else 1
            ok = conn.execute("SELECT id FROM pet_personas WHERE id = ?", (pid,)).fetchone()
            if ok:
                return pid
            first = conn.execute("SELECT id FROM pet_personas ORDER BY id LIMIT 1").fetchone()
            return int(first["id"]) if first else pid
        finally:
            conn.close()

    def get_persona(self, pid: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, name, prompt, created_at FROM pet_personas WHERE id = ?",
                (int(pid),),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def add_persona(self, name: str, prompt: str = "") -> Optional[Dict[str, Any]]:
        name = (name or "").strip() or "新人设"
        prompt = (prompt or "").strip() or DEFAULT_PERSONA
        conn = self._connect()
        try:
            dup = conn.execute(
                "SELECT id FROM pet_personas WHERE lower(name) = lower(?)", (name,)
            ).fetchone()
            if dup:
                return None
            cur = conn.execute(
                "INSERT INTO pet_personas (name, prompt, created_at) VALUES (?, ?, ?)",
                (name, prompt, time.time()),
            )
            conn.commit()
            pid = cur.lastrowid
        finally:
            conn.close()
        return self.get_persona(pid)

    def update_persona(self, pid: int, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cur = self.get_persona(pid)
        if not cur:
            return None
        name = (patch.get("name") or cur["name"]).strip() or cur["name"]
        prompt = patch.get("prompt")
        prompt = cur["prompt"] if prompt is None else str(prompt)
        conn = self._connect()
        try:
            dup = conn.execute(
                "SELECT id FROM pet_personas WHERE lower(name) = lower(?) AND id != ?",
                (name, int(pid)),
            ).fetchone()
            if dup:
                return None
            conn.execute(
                "UPDATE pet_personas SET name = ?, prompt = ? WHERE id = ?",
                (name, prompt, int(pid)),
            )
            # 当前人设的提示词即时同步到 config.persona（引擎每轮读取）
            cfg = conn.execute("SELECT active_persona_id FROM pet_config WHERE id = 1").fetchone()
            if cfg and int(cfg["active_persona_id"] or 0) == int(pid):
                conn.execute(
                    "UPDATE pet_config SET persona = ?, updated_at = ? WHERE id = 1",
                    (prompt, time.time()),
                )
            conn.commit()
        finally:
            conn.close()
        return self.get_persona(pid)

    def set_active_persona(self, pid: int) -> Optional[Dict[str, Any]]:
        persona = self.get_persona(pid)
        if not persona:
            return None
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE pet_config SET active_persona_id = ?, persona = ?, updated_at = ? WHERE id = 1",
                (int(pid), persona["prompt"], time.time()),
            )
            conn.commit()
        finally:
            conn.close()
        return persona

    def delete_persona(self, pid: int) -> bool:
        """删除人设及其专属会话；禁止删除最后一个人设。"""
        personas = self.list_personas()
        if len(personas) <= 1:
            return False
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM pet_personas WHERE id = ?", (int(pid),)
            ).fetchone()
            if not row:
                return False
            active = conn.execute(
                "SELECT active_persona_id FROM pet_config WHERE id = 1"
            ).fetchone()
            conn.execute("DELETE FROM pet_history WHERE persona_id = ?", (int(pid),))
            conn.execute("DELETE FROM pet_memory WHERE persona_id = ?", (int(pid),))
            conn.execute("DELETE FROM pet_personas WHERE id = ?", (int(pid),))
            if active and int(active["active_persona_id"] or 0) == int(pid):
                nxt = conn.execute("SELECT id, prompt FROM pet_personas ORDER BY id LIMIT 1").fetchone()
                if nxt:
                    conn.execute(
                        "UPDATE pet_config SET active_persona_id = ?, persona = ?, updated_at = ? WHERE id = 1",
                        (nxt["id"], nxt["prompt"], time.time()),
                    )
            conn.commit()
            return True
        finally:
            conn.close()

    # ── 对话历史（按人设隔离） ──

    # @link:block PetHistoryWrite = Write
    def add_history(self, role: str, content: str, persona_id: Optional[int] = None) -> None:
        pid = int(persona_id) if persona_id else self.get_active_persona_id()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO pet_history (role, content, persona_id, created_at) VALUES (?, ?, ?, ?)",
                (role, content, pid, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_history(self, limit: int = 20, persona_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """某个人设的最近对话（默认当前人设），含 id 供前端逐条删除。

        取最新的 limit 条（ORDER BY id DESC 内层筛选，外层 ASC 还原时间正序）。
        """
        pid = int(persona_id) if persona_id else self.get_active_persona_id()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM ("
                "  SELECT id, role, content FROM pet_history"
                "  WHERE persona_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (pid, max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def delete_history(self, mid: int) -> bool:
        """删除一条桌宠对话（按 pet_history.id）；不存在返回 False"""
        conn = self._connect()
        try:
            c = conn.execute("DELETE FROM pet_history WHERE id = ?", (int(mid),))
            conn.commit()
            return c.rowcount > 0
        finally:
            conn.close()

    def clear_history(self, persona_id: Optional[int] = None) -> None:
        """清空指定人设（默认当前人设）的对话历史。"""
        pid = int(persona_id) if persona_id else self.get_active_persona_id()
        conn = self._connect()
        try:
            conn.execute("DELETE FROM pet_history WHERE persona_id = ?", (pid,))
            conn.commit()
        finally:
            conn.close()

    # ── 独立记忆 ──

    def add_memory(self, kind: str, text: str, persona_id: Optional[int] = None) -> Dict[str, Any]:
        pid = int(persona_id) if persona_id else self.get_active_persona_id()
        conn = self._connect()
        try:
            c = conn.execute(
                "INSERT INTO pet_memory (kind, text, persona_id, created_at) VALUES (?, ?, ?, ?)",
                (kind or "fact", text, pid, time.time()),
            )
            conn.commit()
            mid = c.lastrowid
        finally:
            conn.close()
        return {"id": mid, "kind": kind or "fact", "text": text, "persona_id": pid}

    def list_memory(self, limit: int = 50, persona_id: Optional[int] = None) -> List[Dict[str, Any]]:
        pid = int(persona_id) if persona_id else self.get_active_persona_id()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, kind, text, created_at FROM pet_memory"
                " WHERE persona_id = ? ORDER BY created_at DESC LIMIT ?",
                (pid, max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def delete_memory(self, mid: int) -> bool:
        conn = self._connect()
        try:
            c = conn.execute("DELETE FROM pet_memory WHERE id = ?", (int(mid),))
            conn.commit()
            return c.rowcount > 0
        finally:
            conn.close()

    def search_memory(self, limit: int = 3, persona_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """某个人设的最近记忆候选池（向量排序由 memory.py 完成）。"""
        pid = int(persona_id) if persona_id else self.get_active_persona_id()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, kind, text, created_at FROM pet_memory"
                " WHERE persona_id = ? ORDER BY created_at DESC LIMIT 200",
                (pid,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
