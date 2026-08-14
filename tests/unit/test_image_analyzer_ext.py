"""ImageAnalyzer 扩展测试：单例 / 初始化 / 模型加载 / UI 检测辅助

关键：绝不真实加载 torch/transformers/mlx-vlm（conftest 已全局 sys.modules=None），
全部注入假模块测试分支，避免触发真实模型推理与双 OpenMP 死锁。
"""
import asyncio
import base64
import io
import json
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

import pytest
from PIL import Image

import infra.data_process.core.image_analyzer as ia_mod
from config.settings import settings


def _make(**attrs):
    """创建不受单例污染的全新实例（结束后恢复单例）"""
    cls = ia_mod.ImageAnalyzer
    orig = cls._instance
    cls._instance = None
    try:
        inst = cls.__new__(cls)
        inst.__init__()
        for k, v in attrs.items():
            setattr(inst, k, v)
        return inst
    finally:
        cls._instance = orig


@pytest.fixture(autouse=True)
def _reset_model_cache():
    yield
    for k in ia_mod._MODEL_CACHE:
        ia_mod._MODEL_CACHE[k] = None


def _png(size=(4, 4), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _install_mlx(monkeypatch):
    """注入假 mlx_vlm 模块（load/generate/utils/prompt_utils）"""
    mod = types.ModuleType("mlx_vlm")
    mod.load = lambda n: ("model", "processor")
    mod.generate = lambda *a, **k: types.SimpleNamespace(text="生成结果")
    mod.utils = types.ModuleType("mlx_vlm.utils")
    mod.utils.load_config = lambda n: {"model_type": "mlx_vlm"}
    mod.prompt_utils = types.ModuleType("mlx_vlm.prompt_utils")
    mod.prompt_utils.apply_chat_template = lambda *a, **k: "tpl"
    monkeypatch.setitem(sys.modules, "mlx_vlm", mod)
    monkeypatch.setitem(sys.modules, "mlx_vlm.utils", mod.utils)
    monkeypatch.setitem(sys.modules, "mlx_vlm.prompt_utils", mod.prompt_utils)
    return mod


def _install_torch(monkeypatch, cuda=False, mps=False):
    class _NoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("torch")
    mod.cuda = types.SimpleNamespace(is_available=lambda: cuda)
    backends = types.ModuleType("torch.backends")
    backends.mps = types.SimpleNamespace(is_available=lambda: mps)
    mod.backends = backends
    mod.float16 = "fp16"
    mod.float32 = "fp32"
    mod.no_grad = lambda: _NoGrad()
    monkeypatch.setitem(sys.modules, "torch", mod)
    return mod


def _install_qwen_transformers(monkeypatch):
    tf = types.ModuleType("transformers")

    class _Proc:
        @staticmethod
        def from_pretrained(n):
            return "processor"

    class _Model:
        def __init__(self):
            self.to_called = None

        def to(self, d):
            self.to_called = d
            return self

        @staticmethod
        def from_pretrained(n, **k):
            return _Model()

    tf.AutoProcessor = _Proc
    tf.Qwen2VLForConditionalGeneration = _Model
    monkeypatch.setitem(sys.modules, "transformers", tf)
    qvu = types.ModuleType("qwen_vl_utils")
    qvu.process_vision_info = lambda m: ([], [])
    monkeypatch.setitem(sys.modules, "qwen_vl_utils", qvu)
    return tf


# ── 单例 & __init__ ─────────────────────────────────────────────────────────

def test_new_returns_singleton_and_warns_on_mismatch(monkeypatch):
    cls = ia_mod.ImageAnalyzer
    orig = cls._instance
    cls._instance = None
    fake_logger = MagicMock()
    monkeypatch.setattr(ia_mod, "logger", fake_logger)
    try:
        a1 = cls(model_type="qwen_vl")
        a2 = cls(model_type="openai")
        assert a2 is a1
        assert any("忽略新参数" in str(c) for c in fake_logger.warning.call_args_list)
    finally:
        cls._instance = orig


def test_new_same_params_returns_without_warning(monkeypatch):
    cls = ia_mod.ImageAnalyzer
    orig = cls._instance
    cls._instance = None
    fake_logger = MagicMock()
    monkeypatch.setattr(ia_mod, "logger", fake_logger)
    try:
        a1 = cls(model_type="qwen_vl")
        a2 = cls(model_type="qwen_vl")
        assert a2 is a1
        assert fake_logger.warning.call_count == 0
    finally:
        cls._instance = orig


def test_init_sets_fields_and_is_idempotent():
    cls = ia_mod.ImageAnalyzer
    orig = cls._instance
    cls._instance = None
    try:
        inst = cls.__new__(cls)
        inst.__init__(model_type="qwen_vl", local_model="LM")
        assert inst.model_type == "qwen_vl"
        assert inst._init_model_type == "qwen_vl"
        assert inst.local_model == "LM"
        assert inst.model is None and inst.processor is None
        assert inst._initialized is False and inst._init_done is True
        inst.__init__(model_type="openai")
        assert inst.model_type == "qwen_vl"
    finally:
        cls._instance = orig


# ── initialize() 分支 ───────────────────────────────────────────────────────

def test_initialize_already_initialized_returns(monkeypatch):
    inst = _make()
    inst._initialized = True
    called = []

    async def fake_load():
        called.append(1)

    inst._load_mlx_vlm = fake_load
    asyncio.run(inst.initialize())
    assert called == []


def test_initialize_auto_detects(monkeypatch):
    inst = _make()
    inst._detect_available_model = lambda: "openai"
    called = []

    async def fake_init():
        called.append("openai")

    inst._init_openai = fake_init
    asyncio.run(inst.initialize())
    assert called == ["openai"]
    assert inst._initialized is True


@pytest.mark.parametrize(
    "model_type,attr",
    [
        ("mlx_vlm", "_load_mlx_vlm"),
        ("qwen_vl", "_load_qwen_vl"),
        ("llava", "_load_llava"),
        ("openai", "_init_openai"),
    ],
)
def test_initialize_dispatches_backend(monkeypatch, model_type, attr):
    inst = _make(model_type=model_type)
    called = []

    async def fake():
        called.append(model_type)

    setattr(inst, attr, fake)
    asyncio.run(inst.initialize())
    assert called == [model_type]
    assert inst._initialized is True


def test_initialize_unsupported_raises():
    inst = _make(model_type="bad")
    with pytest.raises(ValueError):
        asyncio.run(inst.initialize())


# ── _detect_available_model：config.json 解析 ────────────────────────────────

def _set_local(monkeypatch, local_model):
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", local_model)
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")


def test_detect_mlx_config(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "mlx_vlm"}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", True)
    _install_mlx(monkeypatch)
    assert _make()._detect_available_model() == "mlx_vlm"


def test_detect_qwen_config(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "qwen2_vl", "architectures": ["Qwen2VLForConditionalGeneration"]})
    )
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    _install_qwen_transformers(monkeypatch)
    assert _make()._detect_available_model() == "qwen_vl"


