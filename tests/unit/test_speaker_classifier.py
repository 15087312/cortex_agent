"""声纹识别 — 档案持久化 / 嵌入提取 / 注册与比对 / 默认单例

此前 161 行里 133 行没跑过。这段逻辑决定「谁在说话」能不能记住：
档案损坏不能崩，依赖缺失要说清楚，音频太短要返回 None 而不是瞎猜。
"""
import json
import sys
import types

import numpy as np
import pytest

from infra.data_process.core import speaker_classifier as sc_mod
from infra.data_process.core.speaker_classifier import SpeakerClassifier, get_default_classifier

DIM = 8


def _emb(seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(size=DIM).astype(np.float32)


class FakeEncoder:
    def __init__(self, embedding=None):
        self.embedding = embedding
        self.calls = 0

    def embed_utterance(self, wav):
        self.calls += 1
        return self.embedding if self.embedding is not None else _emb(1)


@pytest.fixture
def clf(tmp_path):
    c = SpeakerClassifier(data_dir=str(tmp_path / "profiles"))
    c._initialized = True
    c.encoder = FakeEncoder()
    return c


@pytest.fixture
def fake_audio(monkeypatch):
    """把 resemblyzer / soundfile / librosa 换成确定性的假实现"""

    def _install(samples=16000, sr=16000, wav=None):
        wav_data = np.zeros(samples, dtype=np.float32) if wav is None else wav
        sf = types.ModuleType("soundfile")
        sf.read = lambda path, dtype=None: (wav_data, sr)
        resemblyzer = types.ModuleType("resemblyzer")
        resemblyzer.preprocess_wav = lambda w, source_sr=None: wav_data
        librosa = types.ModuleType("librosa")
        librosa.resample = lambda w, orig_sr=None, target_sr=None: wav_data
        monkeypatch.setitem(sys.modules, "soundfile", sf)
        monkeypatch.setitem(sys.modules, "resemblyzer", resemblyzer)
        monkeypatch.setitem(sys.modules, "librosa", librosa)

    return _install


# ── 初始化 ──

async def test_initialize_loads_encoder_and_profiles(monkeypatch, tmp_path):
    fake = types.ModuleType("resemblyzer")
    fake.VoiceEncoder = FakeEncoder
    monkeypatch.setitem(sys.modules, "resemblyzer", fake)

    fresh = SpeakerClassifier(data_dir=str(tmp_path / "p2"))
    await fresh.initialize()
    assert fresh._initialized is True
    assert isinstance(fresh.encoder, FakeEncoder)

    await fresh.initialize()          # 幂等，不重复加载
    assert isinstance(fresh.encoder, FakeEncoder)


async def test_initialize_without_resemblyzer_explains_install(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "resemblyzer", None)
    c = SpeakerClassifier(data_dir=str(tmp_path))
    with pytest.raises(ImportError) as ei:
        await c.initialize()
    assert "pip install resemblyzer" in str(ei.value)


# ── 档案持久化 ──

async def test_save_and_reload_profiles(clf, tmp_path):
    clf._speakers = {"alice": _emb(1)}
    clf._speaker_meta = {"alice": {"name": "alice", "embedding_dim": DIM}}
    clf._save_profiles()

    reloaded = SpeakerClassifier(data_dir=clf.data_dir)
    reloaded._load_profiles()
    assert list(reloaded._speakers) == ["alice"]
    assert reloaded._speaker_meta["alice"]["embedding_dim"] == DIM


async def test_save_without_speakers_removes_npz(clf):
    clf._speakers = {"alice": _emb(1)}
    clf._save_profiles()
    assert (clf.data_dir and __import__("os").path.exists(clf._embeddings_path()))

    clf._speakers = {}
    clf._save_profiles()
    assert not __import__("os").path.exists(clf._embeddings_path())


async def test_corrupt_profiles_fall_back_to_empty(clf):
    import os

    os.makedirs(clf.data_dir, exist_ok=True)
    with open(clf._profiles_path(), "w", encoding="utf-8") as f:
        f.write("{not json")
    with open(clf._embeddings_path(), "wb") as f:
        f.write(b"not a npz")

    clf._load_profiles()
    assert clf._speaker_meta == {}
    assert clf._speakers == {}


async def test_save_failure_is_logged_not_raised(clf, monkeypatch):
    clf._speakers = {"alice": _emb(1)}

    def boom(**kw):
        raise OSError("disk full")

    monkeypatch.setattr(sc_mod.np, "savez", boom)
    clf._save_profiles()             # 不抛异常
    assert clf._speaker_meta == {}


# ── 嵌入提取 ──

async def test_extract_returns_none_when_encoder_missing(clf):
    clf.encoder = None
    assert await clf.extract_embedding(b"RIFF") is None


async def test_extract_returns_none_when_dependency_missing(clf, monkeypatch):
    monkeypatch.setitem(sys.modules, "resemblyzer", None)
    assert await clf.extract_embedding(b"RIFF") is None


async def test_extract_embedding_happy_path(clf, fake_audio):
    fake_audio(samples=16000)
    emb = await clf.extract_embedding(b"RIFF")
    assert emb.shape == (DIM,)
    assert clf.encoder.calls == 1


async def test_extract_downmixes_stereo_and_resamples(clf, fake_audio):
    stereo = np.zeros((16000, 2), dtype=np.float32)
    fake_audio(samples=0, sr=8000, wav=stereo)
    assert await clf.extract_embedding(b"RIFF") is not None


async def test_short_audio_is_rejected(clf, fake_audio):
    fake_audio(samples=100)          # < 0.3s
    assert await clf.extract_embedding(b"RIFF") is None
    assert clf.encoder.calls == 0


async def test_extract_initializes_lazily(fake_audio, tmp_path, monkeypatch):
    fake_audio(samples=16000)
    sys.modules["resemblyzer"].VoiceEncoder = FakeEncoder

    c = SpeakerClassifier(data_dir=str(tmp_path / "lazy"))
    assert c._initialized is False
    assert await c.extract_embedding(b"RIFF") is not None
    assert c._initialized is True


# ── 识别 / 注册 ──

async def test_identify_without_registered_speakers(clf):
    emb = _emb(3)
    clf.extract_embedding = lambda *a, **kw: _ret(emb)
    r = await clf.identify_speaker(b"RIFF")
    assert r["identified"] is False
    assert r["speaker"] is None
    assert r["embedding"] == emb.tolist()


async def test_identify_returns_none_without_embedding(clf):
    clf.extract_embedding = lambda *a, **kw: _ret(None)
    assert await clf.identify_speaker(b"") is None


async def test_identify_matches_best_speaker(clf):
    emb = _emb(5)
    clf._speakers = {"alice": emb / np.linalg.norm(emb), "bob": _emb(9) / np.linalg.norm(_emb(9))}
    clf.extract_embedding = lambda *a, **kw: _ret(emb)

    r = await clf.identify_speaker(b"RIFF")
    assert r["identified"] is True
    assert r["speaker"] == "alice"
    assert r["similarity"] == pytest.approx(1.0, abs=1e-3)


async def test_identify_below_threshold_is_unidentified(clf):
    emb = _emb(5)
    clf._speakers = {"bob": _emb(9) / np.linalg.norm(_emb(9))}
    clf.extract_embedding = lambda *a, **kw: _ret(emb)

    r = await clf.identify_speaker(b"RIFF", threshold=1.01)
    assert r["identified"] is False
    assert r["speaker"] is None
    assert r["threshold"] == 1.01


async def test_register_speaker_persists_normalized_embedding(clf):
    clf.extract_embedding = lambda *a, **kw: _ret(_emb(7))
    r = await clf.register_speaker("alice", b"RIFF")
    assert r == {"success": True, "speaker": "alice", "embedding_dim": DIM}
    assert np.linalg.norm(clf._speakers["alice"]) == pytest.approx(1.0)
    assert clf._speaker_meta["alice"]["name"] == "alice"


async def test_register_speaker_rejects_bad_audio(clf):
    clf.extract_embedding = lambda *a, **kw: _ret(None)
    r = await clf.register_speaker("alice", b"")
    assert r["success"] is False
    assert "声纹提取失败" in r["error"]
    assert clf._speakers == {}


async def test_register_from_embedding(clf):
    r = await clf.register_speaker_from_embedding("bob", [3.0, 4.0])
    assert r == {"success": True, "speaker": "bob"}
    assert clf._speaker_meta["bob"]["embedding_dim"] == 2
    assert np.linalg.norm(clf._speakers["bob"]) == pytest.approx(1.0)


async def test_list_speakers_includes_dimensions(clf):
    clf._speaker_meta = {
        "alice": {"name": "alice", "embedding_dim": 4, "registered_at": "2026"},
        "bob": {},
    }
    out = await clf.list_speakers()
    by_name = {s["name"]: s for s in out}
    assert by_name["alice"]["embedding_dim"] == 4
    assert by_name["bob"]["embedding_dim"] == sc_mod.DEFAULT_EMBEDDING_DIM


async def test_delete_speaker(clf):
    clf._speakers = {"alice": _emb(1)}
    clf._speaker_meta = {"alice": {"name": "alice"}}
    assert (await clf.delete_speaker("alice"))["success"] is True
    assert clf._speakers == {} and clf._speaker_meta == {}

    missing = await clf.delete_speaker("ghost")
    assert missing["success"] is False
    assert "不存在" in missing["error"]


async def test_compare_embeddings_scores_cosine(clf):
    v = _emb(2)
    assert await clf.compare_embeddings(v.tolist(), v.tolist()) == pytest.approx(1.0, abs=1e-3)
    assert await clf.compare_embeddings([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-3)


# ── 默认单例 ──

async def test_default_classifier_is_cached_and_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(sc_mod, "_default_classifier", None)
    calls = []

    async def fake_init(self):
        calls.append(self.data_dir)
        self._initialized = True

    monkeypatch.setattr(SpeakerClassifier, "initialize", fake_init)
    first = await get_default_classifier()
    second = await get_default_classifier()
    assert first is second
    assert calls == [first.data_dir]
    assert first.data_dir.endswith("speaker_profiles")


def _ret(value):
    async def _awaitable(*a, **kw):
        return value

    return _awaitable()


async def test_profiles_paths_are_inside_data_dir(clf):
    assert clf._profiles_path() == __import__("os").path.join(clf.data_dir, "speaker_profiles.json")
    assert clf._embeddings_path() == __import__("os").path.join(clf.data_dir, "speaker_embeddings.npz")


def test_metadata_file_is_human_readable_json(clf):
    clf._speakers = {"alice": _emb(1)}
    clf._speaker_meta = {"alice": {"name": "alice", "embedding_dim": DIM}}
    clf._save_profiles()
    with open(clf._profiles_path(), encoding="utf-8") as f:
        assert json.load(f)["alice"]["name"] == "alice"
