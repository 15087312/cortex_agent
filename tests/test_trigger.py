"""perception/trigger 测试（此前 31% 覆盖）：空闲计时器与主动触发"""
from unittest.mock import MagicMock

from modules.perception.trigger import IdleTimer, ProactiveTrigger, _build_outreach_system_prompt


def test_build_outreach_system_prompt():
    p = _build_outreach_system_prompt(role="orchestrator", tier="large")
    assert "主模型" in p
    assert isinstance(p, str)


def test_idle_timer():
    t = IdleTimer()
    t.notify_activity()
    assert t.idle_minutes >= 0
    assert t.idle_seconds >= 0


def test_proactive_trigger_init_and_notify():
    tr = ProactiveTrigger()
    tr.notify_activity()
    assert tr._trigger_count == 0


def test_proactive_trigger_start_subscribes():
    tr = ProactiveTrigger()
    bus = MagicMock()
    bus.subscribe.return_value = "sub-1"
    tr.start(bus)
    assert tr._sub_id == "sub-1"
    assert bus.subscribe.called


def test_proactive_trigger_stop():
    tr = ProactiveTrigger()
    bus = MagicMock()
    bus.subscribe.return_value = "sub-1"
    tr.start(bus)
    tr._event_bus = bus
    tr.stop()
    assert tr._sub_id == ""
