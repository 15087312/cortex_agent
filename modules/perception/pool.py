"""
PerceptionPool — 统一感知事件池

替代 PerceptionIntegrator._attention_items 的分散存储。
内置去重（MD5 hash）、TTL 过期、按类型分组输出 ContextFragment。
"""
import hashlib
import time
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from modules.thinking.context.pool import ContextFragment


class PerceptionPool:
    """统一感知事件池

    add(event_type, source, description, payload) → 去重+入库
    snapshot() → ContextFragment（最近 5 条语义事件）
    """

    def __init__(self, max_items: int = 20, ttl_seconds: float = 30.0):
        self._items: list = []
        self._max_items = max_items
        self._ttl = ttl_seconds
        self._hashes: set = set()

    def add(self, event_type: str, source: str, description: str, payload: dict = None) -> None:
        if not description:
            return
        # 去重
        h = hashlib.md5(description.encode()).hexdigest()[:16]
        if h in self._hashes:
            return
        self._hashes.add(h)
        if len(self._hashes) > 200:
            self._hashes.clear()

        self._items.append({
            "event_type": event_type,
            "source": source,
            "description": description[:300],
            "payload": payload or {},
            "timestamp": time.time(),
        })
        if len(self._items) > self._max_items:
            self._items = self._items[-self._max_items:]

        # TTL 物理清除
        cutoff = time.time() - self._ttl
        self._items = [i for i in self._items if i["timestamp"] >= cutoff]

    def snapshot(self, max_items: int = 5) -> "ContextFragment":  # noqa: F821 - 字符串注解，函数内 import
        """取最近 N 条语义事件，输出 ContextFragment"""
        from modules.thinking.context.pool import ContextFragment

        now = time.time()
        cutoff = now - self._ttl
        recent = [i for i in self._items[-max_items:] if i["timestamp"] >= cutoff]

        if not recent:
            return ContextFragment(
                source="perception",
                content="当前无感知数据（系统运行正常，但最近无屏幕/文件/语音变化）",
                target_roles=("orchestrator",),
                section_title="环境感知",
                priority=5,
            )

        sections: Dict[str, List[str]] = {"windows": [], "text": [], "files": [], "changes": [], "speech": []}
        for item in recent:
            et = item["event_type"]
            desc = item["description"]
            if "window" in et:
                sections["windows"].append(desc)
            elif "ocr" in et or "screen.ui" in et:
                sections["text"].append(desc)
            elif "file" in et:
                sections["files"].append(desc)
            elif "screen.diff" in et:
                payload = item.get("payload") or {}
                intensity = payload.get("intensity", 0)
                if intensity >= 0.3:
                    sections["changes"].append(desc)
            elif "speech" in et:
                sections["speech"].append(desc)

        parts = []
        if sections["windows"]:
            parts.append("【窗口状态】\n" + "\n".join(sections["windows"]))
        if sections["text"]:
            parts.append("【屏幕文本】\n" + "\n".join(sections["text"]))
        if sections["files"]:
            parts.append("【文件变化】\n" + "\n".join(sections["files"]))
        if sections["changes"]:
            parts.append("【屏幕变化】\n" + "\n".join(sections["changes"]))
        if sections["speech"]:
            parts.append("【语音指令】\n" + "\n".join(sections["speech"]))

        if not parts:
            return ContextFragment(
                source="perception",
                content="",
                target_roles=("large",),
                section_title="环境感知",
                priority=5,
            )

        return ContextFragment(
            source="perception",
            content="\n\n".join(parts),
            target_roles=("orchestrator",),
            section_title="环境感知",
            priority=5,
            ttl_turns=1,
        )

    def clear(self) -> None:
        self._items.clear()
        self._hashes.clear()
