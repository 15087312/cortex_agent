"""桌宠状态模型 — 心情 / 饱食 / 精力 / 清洁（完整版养成）

- 4 维状态 0-100，持久化到 ~/.cortex/pet_state.json
- 时间衰减：读取时按 elapsed 计算（无需后台定时器，崩溃不丢状态）
- 互动效果：apply(action) 增减后保存
- describe()：生成状态描述，注入模型提示词
"""
import json
import os
import threading
import time
from typing import Dict

from utils.logger import setup_logger

logger = setup_logger("pet_state")

DEFAULTS: Dict[str, float] = {
    "mood": 60,          # 心情
    "satiety": 70,       # 饱食
    "energy": 80,        # 精力
    "cleanliness": 75,   # 清洁
}
MAX_VAL = 100.0
MIN_VAL = 0.0
# 每小时衰减速率
DECAY_PER_HOUR: Dict[str, float] = {
    "mood": 0.5,
    "satiety": 3.0,
    "energy": 2.0,
    "cleanliness": 3.0,
}
# 其他维度过低对心情的惩罚（低于阈值时心情额外下降/小时）
_MOOD_PENALTY = {"satiety": 2.0, "energy": 1.5, "cleanliness": 1.5}
_MOOD_PENALTY_BELOW = 30.0


def _state_file() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".cortex", "pet_state.json")


class PetState:
    _instance: "PetState" = None
    _lock = threading.Lock()

    def __init__(self, path: str = ""):
        self._path = path or _state_file()
        self._values: Dict[str, float] = dict(DEFAULTS)
        self._updated_at: float = time.time()
        self._load()

    @classmethod
    def get_instance(cls) -> "PetState":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 持久化 ──

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k in DEFAULTS:
                    v = data.get("values", {}).get(k)
                    if isinstance(v, (int, float)):
                        self._values[k] = float(v)
                ts = data.get("updated_at")
                if isinstance(ts, (int, float)):
                    self._updated_at = float(ts)
        except Exception as e:
            logger.debug(f"[PetState] 加载失败 (使用默认): {e}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"values": self._values, "updated_at": self._updated_at}, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            logger.debug(f"[PetState] 保存失败: {e}")

    # ── 读取 + 衰减 ──

    def read(self, now: float = 0) -> Dict[str, int]:
        """读取当前状态：按 elapsed 衰减 + 心情惩罚，clamp 到 0-100（不落盘）"""
        now = now or time.time()
        elapsed = max(0.0, (now - self._updated_at) / 3600.0)
        v = {k: self._values[k] - DECAY_PER_HOUR[k] * elapsed for k in DEFAULTS}
        for key, rate in _MOOD_PENALTY.items():
            if v.get(key, 100) < _MOOD_PENALTY_BELOW:
                v["mood"] -= rate * elapsed
        return {k: int(max(MIN_VAL, min(MAX_VAL, round(val)))) for k, val in v.items()}

    # ── 互动效果 ──

    def apply(self, action_id: str, now: float = 0) -> Dict[str, int]:
        """应用互动效果（先按时间衰减再增减），保存并返回最新状态"""
        from modules.desktop_pet.actions import get_action
        action = get_action(action_id)
        now = now or time.time()
        elapsed = max(0.0, (now - self._updated_at) / 3600.0)
        base = {k: self._values[k] - DECAY_PER_HOUR[k] * elapsed for k in DEFAULTS}
        for key, rate in _MOOD_PENALTY.items():
            if base.get(key, 100) < _MOOD_PENALTY_BELOW:
                base["mood"] -= rate * elapsed
        for key, delta in (action or {}).get("effects", {}).items():
            if key in DEFAULTS:
                base[key] += delta
        self._values = {k: max(MIN_VAL, min(MAX_VAL, round(v))) for k, v in base.items()}
        self._updated_at = now
        self._save()
        return {k: int(v) for k, v in self._values.items()}

    # ── 状态描述（注入提示词）──

    def describe(self, values: Dict[str, int] = None) -> str:
        values = values or self.read()
        desc = []
        if values["mood"] >= 80:
            desc.append("心情很好")
        elif values["mood"] >= 50:
            desc.append("心情不错")
        elif values["mood"] >= 30:
            desc.append("心情有点低落")
        else:
            desc.append("心情很差")
        if values["satiety"] >= 80:
            desc.append("吃得很饱")
        elif values["satiety"] >= 50:
            desc.append("肚子半饱")
        elif values["satiety"] >= 30:
            desc.append("有点饿")
        else:
            desc.append("饿坏了")
        if values["energy"] >= 70:
            desc.append("精力充沛")
        elif values["energy"] >= 40:
            desc.append("精力一般")
        else:
            desc.append("很疲惫")
        if values["cleanliness"] >= 70:
            desc.append("身上干干净净")
        elif values["cleanliness"] >= 40:
            desc.append("身上有点脏")
        else:
            desc.append("身上脏兮兮")
        return f"你现在{desc[0]}、{desc[1]}、{desc[2]}、{desc[3]}。"
