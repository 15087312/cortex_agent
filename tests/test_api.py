"""
Comprehensive tests for the API layer.

Covers:
  - Root endpoint (GET /)
  - Health endpoint (GET /health)
  - Metrics endpoint (GET /metrics)
  - Authentication middleware (X-API-Key)
  - Rate limiting
  - CORS headers
  - Error handling (404, validation)
  - Request-ID and process-time headers
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def _mock_lifespan():
    """Replace the app lifespan so tests do not initialise heavy subsystems."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    with patch("api.main.app.router.lifespan_context", _noop_lifespan):
        yield


@pytest.fixture
def _auth_key():
    """Set a known SIMPLE_API_KEY for tests that need auth."""
    with patch("api.main._SIMPLE_API_KEY", "test-secret-key"):
        yield


@pytest.fixture
def _no_auth():
    """Disable auth (empty key) for tests that should skip authentication."""
    with patch("api.main._SIMPLE_API_KEY", ""):
        yield


@pytest.fixture
def _reset_rate_limit():
    """Clear the rate-limit counter between tests."""
    import api.main
    api.main.request_counts.clear()
    api.main._request_counter_ref[0] = 0
    yield
    api.main.request_counts.clear()
    api.main._request_counter_ref[0] = 0


def _client(app, **kwargs):
    """Helper to build an AsyncClient against the ASGI app."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", **kwargs)


# ---------------------------------------------------------------------------
# Root endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_root_returns_app_info(_mock_lifespan, _no_auth, _reset_rate_limit):
    from api.main import app

    async with _client(app) as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["name"] == "Humanoid AGI"
    assert data["data"]["version"] == "2.0.0"
    assert data["data"]["status"] == "running"


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_returns_status(_mock_lifespan, _no_auth, _reset_rate_limit):
    """Health endpoint should return success with a status field and checks dict."""
    with patch("modules.thinking.core.model_manager.model_manager") as mock_mm, \
         patch("modules.database.connection.db_manager") as mock_db:
        mock_mm.is_initialized = True
        mock_db.get_session.return_value = MagicMock()

        from api.main import app
        async with _client(app) as client:
            resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "status" in data["data"]
    assert "checks" in data["data"]


@pytest.mark.asyncio
async def test_health_degraded_when_subsystem_unavailable(_mock_lifespan, _no_auth, _reset_rate_limit):
    """When a subsystem raises, health should report 'degraded'."""
    # MagicMock side_effect does NOT fire on attribute access — only on call.
    # The health endpoint does: `from ... import model_manager` then accesses
    # `model_manager.is_initialized`.  We must configure the mock objects so
    # the code paths that set ``all_healthy = False`` are actually hit.
    mock_mm = MagicMock()
    mock_mm.is_initialized = False          # → "not_initialized"

    mock_db = MagicMock()
    mock_db.get_session.side_effect = RuntimeError("db down")  # → "unavailable"

    with patch("modules.thinking.core.model_manager.model_manager", mock_mm), \
         patch("modules.database.connection.db_manager", mock_db):

        from api.main import app
        async with _client(app) as client:
            resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["status"] == "degraded"
    assert data["data"]["checks"]["model_manager"] == "not_initialized"
    assert data["data"]["checks"]["database"] == "unavailable"


@pytest.mark.asyncio
async def test_health_bypasses_auth(_mock_lifespan, _auth_key, _reset_rate_limit):
    """Health endpoint must be accessible without an API key (whitelisted)."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metrics_returns_prometheus_format(_mock_lifespan, _no_auth, _reset_rate_limit):
    """GET /metrics should return text/plain Prometheus exposition format."""
    fake_metrics = "# TYPE test_counter gauge\ntest_counter 42\n"
    with patch("modules.metrics.collector.MetricsExporter.to_prometheus", return_value=fake_metrics):
        from api.main import app
        async with _client(app) as client:
            resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "test_counter 42" in resp.text


@pytest.mark.asyncio
async def test_metrics_empty(_mock_lifespan, _no_auth, _reset_rate_limit):
    """GET /metrics should handle an empty metric set gracefully."""
    with patch("modules.metrics.collector.MetricsExporter.to_prometheus", return_value=""):
        from api.main import app
        async with _client(app) as client:
            resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert resp.text == ""


