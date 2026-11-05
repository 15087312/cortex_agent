# -*- coding: utf-8 -*-
"""system doctor 自检单测 — 外部条件全部可控注入，不依赖真实环境。"""

import pytest


def _run():
    from api.doctor import run_doctor
    return run_doctor()


def _by_name(res, name):
    return next(c for c in res["data"]["checks"] if c["name"] == name)


@pytest.fixture
def healthy_env(monkeypatch, tmp_path):
    """让全部检查项都可过的最小环境（patch 真实单例，doctor 运行时才导入它）。"""
    import infra.data_process.core.video_analyzer as va
    from config.settings import settings as _real

    monkeypatch.setattr(va, "find_ffmpeg_tool", lambda n: f"/tmp/fixed/{n}")
    monkeypatch.setattr(_real, "LARGE_MODEL_API_KEY", "k", raising=True)
    monkeypatch.setattr(_real, "OPENAI_API_KEY", "", raising=True)
    monkeypatch.setattr(_real, "VISION_API_KEY", "k", raising=True)
    monkeypatch.setattr(_real, "VISION_API_URL", "http://v", raising=True)
    # embedding：用临时目录冒充模型目录
    emb = tmp_path / "emb" / "data" / "memory" / "embeddings" / "models"
    emb.mkdir(parents=True)
    (emb / "m.bin").write_bytes(b"x")
    monkeypatch.setenv("_MEIPASS", str(tmp_path / "emb"))
    return _real


class TestDoctor:
    def test_all_healthy(self, healthy_env, monkeypatch):
        monkeypatch.setattr("builtins.__import__", __import__, raising=False)
        res = _run()
        assert res["success"] is True
        assert res["data"]["healthy"] is True
        names = {c["name"] for c in res["data"]["checks"]}
        assert {"ffmpeg", "llm_keys", "vision_keys", "embedding_models",
                "whisper", "pyaudio", "pyautogui", "flac", "whisper_weights"} <= names

    def test_ffmpeg_missing_is_required_with_fix(self, healthy_env, monkeypatch):
        import infra.data_process.core.video_analyzer as va
        monkeypatch.setattr(va, "find_ffmpeg_tool", lambda n: None)
        res = _run()
        ff = _by_name(res, "ffmpeg")
        assert ff["ok"] is False and ff["severity"] == "required"
        assert "brew install ffmpeg" in ff["fix"]
        assert res["data"]["healthy"] is False

    def test_ffmpeg_missing_not_required_when_imports_fail(self, healthy_env, monkeypatch):
        """ffmpeg 失败 + 非必需项失败 → healthy 只由 required 决定。"""
        import infra.data_process.core.video_analyzer as va
        monkeypatch.setattr(va, "find_ffmpeg_tool", lambda n: None)

        def _boom(import_name, pkg_name, capability):
            return {"ok": False, "severity": "optional", "detail": "x", "fix": "y"}

        import api.doctor as doc
        monkeypatch.setattr(doc, "_check_import", _boom)
        res = _run()
        assert res["data"]["healthy"] is False

    def test_llm_key_missing(self, monkeypatch):
        import infra.data_process.core.video_analyzer as va
        from config.settings import settings as _real
        monkeypatch.setattr(va, "find_ffmpeg_tool", lambda n: f"/x/{n}")
        monkeypatch.setattr(_real, "LARGE_MODEL_API_KEY", "", raising=True)
        monkeypatch.setattr(_real, "OPENAI_API_KEY", "", raising=True)
        res = _run()
        llm = _by_name(res, "llm_keys")
        assert llm["ok"] is False and "OPENAI_API_KEY" in llm["fix"]

    def test_vision_key_missing(self, monkeypatch):
        import infra.data_process.core.video_analyzer as va
        from config.settings import settings as _real
        monkeypatch.setattr(va, "find_ffmpeg_tool", lambda n: f"/x/{n}")
        monkeypatch.setattr(_real, "VISION_API_KEY", "", raising=True)
        res = _run()
        v = _by_name(res, "vision_keys")
        assert v["ok"] is False

    def test_import_check_ok_and_fail(self, monkeypatch):
        import api.doctor as doc
        res_ok = doc._check_import("json", "json-pkg", "某能力")
        assert res_ok["ok"] is True

        def _missing(name, *a, **k):
            raise ImportError(f"No module named {name}")

        monkeypatch.setattr(doc, "__import__", _missing, raising=False)
        res_fail = doc._check_import("no_such_mod", "no-such-pkg", "某能力")
        assert res_fail["ok"] is False
        assert "pip install no-such-pkg" in res_fail["fix"]

    def test_embedding_models_found_and_missing(self, monkeypatch, tmp_path):
        import api.doctor as doc
        root = tmp_path / "has"
        emb = root / "data" / "memory" / "embeddings" / "models"
        emb.mkdir(parents=True)
        (emb / "m.bin").write_bytes(b"x")
        monkeypatch.setattr(doc, "_embedding_roots", lambda: [root])
        assert doc._check_embedding_models()["ok"] is True

        monkeypatch.setattr(doc, "_embedding_roots", lambda: [tmp_path / "empty"])
        res = doc._check_embedding_models()
        assert res["ok"] is False

    def test_whisper_weights_missing_is_optional(self, healthy_env, monkeypatch):
        # 需要 healthy_env 兜住 required 项（CI 无真实 API key / embedding 模型），
        # 否则 healthy 会被环境缺失拉低，断言与 whisper_weights 无关地失败。
        monkeypatch.setenv("HOME", "/nonexistent-home")
        import infra.data_process.core.video_analyzer as va
        monkeypatch.setattr(va, "find_ffmpeg_tool", lambda n: f"/x/{n}")
        res = _run()
        w = _by_name(res, "whisper_weights")
        assert w["ok"] is False and w["severity"] == "optional"
        assert res["data"]["healthy"] is True   # 可选项不拉低总体

    def test_check_never_raises(self, monkeypatch):
        """单项检查炸了也要兜住，结果仍 9 项齐全。"""
        import infra.data_process.core.video_analyzer as va

        def _explode(n):
            raise RuntimeError("boom")

        monkeypatch.setattr(va, "find_ffmpeg_tool", _explode)
        res = _run()
        assert len(res["data"]["checks"]) == 9
        assert res["success"] is True
