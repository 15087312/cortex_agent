"""
FastAPI 主入口 - 挂载所有模块的路由、全局中间件
"""
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from contextlib import asynccontextmanager
import os
import time
import uuid
import hmac
import asyncio

from api.errors import (
    ErrorCode, ErrorResponse, ErrorDetail, error_response, AppError,
)
from modules.management import report_exception
from cortex.version import __version__ as _CORTEX_VERSION
from infra.data_process.api import router as data_process_router
from infra.tool_manager.api import router as tool_router
from modules.thinking.api_stream import router as stream_router
from modules.attention.api import router as attention_router
from modules.management.api import router as management_router
from modules.output_system.api import router as output_router
from modules.security_system.api import router as security_router
from config.settings import settings

# 条件导入差异检测器路由
if settings.DIFFERENCE_DETECTOR_ENABLED:
    from modules.perception.difference.api import router as difference_router
from utils.logger import setup_logger

logger = setup_logger("api_main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting Humanoid AGI...")

    # SEC-2: 生产环境必须配置 API Key
    if settings.APP_ENV == "production" and not _SIMPLE_API_KEY:
        logger.error("FATAL: API Key not configured in production mode. Set SIMPLE_API_KEY environment variable.")
        raise RuntimeError("API Key must be configured in production mode")

    # 校验生产环境关键配置
    settings.validate_production()

    # 初始化 ~/.cordex/ 本地存储目录
    try:
        from pathlib import Path
        base = Path.home() / ".cordex"
        for sub in ("debug", "skills", "plans", "todos", "edits", "memories", "projects"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        logger.info("✓ ~/.cordex/ 本地存储目录已就绪")
    except Exception as e:
        logger.warning(f"~/.cordex/ 目录初始化失败 (非致命): {e}")

    # 检测屏幕录制权限（一次检测，全局生效）
    try:
        from utils.screen_capture import init_screen_permission
        init_screen_permission()
    except Exception as e:
        logger.debug(f"屏幕权限检测跳过: {e}")

    # 预加载 Embedding 模型（阻塞启动，加载失败则启动失败）
    import sys
    from modules.memory.embedding import EmbeddingEngine
    eng = EmbeddingEngine.get_instance()
    print("[DEBUG] 开始加载 Embedding 模型...", flush=True)
    if not eng._load_model():
        raise RuntimeError("Embedding 模型加载失败，无法启动（请检查网络或代理，模型自动下载需要访问 huggingface.co）")
    print(f"[DEBUG] Embedding 模型加载完成，dim={eng.dim}", flush=True)
    logger.info(f"✓ Embedding 模型已预加载 (dim={eng.dim})")
    print("[DEBUG] 继续后续初始化...", flush=True)

    # 预加载 MLX-VLM 视觉模型（后台加载，不阻塞启动）
    if settings.VISION_BACKEND in ("mlx", "auto"):
        import threading
        def _preload_vision():
            try:
                from utils.logger import setup_logger
                vision_logger = setup_logger("vision_preload")
                vision_logger.info("开始预加载 MLX-VLM 视觉模型...")
                from infra.data_process.core.image_analyzer import ImageAnalyzer
                analyzer = ImageAnalyzer(model_type="auto")
                # 同步加载（在后台线程中）
                import asyncio
                loop = asyncio.new_event_loop()
                loop.run_until_complete(analyzer.initialize())
                loop.close()
                vision_logger.info(f"✓ MLX-VLM 视觉模型预加载完成 (type={analyzer.model_type})")
            except Exception as e:
                from utils.logger import setup_logger
                vision_logger = setup_logger("vision_preload")
                vision_logger.warning(f"MLX-VLM 预加载失败 (非致命): {e}")

        threading.Thread(target=_preload_vision, daemon=True, name="vision-preload").start()
        logger.info("✓ 视觉模型预加载已启动（后台）")

    # 初始化模型调度管理器
    try:
        from modules.thinking.model_factory import get_model_factory
        get_model_factory().ensure_ready()
        print("[DEBUG] 模型实例工厂就绪", flush=True)
        logger.info("✓ 模型实例工厂已就绪")
    except Exception as e:
        logger.error(f"✗ 模型调度管理器初始化失败: {e}")

    # 初始化流式思考系统
    try:
        from modules.thinking.api_stream import initialize_system
        print("[DEBUG] 开始初始化流式思考系统...", flush=True)
        await initialize_system()
        print("[DEBUG] 流式思考系统就绪", flush=True)
        logger.info("✓ 流式思考系统已初始化")
    except Exception as e:
        logger.error(f"✗ 流式思考系统初始化失败: {e}")

    # 初始化全局错误总线的asyncio处理器
    try:
        from modules.management.core.error_bus import error_bus
        loop = asyncio.get_running_loop()
        error_bus.setup_asyncio_handler(loop)
        logger.info("✓ 全局错误总线已初始化")
    except Exception as e:
        logger.error(f"✗ 全局错误总线初始化失败: {e}")

    # 注册错误总线 WebSocket 回调 — 错误推送到前端 TUI
    try:
        from modules.management.core.error_bus import error_bus
        from modules.thinking.api_stream import connection_manager

        def _on_error(error_type: str, error_msg: str, ctx: dict):
            """错误回调：推送到所有活跃 WebSocket session"""
            from modules.thinking.api_stream import _build_event
            for session_id in list(connection_manager.active_connections.keys()):
                envelope = _build_event(
                    session_id=session_id,
                    msg_type="error",
                    event="system_error",
                    content=error_msg,
                    role="system",
                    data={
                        "error_type": error_type,
                        "error_message": f"[{error_type}] {error_msg}",
                        "module": ctx.get("module", ""),
                        "function": ctx.get("function", ""),
                        "phase": "error",
                    },
                )
                connection_manager.send_json_from_thread(session_id, envelope)
        error_bus.set_ws_callback(_on_error)
        logger.info("✓ 错误总线 WebSocket 回调已注册")
    except Exception as e:
        logger.debug(f"错误总线 WebSocket 回调注册失败 (非致命): {e}")

    # 启动感知系统（统一由 PerceptionSystem 管理：屏幕/文件/对话/语音+主动触发）
    if settings.PERCEPTION_ENABLED:
        try:
            from modules.perception.setup import get_perception_system
            ps = get_perception_system()
            ps.setup()
            ps.start()
            logger.info("✓ 感知系统已启动")

            # 启动感知集成器（订阅事件并注入模型上下文）
            from modules.perception.integration import get_perception_integrator
            get_perception_integrator().start()
        except Exception as e:
            logger.error(f"✗ 感知系统启动失败: {e}")

    # MCP 屏幕差异检测（独立子进程，像素级帧差）
    if settings.SCREEN_DIFF_ENABLED and settings.DIFFERENCE_DETECTOR_ENABLED:
        try:
            from modules.perception.difference.sources.mcp_screen_source import get_screen_diff_source
            src = get_screen_diff_source()
            # 从 settings 读取配置（interval 在运行时可通过 REST API 调整）
            src.interval = settings.SCREEN_DIFF_INTERVAL
            src.start()
            logger.info("✓ MCP 屏幕差异检测已启动 (screen_diff_server)")
        except Exception as e:
            logger.warning(f"MCP 屏幕差异检测启动失败: {e}")

        # 屏幕内容分析（OCR + 视觉，独立于像素差）
        try:
            from modules.perception.difference.sources.screen_monitor_source import get_screen_monitor_source
            get_screen_monitor_source().start()
            logger.info("✓ 屏幕内容分析已启动 (screen_monitor_server)")
        except Exception as e:
            logger.warning(f"屏幕内容分析启动失败: {e}")

    # 启动差异检测器心跳（1Hz 扫描 TimeDifferenceSource — 空闲检测/时间差异）
    if settings.DIFFERENCE_DETECTOR_ENABLED:
        try:
            from modules.perception.difference import get_heartbeat
            get_heartbeat().start()
            logger.info("✓ 差异检测器心跳已启动 (1Hz)")
        except Exception as e:
            logger.warning(f"差异检测器心跳启动失败: {e}")

    # 预加载视觉模型（MLX-VLM），避免首次 tool call 时阻塞事件循环
    if settings.VISION_BACKEND != "mock":
        try:
            from infra.data_process.core.image_analyzer import get_default_analyzer
            await get_default_analyzer()
            logger.info("✓ 视觉模型（ImageAnalyzer）已预加载")
        except Exception as e:
            logger.warning(f"视觉模型预加载失败（首次调用时会重试）: {e}")

    yield
    logger.info("Shutting down Humanoid AGI...")

    # 关闭模型调度管理器
    try:
        from modules.thinking.model_factory import get_model_factory
        get_model_factory().shutdown()
        logger.info("✓ 模型实例已关闭")
    except Exception as e:
        logger.debug(f"模型调度管理器关闭失败 (非致命): {e}")

    # 停止 MCP 屏幕差异检测
    if settings.SCREEN_DIFF_ENABLED and settings.DIFFERENCE_DETECTOR_ENABLED:
        try:
            from modules.perception.difference.sources.mcp_screen_source import get_screen_diff_source
            get_screen_diff_source().stop()
            logger.info("✓ MCP 屏幕差异检测已停止")
        except Exception as e:
            logger.debug(f"MCP 屏幕差异检测停止失败 (非致命): {e}")

    # 停止差异检测器心跳
    if settings.DIFFERENCE_DETECTOR_ENABLED:
        try:
            from modules.perception.difference import get_heartbeat
            get_heartbeat().stop()
            logger.info("✓ 差异检测器心跳已停止")
        except Exception as e:
            logger.debug(f"差异检测器心跳停止失败 (非致命): {e}")

    # 停止感知系统
    if settings.PERCEPTION_ENABLED:
        try:
            from modules.perception.setup import get_perception_system
            ps = get_perception_system()
            ps.stop()
            logger.info("✓ 感知系统已停止")
        except Exception as e:
            logger.debug(f"感知系统停止失败 (非致命): {e}")


    # 关闭数据库连接
    try:
        from modules.database.connection import db_manager
        db_manager.close()
        logger.info("✓ 数据库连接已关闭")
    except Exception as e:
        logger.warning(f"数据库连接关闭失败: {e}")


# ---------------------------------------------------------------------------
# Config API — 安全允许列表（从 Settings._MODIFIABLE_FIELDS 读取）
# ---------------------------------------------------------------------------

_MODIFIABLE_CONFIG_KEYS = settings._MODIFIABLE_FIELDS


app = FastAPI(
    title="Humanoid AGI",
    description="类人智能架构系统 API",
    version=_CORTEX_VERSION,
    lifespan=lifespan
)

# ── Dashboard 静态文件 ──
_DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")

@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/", response_class=HTMLResponse)
async def serve_dashboard():
    """因果图可视化 Dashboard"""
    index_path = os.path.join(_DASHBOARD_DIR, "causal_graph.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)

# SEC-14: HTTPS redirect middleware (production only)
if settings.APP_ENV == "production" and getattr(settings, 'ENABLE_HTTPS_REDIRECT', False):
    @app.middleware("http")
    async def https_redirect(request: Request, call_next):
        if request.url.scheme == "http":
            url = str(request.url).replace("http://", "https://", 1)
            return RedirectResponse(url, status_code=301)
        return await call_next(request)

# SEC-14: CORS 中间件 - 限制允许的源
allowed_cors_origins = [o.strip() for o in settings.ALLOWED_CORS_ORIGINS.split(",") if o.strip()]
# 在生产环境中，确保只配置可信域名
if settings.APP_ENV == "production" and len(allowed_cors_origins) == 0:
    logger.warning("CORS origins not configured in production, using secure defaults")
    allowed_cors_origins = []  # 生产环境默认关闭 CORS

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Request-ID"],
)

# ---------------------------------------------------------------------------
# API Key 认证中间件
# ---------------------------------------------------------------------------

_SIMPLE_API_KEY = settings.SIMPLE_API_KEY
_AUTH_WHITELIST = {
    "/", "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico",
    "/dashboard", "/dashboard/",
    "/stream/sessions", "/stream/sessions/",
    "/config", "/config/",
}
_AUTH_WHITELIST_PREFIXES = ("/management/causal-graph", "/management/memory",
                             "/stream/sessions/", "/config/")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    # 白名单路径跳过
    if request.url.path in _AUTH_WHITELIST or request.url.path.startswith("/docs") or request.url.path.startswith("/redoc") or any(request.url.path.startswith(p) for p in _AUTH_WHITELIST_PREFIXES):
        return await call_next(request)
    # 未配置 API Key 时跳过认证（开发模式）
    if not _SIMPLE_API_KEY:
        logger.warning("API Key not configured, authentication is disabled")
        return await call_next(request)
    # 验证 X-API-Key 头 - 使用 hmac.compare_digest 防止时序攻击
    api_key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(api_key, _SIMPLE_API_KEY):
        return JSONResponse(
            status_code=401,
            content=error_response(ErrorCode.UNAUTHORIZED, "未授权访问").model_dump()
        )
    return await call_next(request)


# 请求 ID 中间件
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:12]
    request.state.request_id = request_id
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    response.headers["X-Request-ID"] = request_id
    return response