# ---------------------------------------------------------------------------
# Authentication middleware
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_missing_key_returns_401(_mock_lifespan, _auth_key, _reset_rate_limit):
    """Request without X-API-Key header to a protected path should return 401."""
    from api.main import app

    # /config/{key} is PUT-only and is behind auth — no key → 401
    async with _client(app) as client:
        resp = await client.put("/config/DEBUG", json={"value": True})

    assert resp.status_code == 401
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_auth_wrong_key_returns_401(_mock_lifespan, _auth_key, _reset_rate_limit):
    """Request with an incorrect X-API-Key should return 401."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.put(
            "/config/DEBUG",
            json={"value": True},
            headers={"X-API-Key": "wrong-key"},
        )

    assert resp.status_code == 401
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_auth_correct_key_returns_200(_mock_lifespan, _auth_key, _reset_rate_limit):
    """Request with the correct X-API-Key should pass auth."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.put(
            "/config/DEBUG",
            json={"value": True},
            headers={"X-API-Key": "test-secret-key"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_auth_disabled_when_key_empty(_mock_lifespan, _no_auth, _reset_rate_limit):
    """When SIMPLE_API_KEY is empty (dev mode), auth should be skipped entirely."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.put("/config/DEBUG", json={"value": True})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_root_bypasses_auth(_mock_lifespan, _auth_key, _reset_rate_limit):
    """Root '/' is in the whitelist and must be accessible without a key."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429(_mock_lifespan, _no_auth, _reset_rate_limit):
    """When the per-minute request count exceeds 100, the server should return 429."""
    import api.main
    import time

    # ASGI test transport reports request.client.host as "127.0.0.1"
    current_minute = int(time.time() / 60)
    key = f"127.0.0.1|{current_minute}"
    api.main.request_counts[key] = 100  # already at the limit

    from api.main import app
    async with _client(app) as client:
        resp = await client.get("/")

    assert resp.status_code == 429
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_rate_limit_within_limit_returns_200(_mock_lifespan, _no_auth, _reset_rate_limit):
    """Requests within the 100/min limit should succeed."""
    from api.main import app

    # Make a few normal requests — well under the limit
    async with _client(app) as client:
        for _ in range(5):
            resp = await client.get("/")

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cors_options_returns_allowed_headers(_mock_lifespan, _no_auth, _reset_rate_limit):
    """An OPTIONS preflight request should include CORS allow-headers."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.options(
            "/",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    # Starlette CORSMiddleware sets these headers on preflight
    assert resp.status_code == 200
    allow_headers = resp.headers.get("access-control-allow-headers", "")
    assert "Content-Type" in allow_headers or "X-API-Key" in allow_headers


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_404_returns_error_response(_mock_lifespan, _no_auth, _reset_rate_limit):
    """An unknown path should return a 404 error response.

    Note: Starlette handles route-not-found at the ASGI level *before*
    FastAPI's exception handlers run, so the response uses the default
    FastAPI format ``{"detail": "Not Found"}`` rather than the custom
    ``error_response`` envelope.
    """
    from api.main import app

    async with _client(app) as client:
        resp = await client.get("/this/path/does/not/exist")

    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data or ("success" in data and data["success"] is False)


@pytest.mark.asyncio
async def test_http_exception_returns_structured_error(_mock_lifespan, _no_auth, _reset_rate_limit):
    """An explicitly raised HTTPException should be converted to the structured format."""
    from api.main import app
    from fastapi import HTTPException

    # Add a temporary route that raises HTTPException(404)
    @app.get("/__test_http_404__")
    async def _test_route():
        raise HTTPException(status_code=404, detail="resource gone")

    try:
        async with _client(app) as client:
            resp = await client.get("/__test_http_404__")

        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["code"] == "NOT_FOUND"
        assert data["error"]["message"] == "resource gone"
    finally:
        # Remove the temporary route so it doesn't leak into other tests
        app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/__test_http_404__"]


@pytest.mark.asyncio
async def test_validation_error_returns_structured_response(_mock_lifespan, _no_auth, _reset_rate_limit):
    """A request that fails Pydantic validation should return 422 with structured detail."""
    from api.main import app

    # PUT /config/{key} expects {"value": <str|int|float|bool>}.
    # Sending an empty body triggers a RequestValidationError.
    async with _client(app) as client:
        resp = await client.put(
            "/config/DEBUG",
            content="",
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 422
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(data["error"]["message"], str)
    assert len(data["error"]["message"]) > 0


@pytest.mark.asyncio
async def test_validation_error_wrong_type(_mock_lifespan, _no_auth, _reset_rate_limit):
    """Sending a list where a scalar is expected should trigger validation error."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.put(
            "/config/DEBUG",
            json={"value": [1, 2, 3]},  # list is not str|int|float|bool
        )

    assert resp.status_code == 422
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Request-ID and Process-Time headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_response_has_request_id_header(_mock_lifespan, _no_auth, _reset_rate_limit):
    """Every response should carry an X-Request-ID header."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.get("/")

    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) > 0


@pytest.mark.asyncio
async def test_response_has_process_time_header(_mock_lifespan, _no_auth, _reset_rate_limit):
    """Every response should carry an X-Process-Time header."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.get("/")

    assert "X-Process-Time" in resp.headers
    process_time = float(resp.headers["X-Process-Time"])
    assert process_time >= 0


# ---------------------------------------------------------------------------
# Config endpoint (integration with auth + validation)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_update_forbidden_key(_mock_lifespan, _no_auth, _reset_rate_limit):
    """Updating a key not in the allow-list should return 403."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.put(
            "/config/SUPER_SECRET_SETTING",
            json={"value": "new-value"},
        )

    assert resp.status_code == 403
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_config_update_allowed_key(_mock_lifespan, _no_auth, _reset_rate_limit):
    """Updating a key in the allow-list should succeed."""
    from api.main import app

    async with _client(app) as client:
        resp = await client.put(
            "/config/DEBUG",
            json={"value": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["key"] == "DEBUG"
    assert data["data"]["new_value"] is True
