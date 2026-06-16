"""捕获后端包 — 导出 CaptureBackend 抽象基类和工厂函数"""
from modules.perception.pipeline.capture import CaptureBackend, create_capture_backend

__all__ = ["CaptureBackend", "create_capture_backend"]
