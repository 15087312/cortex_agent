"""adapters 测试（此前 24% 覆盖）：指导/审查适配器"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from modules.thinking.adapters import (
    PreGenExpertGuidanceAdapter,
    OutputSystemReviewAdapter,
    DifferenceDetectorActivityNotifier,
    SecurityApiAdapter,
)


def test_difference_notifier_notify():
    n = DifferenceDetectorActivityNotifier()
    n.notify_activity()  # 不应抛异常


def test_security_api_adapter():
    adapter = SecurityApiAdapter()
    ok, msg = adapter.validate_input("hello")
    assert isinstance(ok, bool)
    assert isinstance(msg, str)
