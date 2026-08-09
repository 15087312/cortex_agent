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


def test_pregen_expert_guidance_success(monkeypatch):
    adapter = PreGenExpertGuidanceAdapter()
    fake_conscience = MagicMock()
    fake_conscience.think = AsyncMock(return_value="内心想法")

    import modules.thinking.conscience as conscience_mod
    import infra.model.small_model_client as smc_mod
    monkeypatch.setattr(conscience_mod, "get_conscience", lambda: fake_conscience)
    monkeypatch.setattr(smc_mod, "SmallModelClient", lambda **kw: MagicMock())

    out = asyncio.run(adapter.run("user input"))
    assert out == {"inner_thoughts": "内心想法"}


def test_pregen_expert_guidance_failure(monkeypatch):
    adapter = PreGenExpertGuidanceAdapter()
    import modules.thinking.conscience as conscience_mod
    import infra.model.small_model_client as smc_mod
    monkeypatch.setattr(conscience_mod, "get_conscience", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(smc_mod, "SmallModelClient", lambda **kw: MagicMock())
    out = asyncio.run(adapter.run("user input"))
    assert out == {}


def test_output_system_review_adapter_empty():
    adapter = OutputSystemReviewAdapter()
    out = asyncio.run(adapter.review(""))
    assert out == ""


def test_output_system_review_adapter_clean(monkeypatch):
    adapter = OutputSystemReviewAdapter()
    fake = MagicMock()
    fake.clean_response.return_value = "清洗后"
    import modules.output_system.core as core_mod
    monkeypatch.setattr(core_mod, "OutputSystem", fake)
    out = asyncio.run(adapter.review("原始"))
    assert out == "清洗后"


def test_output_system_review_adapter_fallback(monkeypatch):
    adapter = OutputSystemReviewAdapter()
    import modules.output_system.core as core_mod
    monkeypatch.setattr(core_mod, "OutputSystem", MagicMock(clean_response=MagicMock(side_effect=RuntimeError)))
    out = asyncio.run(adapter.review("原始"))
    assert out == "原始"