def test_detect_llava_config(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "llava"}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "llava", types.ModuleType("llava"))
    assert _make()._detect_available_model() == "llava"


def test_detect_generic_transformers_config(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "gpt2", "architectures": ["GPT2LMHeadModel"]}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    tf = types.ModuleType("transformers")
    tf.AutoModelForCausalLM = object
    tf.AutoProcessor = object
    monkeypatch.setitem(sys.modules, "transformers", tf)
    assert _make()._detect_available_model() == "qwen_vl"


def test_detect_bad_config_falls_through(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text("{not valid json")
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "llava", None)
    assert _make()._detect_available_model() == "unavailable"


def test_detect_mlx_config_import_fail_falls_through(monkeypatch, tmp_path):
    """config 命中 mlx 但 mlx_vlm 不可导入 → 落到 transformers 通用加载"""
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "mlx_vlm"}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", True)
    monkeypatch.setitem(sys.modules, "mlx_vlm", None)
    _install_qwen_transformers(monkeypatch)
    assert _make()._detect_available_model() == "qwen_vl"


def test_detect_mlx_config_not_apple_skips_mlx(monkeypatch, tmp_path):
    """config 命中 mlx 但非 Apple Silicon → 跳过 mlx 分支，落到 transformers"""
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "mlx_vlm"}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    _install_mlx(monkeypatch)
    _install_qwen_transformers(monkeypatch)
    assert _make()._detect_available_model() == "qwen_vl"


