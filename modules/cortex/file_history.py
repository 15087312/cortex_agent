"""
FileHistory — 文件修改历史（参考 opencode 设计）

存储: ~/.cortex/history/files.db (SQLite)
每条记录保存文件的完整内容，支持按版本回滚。

版本号: initial → v1 → v2 → ...
  initial: AI 修改前的原始内容（首次记录时为空字符串表示文件新建）
  v1, v2..: 每次修改后的内容
"""
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any


_DB_PATH = Path.home() / ".cortex" / "history" / "files.db"


class FileHistory:
    """文件修改历史 — SQLite 单例"""

    _instance: Optional["FileHistory"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = None):
        self._db_path = str(db_path or _DB_PATH)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    @classmethod
    def get_instance(cls) -> "FileHistory":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_db()
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_versions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                path TEXT NOT NULL,
                content TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(session_id, path, version)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fv_session ON file_versions(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fv_path ON file_versions(path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fv_session_path ON file_versions(session_id, path)")
        conn.commit()

    # ── 写入 ──

    def record_initial(self, session_id: str, file_path: str, content: str = "") -> str:
        """记录文件的初始状态（AI 修改前），返回版本 ID"""
        conn = self._get_conn()
        # 检查是否已有 initial
        existing = conn.execute(
            "SELECT id FROM file_versions WHERE session_id=? AND path=? AND version='initial'",
            (session_id, file_path),
        ).fetchone()
        if existing:
            return existing["id"]

        vid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO file_versions (id, session_id, path, content, version, created_at) "
            "VALUES (?, ?, ?, ?, 'initial', ?)",
            (vid, session_id, file_path, content, int(time.time())),
        )
        conn.commit()
        return vid

    def record_version(self, session_id: str, file_path: str, content: str) -> str:
        """记录文件的新版本，返回版本 ID"""
        conn = self._get_conn()
        # 获取当前最大版本号
        row = conn.execute(
            "SELECT version FROM file_versions WHERE session_id=? AND path=? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id, file_path),
        ).fetchone()

        if row is None:
            # 首次记录，先创建 initial（空内容）
            self.record_initial(session_id, file_path, "")
            next_version = "v1"
        elif row["version"] == "initial":
            next_version = "v1"
        else:
            try:
                num = int(row["version"][1:])
                next_version = f"v{num + 1}"
            except ValueError:
                next_version = "v1"

        # 检查版本是否已存在（防御性）
        existing = conn.execute(
            "SELECT id FROM file_versions WHERE session_id=? AND path=? AND version=?",
            (session_id, file_path, next_version),
        ).fetchone()
        if existing:
            # 版本已存在，递增直到找到可用版本号
            try:
                num = int(next_version[1:])
            except ValueError:
                num = 1
            while existing:
                num += 1
                next_version = f"v{num}"
                existing = conn.execute(
                    "SELECT id FROM file_versions WHERE session_id=? AND path=? AND version=?",
                    (session_id, file_path, next_version),
                ).fetchone()

        vid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO file_versions (id, session_id, path, content, version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vid, session_id, file_path, content, next_version, int(time.time())),
        )
        conn.commit()
        return vid

    # ── 读取 ──

    def get_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        """获取指定版本"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM file_versions WHERE id=?", (version_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_latest(self, session_id: str, file_path: str) -> Optional[Dict[str, Any]]:
        """获取文件在指定会话中的最新版本"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM file_versions WHERE session_id=? AND path=? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id, file_path),
        ).fetchone()
        return dict(row) if row else None

    def get_initial(self, session_id: str, file_path: str) -> Optional[Dict[str, Any]]:
        """获取文件的初始版本（修改前）"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM file_versions WHERE session_id=? AND path=? AND version='initial'",
            (session_id, file_path),
        ).fetchone()
        return dict(row) if row else None

    def list_versions(self, session_id: str, file_path: str) -> List[Dict[str, Any]]:
        """列出文件的所有版本"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM file_versions WHERE session_id=? AND path=? ORDER BY created_at ASC",
            (session_id, file_path),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_session_files(self, session_id: str) -> List[Dict[str, Any]]:
        """列出会话中所有被修改过的文件（每个文件只返回最新版本）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT f.* FROM file_versions f "
            "INNER JOIN ("
            "  SELECT session_id, path, MAX(created_at) as max_ts "
            "  FROM file_versions WHERE session_id=? "
            "  GROUP BY session_id, path"
            ") latest ON f.session_id=latest.session_id AND f.path=latest.path AND f.created_at=latest.max_ts "
            "ORDER BY f.path",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_versions(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话中所有版本记录"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM file_versions WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 回滚 ──

    def rollback_file(self, session_id: str, file_path: str) -> Optional[str]:
        """将文件回滚到 initial 版本（AI 修改前），返回 initial 内容"""
        initial = self.get_initial(session_id, file_path)
        if not initial:
            return None
        content = initial["content"]
        # 写回文件
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            Path(file_path).write_text(content, encoding="utf-8")
            return content
        except Exception:
            return None

    def rollback_session(self, session_id: str) -> Dict[str, str]:
        """回滚会话中所有文件到初始状态，返回 {path: status}"""
        files = self.list_session_files(session_id)
        results = {}
        for f in files:
            path = f["path"]
            initial = self.get_initial(session_id, path)
            if not initial:
                results[path] = "no_initial_version"
                continue
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(initial["content"], encoding="utf-8")
                results[path] = "restored"
            except Exception as e:
                results[path] = f"error: {e}"
        return results

    # ── 清理 ──

    def delete_session_history(self, session_id: str) -> int:
        """删除会话的所有文件历史，返回删除条数"""
        conn = self._get_conn()
        cur = conn.execute(
            "DELETE FROM file_versions WHERE session_id=?", (session_id,)
        )
        conn.commit()
        return cur.rowcount

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# 全局单例
def get_file_history() -> FileHistory:
    return FileHistory.get_instance()
