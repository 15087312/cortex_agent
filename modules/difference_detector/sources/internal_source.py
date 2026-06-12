"""
内部状态差异源

检测内容：
- 全局上下文池中未完成任务
- 全局上下文池中失败/错误任务
- 系统异常状态
"""
import time
from typing import List, Optional

from modules.difference_detector.sources.base import DifferenceSource
from modules.difference_detector.models import Difference
from utils.logger import setup_logger

logger = setup_logger("internal_state_source")

INTERNAL_TTL = 30 * 60  # 30 分钟


class InternalStateDifferenceSource(DifferenceSource):
    """内部状态差异源"""

    def __init__(self, gcm_pool=None):
        super().__init__()
        self._gcm_pool = gcm_pool

    @property
    def source_type(self) -> str:
        return "internal"

    def _get_gcm_pool(self):
        if self._gcm_pool is not None:
            return self._gcm_pool
        from modules.thinking.context import gcm_pool
        return gcm_pool

    def detect(self) -> List[Difference]:
        differences = []
        gcm = self._get_gcm_pool()
        now = time.time()

        try:
            state = gcm.get_state()
            active_tasks = getattr(state, 'active_tasks', [])
            completed_tasks = getattr(state, 'completed_tasks', [])

            # 未完成任务检测
            unfinished = [t for t in active_tasks if not t.get("completed_at")]
            if unfinished:
                oldest = min(
                    (t.get("_added_at", now) for t in unfinished),
                    default=now,
                )
                age = now - oldest
                base_intensity = 20.0
                if age > 3600:      # >1 小时
                    base_intensity = 40.0
                elif age > 600:     # >10 分钟
                    base_intensity = 30.0

                differences.append(Difference(
                    source_type="internal",
                    category="unfinished_tasks",
                    intensity=base_intensity,
                    ttl=INTERNAL_TTL,
                    payload={
                        "unfinished_count": len(unfinished),
                        "oldest_age_seconds": round(age, 1),
                        "task_ids": [t.get("id", "?") for t in unfinished[:5]],
                    },
                ))

            # 失败任务检测
            failed = [
                t for t in completed_tasks
                if t.get("result") and isinstance(t["result"], dict) and t["result"].get("error")
            ]
            if failed:
                differences.append(Difference(
                    source_type="internal",
                    category="failed_tasks",
                    intensity=min(30.0 + len(failed) * 5, 80.0),
                    ttl=INTERNAL_TTL,
                    payload={
                        "failed_count": len(failed),
                        "task_ids": [t.get("id", "?") for t in failed[:5]],
                    },
                ))

            # 事件堆积检测
            event_count = gcm.event_count() if hasattr(gcm, 'event_count') else 0
            if event_count > 5000:
                differences.append(Difference(
                    source_type="internal",
                    category="event_backlog",
                    intensity=min(25.0 + (event_count - 5000) / 200, 70.0),
                    ttl=INTERNAL_TTL,
                    payload={"event_count": event_count},
                ))

        except Exception as e:
            logger.debug("内部状态差异检测失败 (非致命): %s", e)

        return differences
