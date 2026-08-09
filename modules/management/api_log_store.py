"""API 请求日志持久化存储（SQLite + 内存积累定时批量写）

- 持续记录、持久化到 data/api_log.db，可追溯历史
- 内存积累 + 每 1s 定时批量 flush（不阻塞事件循环，性能开销极小）
- 支持按 method/path/status/时间筛选、分页、统计
- 记录请求/响应体（截断存储），供排查详细参数与返回值
"""
import os
import sqlite3
import threading
import time

from utils.logger import setup_logger

logger = setup_logger("api_log_store")

_FLUSH_INTERVAL = 1.0
_BATCH_SIZE = 200

# 存储上限：请求体 / 响应体截断长度（避免撑爆数据库）
_MAX_REQ_BODY = 4000
_MAX_RESP_BODY = 8000


def _db_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "api_log.db")


class ApiLogStore:
    _instance: "ApiLogStore" = None
    _lock = threading.Lock()

    def __init__(self, path: str = ""):
        self._path = path or _db_path()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._queue: list = []
        self._qlock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False, timeout=3)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS api_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                time TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER NOT NULL,
                ms REAL NOT NULL DEFAULT 0,
                request_body TEXT NOT NULL DEFAULT '',
                response_body TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_api_requests_ts ON api_requests(ts)")
        # 兼容旧库：缺列则 ALTER 补齐
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(api_requests)")}
        if "request_body" not in cols:
            self._conn.execute("ALTER TABLE api_requests ADD COLUMN request_body TEXT NOT NULL DEFAULT ''")
        if "response_body" not in cols:
            self._conn.execute("ALTER TABLE api_requests ADD COLUMN response_body TEXT NOT NULL DEFAULT ''")
        self._conn.commit()
        self._stop = threading.Event()
        t = threading.Thread(target=self._flush_loop, daemon=True, name="api-log-flush")
        t.start()

    @classmethod
    def get_instance(cls) -> "ApiLogStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 写入（内存积累）──

    def add(self, method: str, path: str, status: int, duration_ms: float = 0.0,
            request_body: str = "", response_body: str = "") -> None:
        with self._qlock:
            self._queue.append((
                time.time(), time.strftime("%H:%M:%S"), method, path, status, duration_ms,
                (request_body or "")[:_MAX_REQ_BODY],
                (response_body or "")[:_MAX_RESP_BODY],
            ))
            if len(self._queue) >= _BATCH_SIZE:
                self._flush_locked()

    def _flush_loop(self):
        while not self._stop.is_set():
            time.sleep(_FLUSH_INTERVAL)
            try:
                self.flush()
            except Exception:
                pass

    def flush(self) -> None:
        with self._qlock:
            if not self._queue:
                return
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._queue:
            return
        batch = self._queue
        self._queue = []
        try:
            self._conn.executemany(
                "INSERT INTO api_requests(ts, time, method, path, status, ms, request_body, response_body) VALUES (?,?,?,?,?,?,?,?)",
                batch,
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"[ApiLog] 写入失败: {e}")
            # 失败回填，避免丢日志
            self._queue = batch + self._queue

    # ── 查询 ──

    def query(self, method: str = "", path: str = "", status: int = 0,
              limit: int = 50, offset: int = 0, since_hours: float = 0.0,
              include_body: bool = True) -> list:
        where, params = [], []
        if method:
            where.append("method=?"); params.append(method)
        if path:
            where.append("path LIKE ?"); params.append(f"%{path}%")
        if status:
            where.append("CAST(status AS TEXT) LIKE ?"); params.append(f"{int(status)}%")
        if since_hours > 0:
            where.append("ts>=?"); params.append(time.time() - since_hours * 3600)
        cols = "ts, time, method, path, status, ms" + (", request_body, response_body" if include_body else "")
        sql = f"SELECT {cols} FROM api_requests"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        conn = sqlite3.connect(self._path, timeout=3)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            item = {"time": r[1], "method": r[2], "path": r[3], "status": r[4], "ms": r[5]}
            if include_body:
                item["request_body"] = r[6]
                item["response_body"] = r[7]
            out.append(item)
        return out

    def count(self, method: str = "", path: str = "", status: int = 0, since_hours: float = 0.0) -> int:
        where, params = [], []
        if method:
            where.append("method=?"); params.append(method)
        if path:
            where.append("path LIKE ?"); params.append(f"%{path}%")
        if status:
            where.append("CAST(status AS TEXT) LIKE ?"); params.append(f"{int(status)}%")
        if since_hours > 0:
            where.append("ts>=?"); params.append(time.time() - since_hours * 3600)
        sql = "SELECT COUNT(*) FROM api_requests"
        if where:
            sql += " WHERE " + " AND ".join(where)
        conn = sqlite3.connect(self._path, timeout=3)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def stats(self, since_hours: float = 0.0) -> dict:
        conn = sqlite3.connect(self._path, timeout=3)
        try:
            since = time.time() - since_hours * 3600 if since_hours > 0 else 0
            base = "FROM api_requests"
            params: list = []
            if since > 0:
                base = "FROM api_requests WHERE ts>=?"
                params.append(since)
            by_method = dict(conn.execute(
                f"SELECT method, COUNT(*) {base} GROUP BY method", params
            ).fetchall())
            by_status = dict(conn.execute(
                f"SELECT status, COUNT(*) {base} GROUP BY status", params
            ).fetchall())
            total, avg_ms = conn.execute(
                f"SELECT COUNT(*), COALESCE(AVG(ms),0) {base}", params
            ).fetchone()
            return {
                "total": total,
                "avg_ms": round(avg_ms, 1),
                "by_method": {k: v for k, v in sorted(by_method.items(), key=lambda x: -x[1])},
                "by_status": {str(k): v for k, v in sorted(by_status.items(), key=lambda x: -x[1])},
            }
        finally:
            conn.close()
