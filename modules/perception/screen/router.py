"""
DetectorRouter — 自动检测并选择最佳 UI 检测后端

根据应用类型自动选择：
- 原生应用 → touchpoint (无障碍 API)
- Chromium 应用 → CDP 或 vision
- 信息不足时 → vision 补充
"""
import time
from typing import Optional

from modules.perception.screen.context import ScreenContext
from utils.logger import setup_logger

logger = setup_logger("detector_router")

# Chromium 应用关键词
CHROMIUM_KEYWORDS = {"edge", "chrome", "brave", "chromium", "opera", "vivaldi", "electron"}

# 信息不足的阈值
MIN_ELEMENTS_THRESHOLD = 5


class DetectorRouter:
    """UI 检测路由器"""

    def __init__(self):
        self._touchpoint = None
        self._cdp = None
        self._vision = None
        self._init_backends()

    def _init_backends(self):
        """初始化可用后端"""
        from modules.perception.screen.touchpoint_backend import TouchpointBackend
        self._touchpoint = TouchpointBackend()

        from modules.perception.screen.vision_backend import VisionBackend
        self._vision = VisionBackend()

        # CDP 后端（可选）
        try:
            from infra.data_process.core.cdp_scanner import get_cdp_scanner
            self._cdp = get_cdp_scanner()
        except Exception:
            pass

    def detect(self, app: str = "", depth: int = 3) -> ScreenContext:
        """
        自动检测 UI 元素

        Args:
            app: 指定应用名（空=当前活跃窗口）
            depth: 检测深度

        Returns:
            ScreenContext 统一结果
        """
        t0 = time.time()

        # 确定目标应用
        target_app = app
        if not target_app:
            target_app = self._get_active_app()

        # 判断应用类型
        is_chromium = self._is_chromium_app(target_app)

        logger.debug(f"检测目标: {target_app} (chromium={is_chromium})")

        # 根据类型选择后端
        if is_chromium:
            result = self._detect_chromium(target_app, depth)
        else:
            result = self._detect_native(target_app, depth)

        # 检查信息是否充足，不足则用视觉补充
        if result.element_count < MIN_ELEMENTS_THRESHOLD and self._vision.is_available():
            logger.info(f"元素数 {result.element_count} < {MIN_ELEMENTS_THRESHOLD}，用视觉模型补充")
            result = self._merge_with_vision(result)

        result.elapsed_ms = (time.time() - t0) * 1000
        return result

    def _get_active_app(self) -> str:
        """获取当前活跃应用名"""
        try:
            import touchpoint as tp
            wins = tp.windows()
            active = [w for w in wins if w.is_active]
            if active:
                return active[0].app
        except Exception:
            pass
        return ""

    def _is_chromium_app(self, app_name: str) -> bool:
        """判断是否是 Chromium 应用"""
        if not app_name:
            return False
        app_lower = app_name.lower()
        return any(kw in app_lower for kw in CHROMIUM_KEYWORDS)

    def _detect_native(self, app: str, depth: int) -> ScreenContext:
        """检测原生应用"""
        if self._touchpoint and self._touchpoint.is_available():
            return self._touchpoint.detect(app, depth=depth)
        return ScreenContext(app_name=app, backend="none")

    def _detect_chromium(self, app: str, depth: int) -> ScreenContext:
        """检测 Chromium 应用"""
        # 尝试 CDP
        if self._cdp:
            try:
                ports = self._cdp.find_chromium_ports()
                if ports:
                    elements = self._cdp.scan(ports[0]["port"], max_depth=depth)
                    if elements:
                        result = ScreenContext(
                            app_name=app,
                            backend="cdp",
                            depth=depth,
                        )
                        result.elements = elements
                        result.element_count = len(elements)
                        return result
            except Exception as e:
                logger.debug(f"CDP 检测失败: {e}")

        # CDP 不可用，用 touchpoint 尝试
        if self._touchpoint and self._touchpoint.is_available():
            return self._touchpoint.detect(app, depth=depth)

        return ScreenContext(app_name=app, backend="none")

    def _merge_with_vision(self, base_result: ScreenContext) -> ScreenContext:
        """用视觉模型补充信息"""
        import asyncio

        try:
            vision_backend = self._vision
            if not vision_backend.is_available():
                return base_result

            # 异步调用视觉模型：get_running_loop 判断是否已在异步上下文，
            # 避免 get_event_loop 在无 loop 线程抛 RuntimeError（3.12+ 已移除自动创建）
            try:
                asyncio.get_running_loop()
                running = True
            except RuntimeError:
                running = False
            if running:
                # 已在异步上下文中，用线程池跑独立 loop（asyncio.run 不能在运行中 loop 内调用）
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    vision_result = pool.submit(
                        lambda: asyncio.run(vision_backend.detect(base_result.app_name))
                    ).result()
            else:
                vision_result = asyncio.run(vision_backend.detect(base_result.app_name))

            # 合并结果
            base_result.visual_description = vision_result.visual_description
            base_result.backend = f"{base_result.backend}+vision"

            # 如果视觉模型提取到了更多元素，补充进去
            if vision_result.element_count > base_result.element_count:
                existing_labels = {e.label for e in base_result.elements}
                for e in vision_result.elements:
                    if e.label not in existing_labels:
                        base_result.elements.append(e)
                base_result.element_count = len(base_result.elements)

            return base_result

        except Exception as e:
            logger.warning(f"视觉补充失败: {e}")
            return base_result


# 全局单例
_router: Optional[DetectorRouter] = None


def get_detector_router() -> DetectorRouter:
    global _router
    if _router is None:
        _router = DetectorRouter()
    return _router
