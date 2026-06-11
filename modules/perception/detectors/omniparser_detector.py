"""OmniParser UI 结构化检测器 — 三级降级

后端优先级：
1. OmniParser HTTP 服务 (localhost:8765)
2. OmniParser 本地模型 (import omniparser)
3. OCR 降级 (OCRDetector + WindowDetector 组合)

同时实现 PerceptionDetector.detect() 接口，发出 SCREEN_UI 事件。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("omniparser_detector")


@dataclass
class UIElement:
    """UI 元素描述"""
    element_id: str = ""       # "e001" 格式
    type: str = "unknown"      # button/input/text/icon/checkbox/link/unknown
    label: str = ""            # 文字内容
    bbox: List[int] = field(default_factory=list)  # [x1, y1, x2, y2] 绝对像素坐标
    center_x: int = 0
    center_y: int = 0
    confidence: float = 0.0
    source: str = ""           # omniparser_http/omniparser_local/ocr_fallback

    def to_dict(self) -> Dict[str, Any]:
        return {
            "element_id": self.element_id,
            "type": self.type,
            "label": self.label,
            "bbox": self.bbox,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "confidence": self.confidence,
            "source": self.source,
        }


class OmniParserDetector(PerceptionDetector):
    """OmniParser UI 结构化检测器

    三级降级：
    1. GET http://localhost:8000/probe/ → omniparser_http  (精度: high)
    2. import omniparser → omniparser_local                (精度: high)
    3. OCR fallback → ocr_fallback                         (精度: low — 只有文字，无坐标)
    """

    # 精度等级：high 可用于 learn_tool，low 只能用于被动感知
    PRECISION_HIGH = "high"
    PRECISION_LOW = "low"

    def __init__(self, api_url: str = "http://localhost:8000"):
        self._api_url = api_url.rstrip("/")
        self._backend: Optional[str] = None
        self._local_parser = None
        self._ocr_engine = None
        self._prev_elements: Dict[str, List[UIElement]] = {}
        self._detect_backend()

    def _detect_backend(self) -> None:
        """探测可用后端"""
        # 1. HTTP API（OmniParser server: GET /probe/）
        try:
            import urllib.request
            req = urllib.request.Request(f"{self._api_url}/probe/", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    self._backend = "omniparser_http"
                    logger.info("OmniParser 后端: HTTP API")
                    return
        except Exception as e:
            logger.debug(f"OmniParser HTTP 探测失败: {e}")

        # 2. 本地模型
        try:
            import importlib
            importlib.import_module("omniparser")
            self._backend = "omniparser_local"
            logger.info("OmniParser 后端: 本地模型")
            return
        except ImportError as e:
            logger.debug(f"OmniParser 本地模型不可用: {e}")

        # 3. OCR 降级
        self._backend = "ocr_fallback"
        logger.info("OmniParser 后端: OCR 降级")

    @property
    def detector_type(self) -> str:
        return "ui"

    def is_available(self) -> bool:
        return self._backend is not None

    @property
    def precision(self) -> str:
        """精度等级：high（可定位 UI 元素）/ low（只有文字行）"""
        if self._backend in ("omniparser_http", "omniparser_local"):
            return self.PRECISION_HIGH
        return self.PRECISION_LOW

    @property
    def backend(self) -> Optional[str]:
        return self._backend

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """实现 PerceptionDetector 接口 — 发出 SCREEN_UI 事件"""
        if roi_image is None or roi_image.size == 0:
            return []

        elements = self.detect_elements(roi_image)
        if not elements:
            return []

        # 与上一次对比
        prev = self._prev_elements.get(roi_name, [])
        self._prev_elements[roi_name] = elements

        if _elements_equal(prev, elements):
            return []

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_UI,
            source=f"omniparser_{self._backend}",
            importance=0.7,
            roi_name=roi_name,
            payload={
                "elements": [e.to_dict() for e in elements],
                "element_count": len(elements),
                "backend": self._backend,
                "changed": True,
            },
        )
        return [event]

    def detect_elements(self, screenshot: Any) -> List[UIElement]:
        """主接口：截图 → UI 元素列表

        Args:
            screenshot: bytes 或 numpy ndarray 格式的截图

        Returns:
            UIElement 列表
        """
        if not self._backend:
            return []

        # 统一转为 numpy array
        if isinstance(screenshot, bytes):
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(screenshot))
            img_array = np.array(img.convert("RGB"))
        elif isinstance(screenshot, np.ndarray):
            img_array = screenshot
        else:
            logger.warning(f"不支持的截图格式: {type(screenshot)}")
            return []

        if self._backend == "omniparser_http":
            return self._detect_http(img_array)
        elif self._backend == "omniparser_local":
            return self._detect_local(img_array)
        else:
            return self._detect_ocr_fallback(img_array)

    def _detect_http(self, image: np.ndarray) -> List[UIElement]:
        """通过 HTTP API 调用 OmniParser（POST /parse/）"""
        try:
            import io
            import json
            import base64
            import urllib.request
            import urllib.error
            from PIL import Image

            img = Image.fromarray(image)
            w, h = img.size
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            payload = json.dumps({"base64_image": img_b64}).encode()
            req = urllib.request.Request(
                f"{self._api_url}/parse/",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())

            elements = []
            # parsed_content_list: [{"type": "text"|"icon", "bbox": [x1,y1,x2,y2] (0-1), "content": "..."}]
            for i, item in enumerate(result.get("parsed_content_list", [])):
                bbox_ratio = item.get("bbox", [0, 0, 0, 0])
                if not isinstance(bbox_ratio, (list, tuple)) or len(bbox_ratio) < 4:
                    continue  # 跳过无效 bbox
                # 比例坐标 → 绝对像素
                bbox = [
                    int(bbox_ratio[0] * w), int(bbox_ratio[1] * h),
                    int(bbox_ratio[2] * w), int(bbox_ratio[3] * h),
                ]
                cx = (bbox[0] + bbox[2]) // 2
                cy = (bbox[1] + bbox[3]) // 2
                content = item.get("content") or ""
                elem_type = item.get("type", "unknown")
                # 映射类型
                type_map = {"text": "text", "icon": "icon", "button": "button", "input": "input"}
                elem_type = type_map.get(elem_type, elem_type)

                elements.append(UIElement(
                    element_id=f"e{i + 1:03d}",
                    type=elem_type,
                    label=content,
                    bbox=bbox,
                    center_x=cx,
                    center_y=cy,
                    confidence=0.9,
                    source="omniparser_http",
                ))
            return elements
        except urllib.error.HTTPError as e:
            # 服务端错误（500）：可能是图片格式问题，OCR 兜底
            logger.warning(f"OmniParser HTTP {e.code}: {e.reason}，降级到 OCR")
            return self._detect_ocr_fallback(image)
        except urllib.error.URLError as e:
            # 网络错误：服务不可用，返回空
            logger.error(f"OmniParser 服务不可用: {e.reason}")
            return []
        except Exception as e:
            logger.warning(f"OmniParser HTTP 调用异常: {e}")
            return self._detect_ocr_fallback(image)

    def _detect_local(self, image: np.ndarray) -> List[UIElement]:
        """通过本地 OmniParser 模型"""
        try:
            if self._local_parser is None:
                from omniparser import Omniparser
                self._local_parser = Omniparser()

            result = self._local_parser.parse(image)
            elements = []
            for i, item in enumerate(result.get("elements", [])):
                bbox = item.get("bbox", [0, 0, 0, 0])
                cx = (bbox[0] + bbox[2]) // 2 if len(bbox) >= 4 else 0
                cy = (bbox[1] + bbox[3]) // 2 if len(bbox) >= 4 else 0
                elements.append(UIElement(
                    element_id=f"e{i + 1:03d}",
                    type=item.get("type", "unknown"),
                    label=item.get("text", ""),
                    bbox=bbox,
                    center_x=cx,
                    center_y=cy,
                    confidence=item.get("confidence", 0.85),
                    source="omniparser_local",
                ))
            return elements
        except Exception as e:
            logger.warning(f"OmniParser 本地模型调用失败: {e}")
            return self._detect_ocr_fallback(image)

    def _detect_ocr_fallback(self, image: np.ndarray) -> List[UIElement]:
        """OCR 降级：每行 OCR 文本 → UIElement"""
        if image is None or image.size == 0:
            return []
        if len(image.shape) < 2:
            return []  # 1D 图像无法做 OCR

        try:
            ocr_text = self._ocr_extract(image)
            if not ocr_text:
                return []

            elements = []
            h, w = image.shape[:2]
            lines = [l.strip() for l in ocr_text.split("\n") if l.strip()]

            for i, line in enumerate(lines):
                # 估算行位置（均匀分布）
                line_h = h / max(len(lines), 1)
                y1 = int(i * line_h)
                y2 = int((i + 1) * line_h)
                elements.append(UIElement(
                    element_id=f"e{i + 1:03d}",
                    type="text",
                    label=line,
                    bbox=[0, y1, w, y2],
                    center_x=w // 2,
                    center_y=(y1 + y2) // 2,
                    confidence=0.6,
                    source="ocr_fallback",
                ))
            return elements
        except Exception as e:
            logger.debug(f"OCR 降级失败: {e}")
            return []

    def _ocr_extract(self, image: np.ndarray) -> str:
        """OCR 文本提取（引擎缓存复用）"""
        if self._ocr_engine is None:
            # 按优先级初始化：RapidOCR > PaddleOCR
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._ocr_engine = ("rapid", RapidOCR())
            except ImportError:
                try:
                    from paddleocr import PaddleOCR
                    self._ocr_engine = ("paddle", PaddleOCR(lang='ch'))
                except ImportError:
                    self._ocr_engine = ("none", None)

        engine_type, engine = self._ocr_engine
        if engine is None:
            return ""

        try:
            if engine_type == "rapid":
                result, _ = engine(image)
                if result:
                    return "\n".join(item[1] for item in result if len(item) > 1)
            elif engine_type == "paddle":
                result = engine.ocr(image)
                if result and result[0]:
                    # PaddleOCR 3.6+ 返回 dict 格式
                    texts = result[0].get("rec_texts", [])
                    return "\n".join(t for t in texts if t)
        except Exception as e:
            logger.debug(f"OCR 提取失败: {e}")

        return ""

    def reset(self) -> None:
        self._prev_elements.clear()


def _elements_equal(a: List[UIElement], b: List[UIElement]) -> bool:
    """快速比较两组 UI 元素是否相同"""
    if len(a) != len(b):
        return False
    for ea, eb in zip(a, b):
        if ea.label != eb.label or ea.type != eb.type or ea.bbox != eb.bbox:
            return False
    return True
