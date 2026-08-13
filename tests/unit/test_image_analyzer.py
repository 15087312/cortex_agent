"""ImageAnalyzer 真实视觉路径测试（此前零覆盖，见 docs/ERRORS_AND_FIXES.md §27.3）

覆盖：
- _detect_available_model 的 api/local 分支（VISION_BACKEND + key 判定）
- _analyze_openai 的 base_url 归一化（/chat/completions 去重，§26 回归）
- _analyze_openai 的 deepseek base64 内联 / openai image_url 两种消息格式
- analyze() 在视觉后端不可用时的降级返回
"""
import base64
import io

import pytest

from infra.data_process.core import image_analyzer as ia_mod

# _detect_available_model 会真实 import mlx_vlm → transformers 重库，
# 超过 pytest 全局 --timeout=10（与 conscience 测试同款），放宽到 60s
pytestmark = pytest.mark.timeout(60)


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _fresh():
    """绕过单例创建干净实例"""
    inst = ia_mod.ImageAnalyzer.__new__(ia_mod.ImageAnalyzer)
    inst.model_type = "auto"
    inst._init_model_type = "auto"
    inst.local_model = None
    inst.model = None
    inst.processor = None
    inst._initialized = False
    return inst


# ── _detect_available_model 分支 ──────────────────────────────────────────

def test_detect_api_without_key_unavailable(monkeypatch):
    inst = _fresh()
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_BACKEND", "api")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert inst._detect_available_model() == "unavailable"


def test_detect_api_with_key_openai(monkeypatch):
    inst = _fresh()
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_BACKEND", "api")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk-test")
    assert inst._detect_available_model() == "openai"


def test_detect_unknown_backend_falls_back_to_local(monkeypatch):
    """未知 VISION_BACKEND 值容错回退到本地检测，不直接判死"""
    import sys
    inst = _fresh()
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_BACKEND", "不存在的后端")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    # 不真实加载 mlx_vlm/transformers/llava：会拉起 torch 等重库，与已加载的
    # faiss/onnxruntime 双 OpenMP 冲突 → 测试进程 GIL 死锁（整批随机挂起）。
    for m in ("mlx_vlm", "transformers", "llava", "qwen_vl_utils"):
        monkeypatch.setitem(sys.modules, m, None)
    assert inst._detect_available_model() == "unavailable"


# ── _analyze_openai base_url 归一化（§26 回归）──────────────────────────────

class _FakeCompletions:
    def __init__(self, base_url):
        self.base_url = base_url

    async def create(self, **kwargs):
        self.payload = kwargs
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "描述"})()})()]})()


def _patch_openai(monkeypatch, base_url, httpx_ok=True):
    import openai as openai_mod
    import httpx as httpx_mod
    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            self.base_url = kw.get("base_url")
            captured["base_url"] = self.base_url
            self.chat = type("C", (), {"completions": _FakeCompletions(self.base_url)})()
            captured["client"] = self

        async def close(self):
            pass

    monkeypatch.setattr(openai_mod, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(httpx_mod, "AsyncClient", lambda *a, **kw: type("H", (), {"__aenter__": lambda s: s, "__aexit__": lambda *a: None})())
    return captured


def test_analyze_openai_strips_chat_completions_suffix(monkeypatch):
    inst = _fresh()
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_API_URL", "https://openrouter.ai/api/v1/chat/completions")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "VISION_API_MODEL", "gpt-4o")
    monkeypatch.setattr(settings, "VISION_API_FORMAT", "openai")
    cap = _patch_openai(monkeypatch, None)
    import asyncio
    asyncio.run(inst._analyze_openai(_png_bytes(), "描述"))
    assert cap["base_url"] == "https://openrouter.ai/api/v1", f"base_url 未去重: {cap['base_url']}"


def test_analyze_openai_keeps_plain_base_url(monkeypatch):
    inst = _fresh()
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_API_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "VISION_API_MODEL", "gpt-4o")
    monkeypatch.setattr(settings, "VISION_API_FORMAT", "openai")
    cap = _patch_openai(monkeypatch, None)
    import asyncio
    asyncio.run(inst._analyze_openai(_png_bytes(), "描述"))
    assert cap["base_url"] == "https://api.openai.com/v1"


# ── deepseek base64 内联 vs openai image_url 格式 ──────────────────────────

def test_analyze_openai_deepseek_uses_inline_base64(monkeypatch):
    inst = _fresh()
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_API_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "VISION_API_MODEL", "deepseek-vl")
    monkeypatch.setattr(settings, "VISION_API_FORMAT", "")
    cap = _patch_openai(monkeypatch, None)
    import asyncio
    asyncio.run(inst._analyze_openai(_png_bytes(), "描述"))
    msg = cap["client"].chat.completions.payload["messages"][0]["content"]
    assert isinstance(msg, str) and msg.startswith("[image: data:image/jpeg;base64,")


