"""强度分配器 — 根据来源类型、类别和载荷计算差异强度

强度范围: 0.0 ~ 100.0
计算方式: base + category_modifier + payload_modifier
"""
from typing import List

from modules.perception.difference.models import Difference

# 不同来源的基础强度
SOURCE_BASE = {
    "time": 30.0,          # 时间流逝/空闲
    "internal": 20.0,      # 内部状态
    "behavioral": 40.0,    # 用户行为
    "expectation": 35.0,   # 期望违反
    "perception": 25.0,    # 外部感知
    "user_input": 50.0,    # 用户直接输入
}

# 各类别的强度修正值
CATEGORY_MODIFIERS = {
    "idle_critical": +20.0,   # 长时间空闲（>30min）
    "idle_alert": +20.0,      # 空闲提醒
    "idle_warning": +5.0,     # 空闲警告
    "unfinished_tasks": +10.0,
    "failed_tasks": +15.0,
    "event_backlog": +10.0,
    "event_rate_spike": +15.0,
    "event_rate_drop": +10.0,
    "file_deleted": +10.0,
    "file_created": +5.0,
    "file_modified": +0.0,
    "file_moved": +0.0,
    "dialog_new_message": +5.0,
    "dialog_edited": +0.0,
    "screen_changed": +0.0,
}


class IntensityAssigner:
    """强度分配器

    综合 source_type、category 和 payload 中的额外信息，
    为每个差异计算 0-100 的强度值。
    """

    def assign(self, diff: Difference) -> float:
        """为单个差异分配强度值

        计算: base + category_modifier + payload_modifier
        结果裁剪到 [0, 100]。
        """
        base = SOURCE_BASE.get(diff.source_type, 25.0)  # 未知来源默认 25
        category_mod = CATEGORY_MODIFIERS.get(diff.category, 0.0)
        if category_mod == 0.0:
            # 精确匹配失败时尝试前缀匹配（如 "idle_" 匹配 "idle_warning"）
            for prefix, mod in CATEGORY_MODIFIERS.items():
                if diff.category.startswith(prefix):
                    category_mod = mod
                    break
        payload_mod = self._payload_modifier(diff)
        intensity = base + category_mod + payload_mod
        return max(0.0, min(100.0, round(intensity, 1)))

    def _payload_modifier(self, diff: Difference) -> float:
        """根据 payload 内容调整强度

        逻辑:
        - idle_minutes > 60: +15, > 30: +10
        - unfinished_count / failed_count: 每个 +3，上限 20
        - ratio > 3: 每超 1 单位 +8，上限 25
        - event_count > 5000: 每 200 个 +1，上限 20
        """
        mod = 0.0
        payload = diff.payload
        if "idle_minutes" in payload:
            minutes = payload["idle_minutes"]
            if minutes > 60:
                mod += 15.0
            elif minutes > 30:
                mod += 10.0
        for key in ("unfinished_count", "failed_count"):
            if key in payload:
                count = payload[key]
                if isinstance(count, (int, float)):
                    mod += min(count * 3, 20.0)
        if "ratio" in payload:
            ratio = payload["ratio"]
            if isinstance(ratio, (int, float)):
                mod += min((ratio - 3.0) * 8, 25.0) if ratio > 3.0 else 0.0
        if "event_count" in payload:
            count = payload["event_count"]
            if isinstance(count, (int, float)):
                mod += min((count - 5000) / 200, 20.0) if count > 5000 else 0.0
        return mod

    def assign_batch(self, differences: List[Difference]) -> List[Difference]:
        """批量分配强度并按强度降序排列"""
        for diff in differences:
            diff.intensity = self.assign(diff)
        differences.sort(key=lambda d: d.intensity, reverse=True)
        return differences
