"""
infra/data_process/api.py 单测 — FastAPI router（信息处理 API）

策略：
- 独立 FastAPI app 挂载 router，避免 api.main 的 lifespan/中间件。
- 认证依赖 require_api_key 读 settings.SIMPLE_API_KEY → patch 单例。
- 重型视觉/语音模型（get_default_recognizer / get_default_analyzer）全 mock。
"""
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import settings
from api.errors import AppError, ErrorCode

AUTH = {"X-API-Key": "test-secret"}
AUDIO_MIME = "audio/wav"
PNG_MIME = "image/png"
AUDIO_BYTES = b"\x00\x01\x02fake-audio"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image"
PNG_RENDERED = b"\x89PNG\r\n\x1a\nrendered"


@pytest.fixture
def data_process_mocks(monkeypatch):
    monkeypatch.setattr(settings, "SIMPLE_API_KEY", "test-secret")

    recognizer = AsyncMock()
    recognizer.recognize = AsyncMock(return_value={"text": "你好", "language": "zh", "confidence": 0.95})
    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value={"description": "图像描述", "objects": []})
    analyzer.analyze_base64 = AsyncMock(return_value={"description": "b64 描述"})
    analyzer.detect_ui_elements = AsyncMock(return_value={"elements": [{"type": "button"}]})
    analyzer.analyze_with_coordinates = AsyncMock(return_value={"answer": "按钮在 (100,200)"})
    analyzer.draw_elements = MagicMock(return_value=PNG_RENDERED)

    monkeypatch.setattr("infra.data_process.api.get_default_recognizer", AsyncMock(return_value=recognizer))
    monkeypatch.setattr("infra.data_process.api.get_default_analyzer", AsyncMock(return_value=analyzer))
    return {"recognizer": recognizer, "analyzer": analyzer}


@pytest.fixture
def client(data_process_mocks):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from infra.data_process.api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# _validate_upload
# ---------------------------------------------------------------------------

class _FakeUploadFile:
    def __init__(self, content_type, size=None):
        self.content_type = content_type
        self.size = size


class TestValidateUpload:
    def test_valid_mime_passes(self):
        from infra.data_process.api import _validate_upload, ALLOWED_AUDIO_MIME
        _validate_upload(_FakeUploadFile(AUDIO_MIME, 10), ALLOWED_AUDIO_MIME)

    def test_none_content_type_passes(self):
        from infra.data_process.api import _validate_upload, ALLOWED_AUDIO_MIME
        _validate_upload(_FakeUploadFile(None, None), ALLOWED_AUDIO_MIME)

    def test_octet_stream_passes(self):
        from infra.data_process.api import _validate_upload, ALLOWED_AUDIO_MIME
        _validate_upload(_FakeUploadFile("application/octet-stream", 10), ALLOWED_AUDIO_MIME)

    def test_unsupported_mime_raises_415(self):
        from infra.data_process.api import _validate_upload, ALLOWED_AUDIO_MIME
        with pytest.raises(AppError) as exc:
            _validate_upload(_FakeUploadFile("text/plain", 10), ALLOWED_AUDIO_MIME)
        assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA_TYPE
        assert exc.value.status_code == 415
        assert "text/plain" in exc.value.message

    def test_too_large_raises_413(self):
        from infra.data_process.api import _validate_upload, ALLOWED_AUDIO_MIME
        with pytest.raises(AppError) as exc:
            _validate_upload(_FakeUploadFile(AUDIO_MIME, 100), ALLOWED_AUDIO_MIME, max_size=50)
        assert exc.value.code == ErrorCode.PAYLOAD_TOO_LARGE
        assert exc.value.status_code == 413

    def test_size_none_skips_size_check(self):
        from infra.data_process.api import _validate_upload, ALLOWED_AUDIO_MIME
        _validate_upload(_FakeUploadFile(AUDIO_MIME, None), ALLOWED_AUDIO_MIME, max_size=1)

    def test_at_limit_passes(self):
        from infra.data_process.api import _validate_upload, ALLOWED_AUDIO_MIME
        _validate_upload(_FakeUploadFile(AUDIO_MIME, 50), ALLOWED_AUDIO_MIME, max_size=50)


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------