def test_analyze_openai_openai_uses_image_url(monkeypatch):
    inst = _fresh()
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_API_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "VISION_API_MODEL", "gpt-4o")
    monkeypatch.setattr(settings, "VISION_API_FORMAT", "openai")
    cap = _patch_openai(monkeypatch, None)
    import asyncio
    asyncio.run(inst._analyze_openai(_png_bytes(), "描述"))
    msg = cap["client"].chat.completions.payload["messages"][0]["content"]
    assert isinstance(msg, list)
    assert msg[0]["type"] == "image_url"
    assert "data:image/jpeg;base64," in msg[0]["image_url"]["url"]


# ── analyze() 降级返回 ─────────────────────────────────────────────────────

def test_analyze_unavailable_returns_error():
    inst = _fresh()
    inst.model_type = "unavailable"
    inst._initialized = True
    import asyncio
    r = asyncio.run(inst.analyze(_png_bytes(), "描述"))
    assert "视觉后端不可用" in r.get("error", "")
    assert r.get("format") == "unavailable"


# ── 本地视觉：_analyze_qwen_vl（mock transformers 推理）─────────────────────

def test_analyze_qwen_vl(monkeypatch):
    import asyncio
    inst = _fresh()
    inst.model_type = "qwen_vl"
    inst._initialized = True
    inst._device = "cpu"

    class FakeProc:
        def apply_chat_template(self, *a, **k):
            return "tpl"
        def __call__(self, *a, **k):
            class _In(dict):
                input_ids = ["ids"]
                def __init__(self, **kw):
                    super().__init__(kw)
                def to(self, d):
                    return self
            return _In()

    class FakeModel:
        def generate(self, **k):
            class Out:
                def __len__(self):
                    return 5
            return [[1, 2, 3]]

    inst.processor = FakeProc()
    inst.model = FakeModel()

    import modules.thinking  # noqa
    # batch_decode 需要返回 "描述"
    def fake_decode(ids, **k):
        return ["一张测试图片"]
    inst.processor.batch_decode = fake_decode

    # 不真实加载 torch/qwen_vl_utils（conftest 已全局拦截，避免测试进程 GIL 死锁）
    import sys
    import types
    fake_torch = types.ModuleType("torch")
    class _NG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    fake_torch.no_grad = lambda: _NG()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    fake_qvu = types.ModuleType("qwen_vl_utils")
    fake_qvu.process_vision_info = lambda messages: ([], [])
    monkeypatch.setitem(sys.modules, "qwen_vl_utils", fake_qvu)

    import infra.data_process.core.image_analyzer as ia_mod
    r = asyncio.run(inst._analyze_qwen_vl(_png_bytes(), "描述"))
    assert "一张测试图片" in r["description"]


# ── 本地视觉：_analyze_mlx_vlm（mock mlx_vlm）──────────────────────────────

def test_analyze_mlx_vlm(monkeypatch):
    import asyncio
    import sys as _sys
    import types
    inst = _fresh()
    inst.model_type = "mlx_vlm"
    inst._initialized = True
    inst._mlx_model_name = "fake-model"
    inst._mlx_config = None

    mlx_mod = types.ModuleType("mlx_vlm")
    mlx_mod.load = lambda n: None
    mlx_mod.generate = lambda *a, **k: types.SimpleNamespace(text="视觉描述内容")
    inst._mlx_generate = mlx_mod.generate
    mlx_mod.utils = types.ModuleType("mlx_vlm.utils")
    mlx_mod.utils.load_config = lambda n: {}
    mlx_mod.prompt_utils = types.ModuleType("mlx_vlm.prompt_utils")
    mlx_mod.prompt_utils.apply_chat_template = lambda *a, **k: "tpl"
    monkeypatch.setitem(_sys.modules, "mlx_vlm", mlx_mod)
    monkeypatch.setitem(_sys.modules, "mlx_vlm.utils", mlx_mod.utils)
    monkeypatch.setitem(_sys.modules, "mlx_vlm.prompt_utils", mlx_mod.prompt_utils)

    r = asyncio.run(inst._analyze_mlx_vlm(_png_bytes(), "描述"))
    assert "视觉描述内容" in r["description"]


# ── detect_ui_elements 降级 ────────────────────────────────────────────────

def test_detect_ui_elements_mock_fallback(monkeypatch):
    import asyncio
    inst = _fresh()
    inst.model_type = "unavailable"
    inst._initialized = True
    r = asyncio.run(inst.detect_ui_elements(_png_bytes(), ["button"]))
    assert isinstance(r, dict)
    assert "elements" in r