def test_detect_qwen_config_import_fail_falls_through(monkeypatch, tmp_path):
    """config 命中 qwen 但 transformers 不可导入 → 全链降级 unavailable"""
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "qwen2_vl"}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "llava", None)
    assert _make()._detect_available_model() == "unavailable"


def test_detect_llava_config_import_fail_falls_through(monkeypatch, tmp_path):
    """config 命中 llava 但 llava 不可导入 → 全链降级 unavailable"""
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "llava"}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "llava", None)
    assert _make()._detect_available_model() == "unavailable"


def test_detect_generic_config_import_fail_falls_through(monkeypatch, tmp_path):
    """config 为通用模型但 transformers 不可导入 → 全链降级 unavailable"""
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "gpt2"}))
    _set_local(monkeypatch, str(tmp_path))
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "llava", None)
    assert _make()._detect_available_model() == "unavailable"


def test_detect_api_without_key_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "VISION_BACKEND", "api")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert _make()._detect_available_model() == "unavailable"


def test_detect_api_with_key_openai(monkeypatch):
    monkeypatch.setattr(settings, "VISION_BACKEND", "api")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk-test")
    assert _make()._detect_available_model() == "openai"


def test_detect_empty_local_model_skips_config_block(monkeypatch):
    """effective_vision_local_model 为空 → 跳过 config.json 解析块，直接库检测"""
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    monkeypatch.setattr(settings, "QWEN_VL_MODEL_NAME", "")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    _install_qwen_transformers(monkeypatch)
    assert _make()._detect_available_model() == "qwen_vl"


def test_detect_no_local_mlx_import_fail(monkeypatch):
    """无本地 config + Apple Silicon 但 mlx_vlm 不可导入 → 继续降级"""
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", True)
    monkeypatch.setitem(sys.modules, "mlx_vlm", None)
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "llava", None)
    assert _make()._detect_available_model() == "unavailable"


# ── _detect_available_model：无 config.json 的库检测 ─────────────────────────

def test_detect_no_local_model_mlx(monkeypatch):
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", True)
    _install_mlx(monkeypatch)
    assert _make()._detect_available_model() == "mlx_vlm"


def test_detect_no_local_model_transformers(monkeypatch):
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    _install_qwen_transformers(monkeypatch)
    assert _make()._detect_available_model() == "qwen_vl"


def test_detect_no_local_model_llava(monkeypatch):
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "llava", types.ModuleType("llava"))
    assert _make()._detect_available_model() == "llava"


def test_detect_no_local_model_falls_back_api(monkeypatch):
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "llava", None)
    assert _make()._detect_available_model() == "openai"


def test_detect_no_local_model_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "VISION_BACKEND", "local")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ia_mod, "_IS_APPLE_SILICON", False)
    monkeypatch.setitem(sys.modules, "transformers", None)
    monkeypatch.setitem(sys.modules, "llava", None)
    assert _make()._detect_available_model() == "unavailable"


# ── _load_mlx_vlm ───────────────────────────────────────────────────────────

def test_load_mlx_vlm_from_cache(monkeypatch):
    inst = _make(model_type="mlx_vlm", local_model="cache-model")
    fake_model, fake_proc = object(), object()
    ia_mod._MODEL_CACHE["mlx_vlm"] = {
        "name": "cache-model", "model": fake_model, "processor": fake_proc,
        "generate": lambda *a, **k: None,
    }
    _install_mlx(monkeypatch)
    asyncio.run(inst._load_mlx_vlm())
    assert inst.model is fake_model
    assert inst.processor is fake_proc
    assert inst._mlx_model_name == "cache-model"


def test_load_mlx_vlm_fresh_with_warmup(monkeypatch):
    inst = _make(model_type="mlx_vlm", local_model="new-model")
    mod = _install_mlx(monkeypatch)
    asyncio.run(inst._load_mlx_vlm())
    assert inst.model == "model"
    assert inst._mlx_generate is mod.generate
    assert ia_mod._MODEL_CACHE["mlx_vlm"]["name"] == "new-model"


