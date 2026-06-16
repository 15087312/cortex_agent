"""流水线包 — 导出捕获后端、帧差检测器和感知流水线"""
from modules.perception.pipeline.capture import CaptureBackend, create_capture_backend
from modules.perception.pipeline.frame_diff import FrameDiffDetector, FrameDiffResult
from modules.perception.pipeline.pipeline import PerceptionPipeline

__all__ = [
    "CaptureBackend", "create_capture_backend",
    "FrameDiffDetector", "FrameDiffResult",
    "PerceptionPipeline",
]
