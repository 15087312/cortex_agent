"""
安全审计日志
"""
from typing import Dict, Any, Optional
from datetime import datetime
import json
import os
import threading
from utils.logger import setup_logger

logger = setup_logger("audit_logger")


class SecurityAuditLogger:
    def __init__(self, archive_path: str = "data/security_audit.jsonl"):
        self.archive_path = archive_path
        self._write_lock = threading.Lock()
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        dir_path = os.path.dirname(self.archive_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

    def log(
        self,
        event_type: str,
        level: str,
        content: str,
        result: bool,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        try:
            content_preview = content[:100] if len(content) > 100 else content
            content_preview = content_preview.encode('utf-8', errors='replace').decode('utf-8')
        except Exception:
            content_preview = "[内容包含无法编码的字符]"
        
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "security_level": level,
            "content_preview": content_preview,
            "result": "通过" if result else "拦截",
            "metadata": metadata or {}
        }

        self._save_local(log_entry)
        try:
            logger.info(f"[审计] {level} {event_type}: {log_entry['result']}")
        except Exception as e:
            logger.debug(f"审计日志输出失败: {e}")

    def _save_local(self, entry: Dict) -> None:
        try:
            with self._write_lock:
                with open(self.archive_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"审计日志写入失败: {e}")

    def get_recent_logs(self, limit: int = 50) -> list:
        logs = []
        try:
            with open(self.archive_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-limit:]:
                    try:
                        logs.append(json.loads(line.strip()))
                    except Exception:
                        continue
        except FileNotFoundError:
            pass
        return logs