# 日志中间件
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    logger.debug(f"{request.method} {request.url.path}")
    response = await call_next(request)
    return response


# 限流中间件（单进程内存计数，--workers 强制为 1）
request_counts: dict = {}
_request_counter_ref: list = [0]
_rate_limit_lock = asyncio.Lock()
_TRUSTED_PROXIES = {"127.0.0.1", "::1"}  # Q-7: Whitelist of trusted reverse proxies (IPv4 + IPv6)
_MAX_RATE_LIMIT_KEYS = 10000  # 防止内存泄漏：最多跟踪的 IP:minute 组合数


# 每处理 500 次请求清理一次过期的分钟 key（key 格式: ip|minute）
def _cleanup_request_counts() -> None:
    current_minute = int(time.time() / 60)
    stale = [k for k in request_counts if isinstance(k, str) and "|" in k]
    for k in stale:
        try:
            _ip, minute = k.split("|", 1)
            if int(minute) < current_minute:
                del request_counts[k]
        except (ValueError, KeyError):
            pass
    # 如果清理后仍然超限，强制清空最旧的一半
    if len(request_counts) > _MAX_RATE_LIMIT_KEYS:
        sorted_keys = sorted(request_counts.keys())
        for k in sorted_keys[:len(sorted_keys) // 2]:
            request_counts.pop(k, None)


def _get_client_ip(request: Request) -> str:
    """Q-7: Extract client IP, considering trusted reverse proxies"""
    # Check if direct connection is from trusted proxy
    if request.client and request.client.host in _TRUSTED_PROXIES:
        forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded_for:
            return forwarded_for
    # Fall back to direct connection
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = _get_client_ip(request)
    current_minute = int(time.time() / 60)

    key = f"{client_ip}|{current_minute}"
    # Q-7: Use async lock to prevent race condition between check and increment
    async with _rate_limit_lock:
        current_count = request_counts.get(key, 0)
        if current_count >= 100:
            logger.warning(f"限流触发: {client_ip} ({request.method} {request.url.path})")
            return JSONResponse(
                status_code=429,
                content=error_response(ErrorCode.RATE_LIMITED, "请求频率超限").model_dump()
            )
        request_counts[key] = current_count + 1

        # 定期清理过期 key（在锁内递增，保证原子性）
        _request_counter_ref[0] += 1
        if _request_counter_ref[0] % 500 == 0:
            _cleanup_request_counts()

    response = await call_next(request)
    return response


# 路由挂载函数（供不同入口复用）
def register_module_routers(app: FastAPI) -> None:
    """挂载所有业务模块路由"""
    app.include_router(data_process_router)
    app.include_router(tool_router)
    # 记忆 API 已迁移，不再挂载旧路由
    app.include_router(stream_router)
    app.include_router(attention_router)
    app.include_router(management_router)
    app.include_router(output_router)
    app.include_router(security_router)
    if settings.DIFFERENCE_DETECTOR_ENABLED:
        app.include_router(difference_router)


# 挂载所有模块的路由
register_module_routers(app)


# ---------------------------------------------------------------------------
# 全局异常处理器 — 统一错误响应格式
# ---------------------------------------------------------------------------

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """处理 AppError → 统一错误格式"""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(exc.code, exc.message).model_dump()
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """将 FastAPI 原生 HTTPException 转换为统一格式"""
    # 映射 HTTP 状态码到 ErrorCode
    code_map = {
        400: ErrorCode.BAD_REQUEST,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        413: ErrorCode.PAYLOAD_TOO_LARGE,
        415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMITED,
        500: ErrorCode.INTERNAL_ERROR,
    }
    code = code_map.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(code, exc.detail if isinstance(exc.detail, str) else "请求错误").model_dump()
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Pydantic 校验错误 → 统一格式"""
    # 提取第一个校验错误的可读描述
    messages = []
    for error in exc.errors():
        loc = ".".join(str(l) for l in error["loc"])
        messages.append(f"{loc}: {error['msg']}")
    detail = "; ".join(messages[:3]) if messages else "请求参数校验失败"
    return JSONResponse(
        status_code=422,
        content=error_response(ErrorCode.VALIDATION_ERROR, detail).model_dump()
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """未预期异常 → 500 通用响应，过滤敏感信息"""
    logger.error(f"[未处理异常] {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    report_exception(
        exc,
        module="api.main",
        function="general_exception_handler",
        context={"method": request.method, "path": request.url.path},
        source="fastapi_exception_handler",
    )
    return JSONResponse(
        status_code=500,
        content=error_response(ErrorCode.INTERNAL_ERROR, "服务内部错误").model_dump()
    )


@app.get("/")
async def root():
    """根路径"""
    return {"success": True, "data": {
        "name": "Humanoid AGI",
        "version": _CORTEX_VERSION,
        "status": "running"
    }}


@app.get("/health")
async def health_check():
    """健康检查 — 验证关键依赖"""
    checks = {}
    all_healthy = True

    # 检查模型管理器
    try:
        from modules.thinking.model_factory import get_model_factory
        checks["model_manager"] = "ok" if get_model_factory().is_ready else "not_initialized"
    except Exception as e:
        logger.debug("健康检查: 模型管理器不可用: %s", e)
        checks["model_manager"] = "unavailable"
        all_healthy = False

    # 检查数据库
    try:
        from modules.database.connection import db_manager
        db_manager.get_session().close()
        checks["database"] = "ok"
    except Exception as e:
        logger.debug("健康检查: 数据库不可用: %s", e)
        checks["database"] = "unavailable"
        all_healthy = False

    status = "healthy" if all_healthy else "degraded"
    return {
        "success": True,
        "data": {"status": status, "checks": checks},
    }


# ---------------------------------------------------------------------------
# Config Update API
# ---------------------------------------------------------------------------

class PutConfigRequest(BaseModel):
    value: str | int | float | bool


@app.get("/config")
async def get_config():
    """读取当前运行时配置（仅返回可修改的配置项）"""
    config_data = {}
    for key in _MODIFIABLE_CONFIG_KEYS:
        val = getattr(settings, key, None)
        if val is not None:
            config_data[key] = val
    return {"data": config_data}


@app.post("/config/toggle-companion-mode")
async def toggle_companion_mode():
    """切换陪伴模式（沿用 API 端点，内部改为激活 companion skill）"""
    from modules.thinking.skills.manager import skill_manager
    companion = skill_manager.get_skill("companion")
    if companion:
        return {"data": {"skill": "companion", "name": companion.name, "message": "Companion skill ready"}}
    return {"data": {"message": "Companion skill not found"}}


@app.put("/config/{key}")
async def update_config(key: str, body: PutConfigRequest):
    """更新运行时配置项（仅限允许列表内的 key）"""
    key_upper = key.upper()
    if key_upper not in _MODIFIABLE_CONFIG_KEYS:
        return JSONResponse(
            status_code=403,
            content=error_response(ErrorCode.FORBIDDEN, f"配置项 '{key}' 不允许通过 API 修改").model_dump()
        )

    # 检查字段是否存在
    field_info = type(settings).model_fields.get(key_upper)
    if field_info is None:
        return JSONResponse(
            status_code=404,
            content=error_response(ErrorCode.NOT_FOUND, f"配置项 '{key_upper}' 不存在").model_dump()
        )

    # 通过 Pydantic 校验新值（触发 field_validator）
    try:
        from pydantic import TypeAdapter
        validated = TypeAdapter(field_info.annotation).validate_python(body.value)
    except Exception as e:
        return JSONResponse(
            status_code=422,
            content=error_response(ErrorCode.VALIDATION_ERROR, f"配置值校验失败: {e}").model_dump()
        )

    try:
        old_value = getattr(settings, key_upper, None)
        object.__setattr__(settings, key_upper, validated)
        logger.info(f"配置已更新: {key_upper} = {validated} (旧值: {old_value})")
        return {
            "success": True,
            "data": {"key": key_upper, "old_value": old_value, "new_value": validated},
        }
    except Exception as e:
        logger.error(f"更新配置失败: {key} -> {e}")
        return JSONResponse(
            status_code=500,
            content=error_response(ErrorCode.INTERNAL_ERROR, f"更新配置失败: {e}").model_dump()
        )


