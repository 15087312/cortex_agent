"""共享 OCR 引擎 — 全局单例，避免重复加载 PaddleOCR/RapidOCR

用法:
    engine, engine_type = get_ocr_engine()
    if engine_type == "paddle":
        result = engine.ocr(image)
    elif engine_type == "rapid":
        result, _ = engine(image)
"""
import threading
from typing import Optional, Tuple, Any

from utils.logger import setup_logger

logger = setup_logger("ocr_utils")

_ocr_engine: Any = None
_ocr_type: Optional[str] = None
_ocr_lock = threading.Lock()


def get_ocr_engine() -> Tuple[Optional[Any], Optional[str]]:
    """获取全局共享 OCR 引擎（线程安全，只初始化一次）"""
    global _ocr_engine, _ocr_type
    if _ocr_engine is not None:
        return _ocr_engine, _ocr_type

    with _ocr_lock:
        if _ocr_engine is not None:
            return _ocr_engine, _ocr_type

        # 按优先级尝试
        try:
            from paddleocr import PaddleOCR
            _ocr_engine = PaddleOCR(lang="ch")
            _ocr_type = "paddle"
            logger.info("共享 OCR 引擎: PaddleOCR")
            return _ocr_engine, _ocr_type
        except ImportError:
            pass

        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
            _ocr_type = "rapid"
            logger.info("共享 OCR 引擎: RapidOCR")
            return _ocr_engine, _ocr_type
        except ImportError:
            pass

        _ocr_engine = None
        _ocr_type = None
        logger.warning("共享 OCR 引擎: 不可用（未安装 PaddleOCR/RapidOCR）")
        return None, None