class TestAuth:
    def test_missing_key_401(self, client):
        resp = client.get("/data-process/status")
        assert resp.status_code == 401

    def test_wrong_key_401(self, client):
        resp = client.get("/data-process/status", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_status_with_key(self, client):
        resp = client.get("/data-process/status", headers=AUTH)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /speech/recognize
# ---------------------------------------------------------------------------

class TestRecognizeSpeech:
    def test_success_auto_language(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/speech/recognize",
            files={"audio_file": ("a.wav", AUDIO_BYTES, AUDIO_MIME)},
            data={"language": "auto", "task": "transcribe"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["text"] == "你好"
        data_process_mocks["recognizer"].recognize.assert_awaited_once_with(
            AUDIO_BYTES, language=None, task="transcribe"
        )

    def test_success_specific_language(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/speech/recognize",
            files={"audio_file": ("a.wav", AUDIO_BYTES, AUDIO_MIME)},
            data={"language": "zh", "task": "translate"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data_process_mocks["recognizer"].recognize.assert_awaited_once_with(AUDIO_BYTES, language="zh", task="translate")

    def test_recognize_error_500(self, client, data_process_mocks):
        data_process_mocks["recognizer"].recognize = AsyncMock(side_effect=RuntimeError("stt down"))
        resp = client.post(
            "/data-process/speech/recognize",
            files={"audio_file": ("a.wav", AUDIO_BYTES, AUDIO_MIME)},
            headers=AUTH,
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /speech/recognize-base64
# ---------------------------------------------------------------------------

class TestRecognizeSpeechBase64:
    def test_success(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/speech/recognize-base64",
            data={"audio": base64.b64encode(AUDIO_BYTES).decode(), "language": "auto"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        data_process_mocks["recognizer"].recognize.assert_awaited_once_with(AUDIO_BYTES, "auto")
    def test_invalid_base64_500(self, client):
        resp = client.post(
            "/data-process/speech/recognize-base64",
            data={"audio": "!!!not-base64!!!", "language": "auto"},
            headers=AUTH,
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /image/analyze
# ---------------------------------------------------------------------------

class TestAnalyzeImage:
    def test_success(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/image/analyze",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            data={"prompt": "描述一下"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["description"] == "图像描述"
        data_process_mocks["analyzer"].analyze.assert_awaited_once_with(PNG_BYTES, "描述一下")

    def test_default_prompt(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/image/analyze",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data_process_mocks["analyzer"].analyze.assert_awaited_once()
        assert data_process_mocks["analyzer"].analyze.await_args.args[1]

    def test_error_500_json_response(self, client, data_process_mocks):
        data_process_mocks["analyzer"].analyze = AsyncMock(side_effect=ValueError("vision down"))
        resp = client.post(
            "/data-process/image/analyze",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            headers=AUTH,
        )
        assert resp.status_code == 500
        assert resp.json()["success"] is False


# ---------------------------------------------------------------------------
# /image/analyze-base64
# ---------------------------------------------------------------------------

class TestAnalyzeImageBase64:
    def test_success(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/image/analyze-base64",
            data={"image": base64.b64encode(PNG_BYTES).decode(), "prompt": "看下"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        data_process_mocks["analyzer"].analyze_base64.assert_awaited_once()

    def test_error_500(self, client, data_process_mocks):
        data_process_mocks["analyzer"].analyze_base64 = AsyncMock(side_effect=RuntimeError("boom"))
        resp = client.post(
            "/data-process/image/analyze-base64",
            data={"image": base64.b64encode(PNG_BYTES).decode()},
            headers=AUTH,
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_healthy(self, client):
        resp = client.get("/data-process/status", headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["module"] == "info_process"
        assert data["status"] == "healthy"
        assert data["capabilities"]["speech_recognition"]["models"]
        assert data["capabilities"]["image_analysis"]["models"]


# ---------------------------------------------------------------------------
# /image/detect-ui
# ---------------------------------------------------------------------------

class TestDetectUI:
    def test_success_without_types(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/image/detect-ui",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        data_process_mocks["analyzer"].detect_ui_elements.assert_awaited_once_with(PNG_BYTES, None)

    def test_success_with_types(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/image/detect-ui",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            data={"element_types": "button,input,icon"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data_process_mocks["analyzer"].detect_ui_elements.assert_awaited_once_with(PNG_BYTES, ["button", "input", "icon"])

    def test_error_500(self, client, data_process_mocks):
        data_process_mocks["analyzer"].detect_ui_elements = AsyncMock(side_effect=RuntimeError("boom"))
        resp = client.post(
            "/data-process/image/detect-ui",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            headers=AUTH,
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /image/analyze-query
# ---------------------------------------------------------------------------

class TestAnalyzeQuery:
    def test_success(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/image/analyze-query",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            data={"query": "按钮在哪"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["answer"] == "按钮在 (100,200)"
        data_process_mocks["analyzer"].analyze_with_coordinates.assert_awaited_once_with(PNG_BYTES, "按钮在哪")

    def test_default_query(self, client, data_process_mocks):
        resp = client.post(
            "/data-process/image/analyze-query",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            headers=AUTH,
        )
        assert resp.status_code == 200

    def test_error_500(self, client, data_process_mocks):
        data_process_mocks["analyzer"].analyze_with_coordinates = AsyncMock(side_effect=RuntimeError("boom"))
        resp = client.post(
            "/data-process/image/analyze-query",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            headers=AUTH,
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /image/draw-elements
# ---------------------------------------------------------------------------

class TestDrawElements:
    def test_success(self, client, data_process_mocks):
        elements = [{"type": "button", "text": "提交", "bounds": {"x": 1, "y": 2, "width": 3, "height": 4}}]
        resp = client.post(
            "/data-process/image/draw-elements",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            data={"element_data": json.dumps(elements)},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == PNG_RENDERED
        data_process_mocks["analyzer"].draw_elements.assert_called_once_with(PNG_BYTES, elements)

    def test_invalid_json_500(self, client):
        resp = client.post(
            "/data-process/image/draw-elements",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            data={"element_data": "{not json"},
            headers=AUTH,
        )
        assert resp.status_code == 500

    def test_draw_error_500(self, client, data_process_mocks):
        data_process_mocks["analyzer"].draw_elements = MagicMock(side_effect=RuntimeError("boom"))
        resp = client.post(
            "/data-process/image/draw-elements",
            files={"image_file": ("i.png", PNG_BYTES, PNG_MIME)},
            data={"element_data": "[]"},
            headers=AUTH,
        )
        assert resp.status_code == 500
