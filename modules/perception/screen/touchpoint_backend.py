"""
TouchpointBackend — 通过 macOS 无障碍 API 检测 UI 元素

适用于原生应用（PyCharm、Safari、Finder 等）。
"""
import time

from modules.perception.screen.context import ScreenContext, UIElement
from utils.logger import setup_logger

logger = setup_logger("touchpoint_backend")


class TouchpointBackend:
    """macOS 无障碍 API 后端"""

    def __init__(self):
        self._available = False
        try:
            import touchpoint  # noqa: F401
            self._available = True
        except ImportError:
            logger.debug("touchpoint 未安装")

    def is_available(self) -> bool:
        return self._available

    def detect(self, app: str = "", depth: int = 3, named_only: bool = True) -> ScreenContext:
        """检测当前窗口的 UI 元素"""
        import touchpoint as tp

        t0 = time.time()
        result = ScreenContext(backend="touchpoint", depth=depth)

        # 角色映射

        # 获取目标窗口
        wins = tp.windows()
        if not wins:
            return result

        target_win = None
        if app:
            for w in wins:
                if app.lower() in w.app.lower():
                    target_win = w
                    break
        else:
            active = [w for w in wins if w.is_active]
            if active:
                target_win = active[0]

        if not target_win:
            return result

        result.app_name = target_win.app
        result.window_title = target_win.title or ""

        # 查询元素
        kwargs = {"window_id": target_win.id, "named_only": named_only}
        if depth > 0:
            kwargs["max_depth"] = depth

        elements = tp.elements(**kwargs)

        # 转换为统一格式
        for e in elements:
            x, y = e.position
            w, h = e.size
            ui_elem = UIElement(
                element_id=f"{e.role.name}_{x}_{y}",
                type=e.role.name.lower(),
                label=str(e.name or "")[:100],
                bbox=[x, y, x + w, y + h],
                center_x=x + w // 2,
                center_y=y + h // 2,
                actions=e.actions if hasattr(e, "actions") else [],
            )
            result.elements.append(ui_elem)

        result.element_count = len(result.elements)
        result.elapsed_ms = (time.time() - t0) * 1000

        # 角色统计
        from collections import Counter
        role_counts = Counter(e.type for e in result.elements)
        result.role_summary = dict(role_counts.most_common(10))

        logger.debug(f"touchpoint: {result.app_name} - {result.element_count} 元素, {result.elapsed_ms:.0f}ms")
        return result