def test_load_mlx_vlm_warmup_failure_non_fatal(monkeypatch):
    inst = _make(model_type="mlx_vlm", local_model="new-model")
    mod = _install_mlx(monkeypatch)
    mod.utils.load_config = lambda n: (_ for _ in ()).throw(RuntimeError("boom"))
    fake_logger = MagicMock()
    monkeypatch.setattr(ia_mod, "logger", fake_logger)
    asyncio.run(inst._load_mlx_vlm())
    assert inst.model == "model"
    assert inst.model_type == "mlx_vlm"
    assert any("warmup 失败" in str(c) for c in fake_logger.warning.call_args_list)


def test_load_mlx_vlm_import_failure(monkeypatch):
    inst = _make(model_type="mlx_vlm")
    asyncio.run(inst._load_mlx_vlm())
    assert inst.model_type == "unavailable"


# ── _analyze_mlx_vlm：RGBA / 缩放 / 配置延迟加载 ─────────────────────────────

def test_analyze_mlx_vlm_rgba_large_and_config_lazy(monkeypatch):
    inst = _make(model_type="mlx_vlm")
    inst._initialized = True
    inst._mlx_model_name = "fake-model"
    inst._mlx_config = None
    mod = _install_mlx(monkeypatch)
    inst._mlx_generate = mod.generate
    img = Image.new("RGBA", (1000, 800), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = asyncio.run(inst._analyze_mlx_vlm(buf.getvalue(), "描述"))
    assert "生成结果" in r["description"]
    assert inst._mlx_config == {"model_type": "mlx_vlm"}


def test_analyze_mlx_vlm_config_already_set(monkeypatch):
    inst = _make(model_type="mlx_vlm")
    inst._initialized = True
    inst._mlx_model_name = "fake-model"
    inst._mlx_config = {"already": "loaded"}
    mod = _install_mlx(monkeypatch)
    inst._mlx_generate = mod.generate
    r = asyncio.run(inst._analyze_mlx_vlm(_png(), "描述"))
    assert "生成结果" in r["description"]
    assert inst._mlx_config == {"already": "loaded"}


# ── _load_qwen_vl / _init_openai ────────────────────────────────────────────

@pytest.mark.parametrize(
    "cuda,mps,expected_device",
    [(False, False, "cpu"), (True, False, "cuda"), (False, True, "mps")],
)
def test_load_qwen_vl_device(monkeypatch, cuda, mps, expected_device):
    inst = _make(model_type="qwen_vl")
    monkeypatch.setattr(settings, "VISION_LOCAL_MODEL", "Qwen/Qwen2-VL-2B")
    _install_torch(monkeypatch, cuda=cuda, mps=mps)
    _install_qwen_transformers(monkeypatch)
    asyncio.run(inst._load_qwen_vl())
    assert inst._device == expected_device
    assert inst.processor == "processor"
    assert inst._process_vl is not None


def test_load_qwen_vl_import_failure(monkeypatch):
    inst = _make(model_type="qwen_vl")
    asyncio.run(inst._load_qwen_vl())
    assert inst.model_type == "unavailable"


def test_init_openai_without_key(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    asyncio.run(inst._init_openai())
    assert inst.model_type == "unavailable"


def test_init_openai_with_key(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "VISION_API_URL", "https://x/v1")
    asyncio.run(inst._init_openai())
    assert inst.model_type == "openai"


# ── analyze() 分发 ──────────────────────────────────────────────────────────

def test_analyze_initializes_when_needed(monkeypatch):
    inst = _make(model_type="openai")
    inst._initialized = False
    inst._detect_available_model = lambda: "openai"

    async def fake_init():
        inst._initialized = True

    async def fake_oa(data, prompt):
        return {"description": "ok", "format": "openai"}

    inst._init_openai = fake_init
    inst._analyze_openai = fake_oa
    r = asyncio.run(inst.analyze(b"x"))
    assert r["format"] == "openai"
    assert inst._initialized is True


@pytest.mark.parametrize(
    "model_type,attr,out",
    [
        ("mlx_vlm", "_analyze_mlx_vlm", "mlx_vlm"),
        ("qwen_vl", "_analyze_qwen_vl", "qwen_vl"),
        ("openai", "_analyze_openai", "openai"),
        ("mock", "_analyze_mock", "mock"),
    ],
)
def test_analyze_dispatch(monkeypatch, model_type, attr, out):
    inst = _make(model_type=model_type)
    inst._initialized = True

    async def fake(data, prompt):
        return {"format": out}

    setattr(inst, attr, fake)
    r = asyncio.run(inst.analyze(b"x", "p"))
    assert r["format"] == out


# ── _analyze_qwen_vl 大图缩放 ───────────────────────────────────────────────

def _qwen_fake_processor(description="描述结果"):
    class _Proc:
        def apply_chat_template(self, *a, **k):
            return "tpl"

        def __call__(self, *a, **k):
            class _In(dict):
                input_ids = ["ids"]

                def to(self, d):
                    return self

            return _In()

        def batch_decode(self, ids, **k):
            return [description]

    return _Proc()


def _run_qwen_vlm(monkeypatch, image_bytes, description="描述结果"):
    inst = _make(model_type="qwen_vl")
    inst._initialized = True
    inst._device = "cpu"
    _install_torch(monkeypatch)
    _install_qwen_transformers(monkeypatch)
    inst.processor = _qwen_fake_processor(description)
    inst.model = MagicMock()
    inst.model.generate.return_value = [[1, 2, 3]]
    return asyncio.run(inst._analyze_qwen_vl(image_bytes, "描述"))


def test_analyze_qwen_vl_resize(monkeypatch):
    img = Image.new("RGB", (1000, 800), (0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = _run_qwen_vlm(monkeypatch, buf.getvalue(), "大图描述")
    assert "大图描述" in r["description"]


def test_analyze_qwen_vl_small_image_skips_resize(monkeypatch):
    r = _run_qwen_vlm(monkeypatch, _png(), "小图描述")
    assert "小图描述" in r["description"]


# ── _analyze_openai ─────────────────────────────────────────────────────────

def _patch_openai_analyze(monkeypatch, captured, content="描述结果"):
    import openai as openai_mod
    import httpx as httpx_mod

    class FakeClient:
        def __init__(self, *a, **kw):
            self.base_url = kw.get("base_url")
            captured["base_url"] = self.base_url
            self.chat = type("C", (), {"completions": type("CC", (), {"create": self._create})()})()

        async def _create(self, **kw):
            captured["payload"] = kw
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()

        async def close(self):
            pass

    monkeypatch.setattr(openai_mod, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(httpx_mod, "AsyncClient", lambda *a, **k: object())


def test_analyze_openai_success_and_url_normalize(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "VISION_API_URL", "https://x.com/v1/chat/completions")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "VISION_API_MODEL", "gpt-4o")
    monkeypatch.setattr(settings, "VISION_API_FORMAT", "openai")
    captured = {}
    _patch_openai_analyze(monkeypatch, captured)
    r = asyncio.run(inst._analyze_openai(_png(), "描述"))
    assert r["format"] == "openai"
    assert r["description"] == "描述结果"
    assert captured["base_url"] == "https://x.com/v1"
    msg = captured["payload"]["messages"][0]["content"]
    assert isinstance(msg, list) and msg[0]["type"] == "image_url"


def test_analyze_openai_deepseek_format_inline(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "VISION_API_URL", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk")
    monkeypatch.setattr(settings, "VISION_API_MODEL", "deepseek-vl")
    monkeypatch.setattr(settings, "VISION_API_FORMAT", "dashscope")
    captured = {}
    _patch_openai_analyze(monkeypatch, captured)
    asyncio.run(inst._analyze_openai(_png(), "描述"))
    msg = captured["payload"]["messages"][0]["content"]
    assert isinstance(msg, str) and msg.startswith("[image: data:image/jpeg;base64,")


# ── _detect_objects / _classify_scene 异常分支 ─────────────────────────────

def test_detect_objects_invalid_image():
    inst = _make()
    assert asyncio.run(inst._detect_objects(b"not image")) == []


def test_classify_scene_invalid_image():
    inst = _make()
    assert asyncio.run(inst._classify_scene(b"not image")) == "unknown"


def test_detect_objects_valid_image():
    inst = _make()
    r = asyncio.run(inst._detect_objects(_png()))
    assert len(r) == 1 and r[0]["label"] == "物体"


# ── analyze_base64 / close / ensure_model_type ──────────────────────────────

def test_analyze_base64():
    inst = _make(model_type="unavailable")
    inst._initialized = True
    r = asyncio.run(inst.analyze_base64(base64.b64encode(b"x").decode()))
    assert r.get("format") == "unavailable"


def test_close_releases_model():
    inst = _make()
    inst.model = object()
    inst.processor = object()
    inst._mlx_generate = lambda: None
    inst._initialized = True
    inst._init_model_type = "qwen_vl"
    inst.model_type = "mlx_vlm"
    asyncio.run(inst.close())
    assert inst.model is None
    assert inst.processor is None
    assert inst._mlx_generate is None
    assert inst._initialized is False
    assert inst.model_type == "qwen_vl"


def test_close_without_model_or_mlx_generate():
    inst = _make()
    inst.model = None
    inst.processor = None
    inst._initialized = True
    inst._init_model_type = "openai"
    inst.model_type = "openai"
    asyncio.run(inst.close())
    assert inst._initialized is False
    assert inst.model_type == "openai"


def test_ensure_model_type_same_type_skips(monkeypatch):
    inst = _make(model_type="qwen_vl")
    inst._initialized = True
    called = []

    async def init():
        called.append("init")

    inst.initialize = init
    asyncio.run(inst.ensure_model_type("qwen_vl"))
    assert called == []


def test_ensure_model_type_switch_reinitializes(monkeypatch):
    inst = _make(model_type="openai")
    inst._initialized = True
    events = []

    async def close():
        events.append("close")
        inst._initialized = False

    async def init():
        events.append("init")

    inst.close = close
    inst.initialize = init
    asyncio.run(inst.ensure_model_type("qwen_vl"))
    assert events == ["close", "init"]
    assert inst.model_type == "qwen_vl"


def test_ensure_model_type_uninitialized(monkeypatch):
    inst = _make(model_type="openai")
    inst._initialized = False
    called = []

    async def init():
        called.append("init")

    inst.initialize = init
    asyncio.run(inst.ensure_model_type("qwen_vl"))
    assert called == ["init"]
    assert inst.model_type == "qwen_vl"


# ── detect_ui_elements 分发 ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "model_type,attr",
    [("qwen_vl", "_detect_ui_qwen_vl"), ("openai", "_detect_ui_openai")],
)
def test_detect_ui_elements_dispatch(monkeypatch, model_type, attr):
    inst = _make(model_type=model_type)
    inst._initialized = True

    async def fake(data, element_types):
        return {"elements": ["e"], "from": attr}

    setattr(inst, attr, fake)
    r = asyncio.run(inst.detect_ui_elements(_png(), ["button"]))
    assert r["from"] == attr


def test_detect_ui_elements_initializes_if_needed(monkeypatch):
    inst = _make(model_type="unavailable")
    inst._initialized = False

    async def fake_init():
        inst._initialized = True

    inst.initialize = fake_init
    r = asyncio.run(inst.detect_ui_elements(_png(), ["button"]))
    assert r.get("mock") is True
    assert inst._initialized is True


def test_detect_ui_elements_mock_branch():
    inst = _make(model_type="unavailable")
    inst._initialized = True
    r = asyncio.run(inst.detect_ui_elements(_png(), ["button"]))
    assert r == {"elements": [], "layout": {}, "mock": True, "error": "视觉后端不可用"}


def test_detect_ui_qwen_valid_json(monkeypatch):
    inst = _make(model_type="qwen_vl")

    async def fake_analyze(data, prompt):
        return {"description": '{"elements": [{"type": "button"}]}'}

    inst._analyze_qwen_vl = fake_analyze
    r = asyncio.run(inst._detect_ui_qwen_vl(_png(), ["button"]))
    assert r["elements"][0]["type"] == "button"


def test_detect_ui_qwen_invalid_json_fallback(monkeypatch):
    inst = _make(model_type="qwen_vl")

    async def fake_analyze(data, prompt):
        return {"description": "not json"}

    async def fake_mock(data, types):
        return {"mock": True}

    inst._analyze_qwen_vl = fake_analyze
    inst._detect_ui_mock = fake_mock
    r = asyncio.run(inst._detect_ui_qwen_vl(_png(), ["button"]))
    assert r == {"mock": True}


# ── _detect_ui_openai ───────────────────────────────────────────────────────

def _patch_openai_client(monkeypatch, captured, content):
    import openai as openai_mod
    import httpx as httpx_mod

    class FakeClient:
        def __init__(self, *a, **kw):
            self.base_url = kw.get("base_url")
            self.chat = type("C", (), {"completions": type("CC", (), {"create": self._create})()})()

        async def _create(self, **kw):
            captured["payload"] = kw
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})()})()]})()

        async def close(self):
            pass

    monkeypatch.setattr(openai_mod, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(httpx_mod, "AsyncClient", lambda *a, **k: object())


def test_detect_ui_openai_deepseek_inline(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "OPENAI_API_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk")
    monkeypatch.setattr(settings, "IMAGE_MODEL_NAME", "deepseek-vl")
    captured = {}
    _patch_openai_client(monkeypatch, captured, '{"elements": [{"type": "button"}]}')
    r = asyncio.run(inst._detect_ui_openai(_png(), ["button"]))
    assert r["elements"][0]["type"] == "button"
    content = captured["payload"]["messages"][0]["content"]
    assert isinstance(content, str) and content.startswith("[image: data:image/jpeg;base64,")


def test_detect_ui_openai_image_url(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "OPENAI_API_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk")
    monkeypatch.setattr(settings, "IMAGE_MODEL_NAME", "gpt-4o")
    captured = {}
    _patch_openai_client(monkeypatch, captured, '{"elements": []}')
    asyncio.run(inst._detect_ui_openai(_png(), ["button"]))
    content = captured["payload"]["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"


def test_detect_ui_openai_normalizes_chat_completions_url(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "OPENAI_API_BASE_URL", "https://x.com/v1/chat/completions")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk")
    monkeypatch.setattr(settings, "IMAGE_MODEL_NAME", "gpt-4o")
    captured = {}
    _patch_openai_client(monkeypatch, captured, "{}")
    asyncio.run(inst._detect_ui_openai(_png(), ["button"]))
    assert captured["payload"]["messages"][0]["content"][0]["type"] == "image_url"


def test_detect_ui_openai_invalid_json_fallback(monkeypatch):
    inst = _make(model_type="openai")
    monkeypatch.setattr(settings, "OPENAI_API_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk")
    monkeypatch.setattr(settings, "IMAGE_MODEL_NAME", "gpt-4o")
    captured = {}
    _patch_openai_client(monkeypatch, captured, "不是json")
    async def fake_mock(data, types):
        return {"mock": True}
    inst._detect_ui_mock = fake_mock
    r = asyncio.run(inst._detect_ui_openai(_png(), ["button"]))
    assert r == {"mock": True}


# ── 工具方法：_estimate_grid / draw_elements / 查找 ─────────────────────────

def test_estimate_grid():
    inst = _make()
    assert inst._estimate_grid(800, 600) == "2x2"
    assert inst._estimate_grid(0, 0) == "1x1"


def test_draw_elements(monkeypatch):
    inst = _make()
    image_bytes = _png(size=(200, 100))
    elements = [{"type": "button", "text": "提交", "bounds": {"x": 10, "y": 10, "width": 50, "height": 30}}]
    out = inst.draw_elements(image_bytes, elements)
    assert out.startswith(b"\x89PNG")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        inst.draw_elements(image_bytes, elements, output_path=f.name)
        assert os.path.exists(f.name)

    long_label = [{"type": "button", "text": "x" * 40, "bounds": {"x": 0, "y": 0, "width": 10, "height": 10}}]
    inst.draw_elements(image_bytes, long_label)


def test_draw_elements_invalid_image_returns_input():
    inst = _make()
    bad = b"not an image"
    assert inst.draw_elements(bad, []) == bad


def test_get_click_point():
    p = ia_mod.ImageAnalyzer.get_click_point({"center": {"x": 100, "y": 200}}, offset_range=0, random_seed=1)
    assert p == (100, 200)
    p2 = ia_mod.ImageAnalyzer.get_click_point({"center": {"x": 100, "y": 200}}, offset_range=5, random_seed=42)
    assert -5 <= p2[0] - 100 <= 5
    p3 = ia_mod.ImageAnalyzer.get_click_point({}, random_seed=1)
    assert -5 <= p3[0] <= 5 and -5 <= p3[1] <= 5


def test_find_element_by_text():
    elems = [{"type": "button", "text": "提交"}, {"type": "input", "text": "用户名"}]
    assert ia_mod.ImageAnalyzer.find_element_by_text(elems, "提交")["type"] == "button"
    assert ia_mod.ImageAnalyzer.find_element_by_text(elems, "提交", "input") is None
    assert ia_mod.ImageAnalyzer.find_element_by_text(elems, "不存在") is None
    assert ia_mod.ImageAnalyzer.find_element_by_text(elems, "提") is not None


def test_find_element_by_color():
    elems = [{"colors": {"bg": "#3498db", "text": "#ffffff"}}]
    assert ia_mod.ImageAnalyzer.find_element_by_color(elems, "#3498db") is not None
    assert ia_mod.ImageAnalyzer.find_element_by_color(elems, "#000000") is None
    assert ia_mod.ImageAnalyzer.find_element_by_color([{}], "#fff") is None


# ── UIClickHelper ───────────────────────────────────────────────────────────

def test_ui_click_helper_flow(monkeypatch):
    analyzer = _make()
    async def fake_detect(data):
        return {"elements": [{"type": "button", "text": "确定", "center": {"x": 10, "y": 20}}], "layout": {}}
    analyzer.detect_ui_elements = fake_detect
    helper = ia_mod.UIClickHelper(analyzer=analyzer)

    els = asyncio.run(helper.detect_from_image(b"img"))
    assert len(els) == 1
    assert helper._image_data == b"img"

    helper.set_elements([{"type": "button", "text": "OK", "center": {"x": 100, "y": 100}}])
    assert helper.find_by_text("OK") is not None
    assert helper.find_by_text("OK", "input") is None
    helper.set_elements([{"colors": {"bg": "#fff"}, "center": {"x": 1, "y": 2}}])
    assert helper.find_by_color("#fff") is not None
    assert helper.get_click_point({"center": {"x": 5, "y": 6}}, offset_range=0) == (5, 6)

    helper.set_elements([{"type": "button", "text": "OK", "center": {"x": 100, "y": 100}}])
    assert helper.click(text="OK", offset_range=0) == (100, 100)
    helper.set_elements([{"colors": {"bg": "#123456"}, "center": {"x": 7, "y": 8}}])
    assert helper.click(color="#123456", offset_range=0) == (7, 8)
    helper.set_elements([{"type": "button", "text": "OK", "center": {"x": 1, "y": 2}}])
    assert helper.click(text="nope") is None
    assert helper.click() is None


def test_ui_click_helper_analyze_with_coordinates(monkeypatch):
    analyzer = _make(model_type="unavailable")
    helper = ia_mod.UIClickHelper(analyzer=analyzer)
    helper.model_type = "unavailable"
    async def fake_detect(data):
        return {"elements": [{"type": "button", "text": "确定"}], "layout": {"grid": "1x1"}}
    helper.detect_ui_elements = fake_detect
    async def fake_mock(data, prompt):
        return {"description": "mock answer"}
    helper._analyze_mock = fake_mock
    r = asyncio.run(helper.analyze_with_coordinates(b"img", "按钮在哪里"))
    assert r["answer"] == "mock answer"
    assert r["raw_query"] == "按钮在哪里"
    assert r["layout"] == {"grid": "1x1"}


def test_ui_click_helper_analyze_with_coordinates_openai(monkeypatch):
    analyzer = _make(model_type="openai")
    helper = ia_mod.UIClickHelper(analyzer=analyzer)
    helper.model_type = "openai"
    async def fake_detect(data):
        return {"elements": [], "layout": {}}
    helper.detect_ui_elements = fake_detect
    async def fake_oa(data, prompt):
        return {"description": "openai answer"}
    helper._analyze_openai = fake_oa
    r = asyncio.run(helper.analyze_with_coordinates(b"img"))
    assert r["answer"] == "openai answer"


# ── get_default_analyzer ────────────────────────────────────────────────────

def test_get_default_analyzer_singleton(monkeypatch):
    monkeypatch.setattr(ia_mod, "_default_analyzer", None)

    async def fake_initialize(self):
        self._initialized = True

    monkeypatch.setattr(ia_mod.ImageAnalyzer, "initialize", fake_initialize)
    a1 = asyncio.run(ia_mod.get_default_analyzer())
    a2 = asyncio.run(ia_mod.get_default_analyzer())
    assert a1 is a2
