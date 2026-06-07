"""
强度赋值器 — 为差异计算 0-100 的强度值

复用 WeightCalculator.calculate() 的因子映射 + 钳位模式：
  intensity = base(source_type) + category_modifier + payload_modifier → clamp [0, 100]
"""
from typing import List, Optional

from modules.difference_detector.models import Difference


# 各源类型的基础强度 (基准值)
SOURCE_BASE = {
    "time": 30.0,
    "internal": 20.0,
    "behavioral": 40.0,
    "expectation": 35.0,
    "perception": 25.0,
    "user_input": 50.0,
}

# 类别修饰符 — 在基础值上叠加
CATEGORY_MODIFIERS = {
    "idle_critical": +20.0,
    "idle_alert": +20.0,
    "idle_warning": +5.0,
    "unfinished_tasks": +10.0,
    "failed_tasks": +15.0,
    "event_backlog": +10.0,
    "event_rate_spike": +15.0,
    "event_rate_drop": +10.0,
    # 感知系统推送的类别
    "file_deleted": +10.0,
    "file_created": +5.0,
    "file_modified": +0.0,
    "file_moved": +0.0,
    "dialog_new_message": +5.0,
    "dialog_edited": +0.0,
    "screen_changed": +0.0,
}


class IntensityAssigner:
    """强度赋值器 — 因子映射 + 钳位 [0, 100]"""

    def assign(self, diff: Difference) -> float:
        """为单个差异计算强度"""
        base = SOURCE_BASE.get(diff.source_type, 25.0)

        # 类别修饰符（精确匹配 + 前缀匹配）
        category_mod = CATEGORY_MODIFIERS.get(diff.category, 0.0)
        if category_mod == 0.0:
            for prefix, mod in CATEGORY_MODIFIERS.items():
                if diff.category.startswith(prefix):
                    category_mod = mod
                    break

        # 载荷修饰符 — 从 payload 中提取额外信息
        payload_mod = self._payload_modifier(diff)

        intensity = base + category_mod + payload_mod
        return max(0.0, min(100.0, round(intensity, 1)))

    def _payload_modifier(self, diff: Difference) -> float:
        """从 payload 提取额外修饰值"""
        mod = 0.0
        payload = diff.payload

        # 空闲时长修饰
        if "idle_minutes" in payload:
            minutes = payload["idle_minutes"]
            if minutes > 60:
                mod += 15.0
            elif minutes > 30:
                mod += 10.0

        # 任务数量修饰
        for key in ("unfinished_count", "failed_count"):
            if key in payload:
                count = payload[key]
                if isinstance(count, (int, float)):
                    mod += min(count * 3, 20.0)

        # 事件速率比值修饰
        if "ratio" in payload:
            ratio = payload["ratio"]
            if isinstance(ratio, (int, float)):
                mod += min((ratio - 3.0) * 8, 25.0) if ratio > 3.0 else 0.0

        # 事件积压修饰
        if "event_count" in payload:
            count = payload["event_count"]
            if isinstance(count, (int, float)):
                mod += min((count - 5000) / 200, 20.0) if count > 5000 else 0.0

        return mod

    def assign_batch(self, differences: List[Difference]) -> List[Difference]:
        """批量赋值 — 对列表中的每个差异计算 intensity 并更新"""
        for diff in differences:
            diff.intensity = self.assign(diff)
        # 按强度降序排列
        differences.sort(key=lambda d: d.intensity, reverse=True)
        return differences
