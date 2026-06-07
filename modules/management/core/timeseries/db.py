"""
时序数据库 - 基于 SQLite 的指标历史存储
"""
import sqlite3
import json
import time
import threading
from typing import Dict, Any, List, Optional
from pathlib import Path
from utils.logger import setup_logger

logger = setup_logger("timeseries_db")


class TimeSeriesDB:
    """时序数据库"""

    def __init__(self, db_path: str = "data/metrics/timeseries.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_name TEXT NOT NULL,
                value REAL NOT NULL,
                timestamp REAL NOT NULL,
                tags TEXT,
                module TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_name_time
            ON metrics(metric_name, timestamp)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                message TEXT,
                details TEXT,
                timestamp REAL NOT NULL,
                severity TEXT DEFAULT 'info'
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_time
            ON events(timestamp)
        """)

        conn.commit()
        conn.close()
        logger.info(f"时序数据库初始化完成: {self.db_path}")

    def write(
        self,
        metric_name: str,
        value: float,
        tags: Dict[str, str] = None,
        module: str = ""
    ):
        """写入指标"""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                "INSERT INTO metrics (metric_name, value, timestamp, tags, module) VALUES (?, ?, ?, ?, ?)",
                (
                    metric_name,
                    value,
                    time.time(),
                    json.dumps(tags) if tags else None,
                    module
                )
            )

            conn.commit()
            conn.close()

    def query(
        self,
        metric_name: str,
        start_time: float = None,
        end_time: float = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """查询指标"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        query = "SELECT metric_name, value, timestamp, tags, module FROM metrics WHERE metric_name = ?"
        params = [metric_name]

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "metric_name": row[0],
                "value": row[1],
                "timestamp": row[2],
                "tags": json.loads(row[3]) if row[3] else {},
                "module": row[4]
            })

        return results

    def aggregate(
        self,
        metric_name: str,
        start_time: float,
        end_time: float,
        interval: str = "1m"
    ) -> List[Dict[str, Any]]:
        """聚合查询"""
        interval_seconds = self._parse_interval(interval)

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                (CAST(timestamp / ? AS INTEGER) * ?) as bucket,
                AVG(value) as avg_value,
                MIN(value) as min_value,
                MAX(value) as max_value,
                COUNT(*) as count
            FROM metrics
            WHERE metric_name = ? AND timestamp >= ? AND timestamp <= ?
            GROUP BY bucket
            ORDER BY bucket
        """, (interval_seconds, interval_seconds, metric_name, start_time, end_time))

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "timestamp": row[0],
                "avg": row[1],
                "min": row[2],
                "max": row[3],
                "count": row[4]
            })

        return results

    def _parse_interval(self, interval: str) -> int:
        """解析时间间隔"""
        try:
            if not interval or len(interval) < 2:
                return 60
            unit = interval[-1]
            value = int(interval[:-1])

            if unit == "s":
                return value
            elif unit == "m":
                return value * 60
            elif unit == "h":
                return value * 3600
            elif unit == "d":
                return value * 86400
            return 60
        except (ValueError, IndexError):
            return 60

    def write_event(
        self,
        event_type: str,
        message: str,
        details: Dict[str, Any] = None,
        severity: str = "info"
    ):
        """写入事件"""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute(
                "INSERT INTO events (event_type, message, details, timestamp, severity) VALUES (?, ?, ?, ?, ?)",
                (
                    event_type,
                    message,
                    json.dumps(details) if details else None,
                    time.time(),
                    severity
                )
            )

            conn.commit()
            conn.close()

    def query_events(
        self,
        event_type: str = None,
        start_time: float = None,
        end_time: float = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """查询事件"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        query = "SELECT event_type, message, details, timestamp, severity FROM events WHERE 1=1"
        params = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "event_type": row[0],
                "message": row[1],
                "details": json.loads(row[2]) if row[2] else {},
                "timestamp": row[3],
                "severity": row[4]
            })

        return results

    def get_stats(self) -> Dict[str, Any]:
        """获取数据库统计"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM metrics")
        metrics_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM events")
        events_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT metric_name) FROM metrics")
        unique_metrics = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(value) FROM metrics WHERE metric_name LIKE '%latency%'")
        total_latency = cursor.fetchone()[0] or 0

        conn.close()

        return {
            "metrics_count": metrics_count,
            "events_count": events_count,
            "unique_metrics": unique_metrics,
            "total_latency_ms": total_latency,
            "db_size_mb": round(self.db_path.stat().st_size / 1024 / 1024, 2)
        }

    def cleanup(self, days: int = 7):
        """清理过期数据"""
        cutoff_time = time.time() - (days * 86400)

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            cursor.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff_time,))
            deleted_metrics = cursor.rowcount

            cursor.execute("DELETE FROM events WHERE timestamp < ?", (cutoff_time,))
            deleted_events = cursor.rowcount

            conn.commit()
            conn.close()

        logger.info(f"清理完成: 删除 {deleted_metrics} 条指标, {deleted_events} 条事件")
        return {"deleted_metrics": deleted_metrics, "deleted_events": deleted_events}


timeseries_db = TimeSeriesDB()