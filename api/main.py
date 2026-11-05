"""
FastAPI 主入口 - 挂载所有模块的路由、全局中间件
"""
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from contextlib import asynccontextmanager
import os
import sys
import time
import uuid
import hmac
import asyncio
from pathlib import Path
from typing import Optional

from infra.errors import (
    ErrorCode, error_response, AppError,
)
from cortex.version import __version__ as _CORTEX_VERSION
from infra.watchdog import enable as _enable_orphan_watchdog

# 防残留: 若启动本后端的 cortex 父进程被强杀，自动退出避免孤儿进程
try:
    _enable_orphan_watchdog()
except Exception as _swallowed_err:
    # logger 在文件下方才初始化（依赖 config.settings）。这里直接引用它会抛
    # NameError，把真正的 watchdog 故障掩盖掉，所以异常分支必须自足。
    import logging as _logging
    _logging.getLogger("api_main").debug("[module] ignored Exception: %s", _swallowed_err)
from infra.data_process.api import router as data_process_router
from infra.tool_manager.api import router as tool_router
from modules.thinking.chat_gateway import router as stream_router
from modules.desktop_pet.api import router as desktop_pet_router
from modules.management.api import router as management_router
from modules.output_system.api import router as output_router
from modules.output_system.tts import get_tts_output_dir as _get_tts_dir
from modules.security_system.api import router as security_router
from config.settings import settings

# 装配：向 infra 能力端口注册业务服务（消除 infra → modules 逆向依赖）
from bootstrap import register_business_capabilities
register_business_capabilities()

# 条件导入差异检测器路由
if settings.DIFFERENCE_DETECTOR_ENABLED:
    from modules.perception.difference.api import router as difference_router
from utils.logger import setup_logger

logger = setup_logger("api_main")


@asynccontextmanager
# @link:block App
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting Humanoid AGI...")

    # 进程锚定: 把自身 PID 写入发现文件，所有卫星进程（前端代理/Qt/桌宠/
    # 截图 daemon/MCP server）通过它绑定后端；后端退出时全体随之退出。
    try:
        from infra.watchdog import write_backend_pid_file
        write_backend_pid_file(os.getpid())
    except Exception as _swallowed_err:
        logger.debug(f"[lifespan] ignored Exception: {_swallowed_err}")

    # SEC-2: 生产环境必须配置 API Key
    # @link:block App_Guard = Guard
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

    # 启动时应用当前记忆库（含同步 backend 纯对话链路的记忆路径）。
    # 主 settings 构造期因循环 import 跳过了 backend 同步，此处补上。
    try:
        settings._apply_current_memory_lib()
    except Exception as e:
        logger.debug(f"应用当前记忆库失败: {e}")

    # 预加载 Embedding 模型（阻塞启动，加载失败则启动失败）
    # @link:block App_Retrieve = Retrieve
    from modules.memory.embedding import EmbeddingEngine
    eng = EmbeddingEngine.get_instance()
    print("[DEBUG] 开始加载 Embedding 模型...", flush=True)
    if not eng._load_model():
        raise RuntimeError("Embedding 模型加载失败，无法启动（请检查网络或代理，模型自动下载需要访问 huggingface.co）")
    print(f"[DEBUG] Embedding 模型加载完成，dim={eng.dim}", flush=True)
    logger.info(f"✓ Embedding 模型已预加载 (dim={eng.dim})")
    print("[DEBUG] 继续后续初始化...", flush=True)

    # 知识库启动预热：Embedding 就绪后后台按哈希增量摄取 KNOWLEDGE_ROOTS，
    # 保证模型第一次调用 knowledge_search 就有内容可引用；不阻塞启动。
    try:
        from modules.knowledge.warmup import start_background_ingest
        if start_background_ingest() is not None:
            logger.info("✓ 知识库后台增量摄取已启动")
    except Exception as e:
        logger.warning(f"知识库后台增量摄取启动失败 (非致命): {e}")

    # 预加载视觉模型（同步加载，先加载再启动，避免运行中首次调用才加载）
    # auto/mlx/transformers/api 均为真实视觉后端；mock 为模拟，无需加载
    # 上限 60s：模型加载过慢（或首次下载）时降级为按需加载，不拖垮整体启动
    if settings.VISION_BACKEND and settings.VISION_BACKEND.lower() != "mock":
        try:
            from infra.data_process.core.image_analyzer import ImageAnalyzer
            analyzer = ImageAnalyzer(model_type="auto")
            await asyncio.wait_for(analyzer.initialize(), timeout=60)
            logger.info(f"✓ 视觉模型预加载完成 (type={analyzer.model_type})")
        except asyncio.TimeoutError:
            logger.warning("视觉模型预加载超时(60s)，降级为运行中按需加载")
        except Exception as e:
            logger.warning(f"视觉模型预加载失败 (降级，运行中按需加载): {e}")

    # 初始化模型调度管理器
    try:
        from modules.thinking.model_factory import get_model_factory
        get_model_factory().ensure_ready()
        print("[DEBUG] 模型实例工厂就绪", flush=True)
        logger.info("✓ 模型实例工厂已就绪")
    except Exception as e:
        logger.error(f"✗ 模型调度管理器初始化失败: {e}")

    # 初始化流式思考系统
    # @link:block App_Think = Think
    try:
        from modules.thinking.api_stream import initialize_system
        print("[DEBUG] 开始初始化流式思考系统...", flush=True)
        await initialize_system()
        print("[DEBUG] 流式思考系统就绪", flush=True)
        logger.info("✓ 流式思考系统已初始化")
    except Exception as e:
        logger.error(f"✗ 流式思考系统初始化失败: {e}")

    # 初始化全局错误总线的asyncio处理器
    # @link:block App_Write = Write
    try:
        from infra.error_bus import error_bus
        loop = asyncio.get_running_loop()
        error_bus.setup_asyncio_handler(loop)
        logger.info("✓ 全局错误总线已初始化")
    except Exception as e:
        logger.error(f"✗ 全局错误总线初始化失败: {e}")

    # 注册错误总线 WebSocket 回调 — 枚举可推送出口（Retrieve）后逐个发送（Emit）
    # @link:block App_Emit = Retrieve · Emit
    try:
        from infra.error_bus import error_bus
        from modules.thinking.api_stream import connection_manager

        def _on_error(error_type: str, error_msg: str, ctx: dict):
            """错误回调：推送到所有活跃前端出口（WebSocket ∪ SSE）"""
            from modules.thinking.api_stream import _build_event
            for session_id in connection_manager.push_sessions():
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

            # 桌面宠物（语音触发 + 主会话对话；由装配层拉起，
            # 不挂在感知系统内部以保持 perception 不反向依赖 desktop_pet）
            if settings.DESKTOP_PET_ENABLED:
                try:
                    from modules.desktop_pet.pet_engine import PetEngine
                    PetEngine.get_instance(ps.event_bus).start()
                    logger.info("✓ 桌宠已启动")
                except Exception as e:
                    logger.warning(f"桌面宠物启动失败: {e}")
        except Exception as e:
            logger.error(f"✗ 感知系统启动失败: {e}")

    # 会话定时任务（每会话独立，到点调用逻辑）
    # 由装配层拉起：定时任务属思考侧生命周期，若挂在 perception.start() 内会让
    # perception 反向依赖 thinking，与 thinking → perception 形成逻辑循环。
    # 同时放在感知开关之外，使「关掉感知」不再连带禁用定时任务。
    try:
        from modules.thinking.scheduled_tasks import get_task_manager
        get_task_manager().start()
        logger.info("✓ 会话定时任务已启动")
    except Exception as e:
        logger.warning(f"会话定时任务启动失败: {e}")

    # MCP 屏幕差异检测（独立子进程，像素级帧差）
    if settings.SCREEN_DIFF_ENABLED and settings.DIFFERENCE_DETECTOR_ENABLED and settings.PERCEPTION_MCP_ENABLED:
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
    if settings.DIFFERENCE_DETECTOR_ENABLED and settings.PERCEPTION_INTERNAL_ENABLED:
        try:
            from modules.perception.difference import get_heartbeat
            get_heartbeat().start()
            logger.info("✓ 差异检测器心跳已启动 (1Hz)")
        except Exception as e:
            logger.warning(f"差异检测器心跳启动失败: {e}")

    # 启动时应用系统级设置（真实生效，非摆设）：
    # 防休眠（caffeinate 保系统不睡）+ 开机启动（LaunchAgent）
    try:
        from utils.power import apply as _apply_power, get_process as _power_proc
        _apply_power(bool(getattr(settings, "prevent_sleep", False)))
        # caffeinate 是后端拉起的常驻子进程：登记以随 shutdown 收编，
        # 否则后端重启后会残留。登记由装配层负责，utils.power 不 import infra。
        if _power_proc() is not None:
            from infra.process_registry import register as _reg_proc
            _reg_proc("caffeinate", _power_proc())
    except Exception as e:
        logger.debug(f"防休眠应用失败 (非致命): {e}")
    try:
        from utils.autostart import apply as _apply_autostart
        if getattr(settings, "launch_at_startup", False):
            _apply_autostart(True)
    except Exception as e:
        logger.debug(f"开机启动应用失败 (非致命): {e}")

    yield
    logger.info("Shutting down Humanoid AGI...")

    # 进程锚定收尾: 正常退出时清 PID 文件（kill -9 时文件残留，
    # 卫星进程靠「PID 不存在」探测自动退出），并收编自己拉起的子进程。
    try:
        from infra.watchdog import clear_backend_pid_file
        clear_backend_pid_file(os.getpid())
    except Exception as _swallowed_err:
        logger.debug(f"[lifespan] ignored Exception: {_swallowed_err}")
    try:
        from cortex.process_registry import shutdown_all_children
        shutdown_all_children()
    except Exception as _swallowed_err:
        logger.debug(f"[lifespan] ignored Exception: {_swallowed_err}")

    # 桌宠窗口监管器收尾：停监控线程 + 终止 adopt 的外来窗口 + 清 pet_window_pid.json
    # （shutdown_all_children 只收编 Popen 登记，收编的 adopted_pid 与残留 pid 文件需要这里清）
    try:
        from modules.desktop_pet.window_supervisor import get_pet_window_supervisor
        get_pet_window_supervisor().shutdown()
        logger.info("✓ 桌宠窗口监管器已关闭")
    except Exception as _swallowed_err:
        logger.debug(f"[lifespan] ignored Exception: {_swallowed_err}")

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
    if settings.DIFFERENCE_DETECTOR_ENABLED and settings.PERCEPTION_INTERNAL_ENABLED:
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

# 主模型配置键：变更后需重建模型实例（客户端在构造时读取配置）
_MODEL_CONFIG_KEYS = {
    "LARGE_MODEL_API_KEY", "LARGE_MODEL_API_URL", "LARGE_MODEL_NAME", "LARGE_MODEL_API_FORMAT",
    "LARGE_MODEL_PROVIDER", "LARGE_MODEL_OPENROUTER_SUPPLIER",
    "MEDIUM_MODEL_API_KEY", "MEDIUM_MODEL_API_URL", "MEDIUM_MODEL_NAME",
    "MEDIUM_MODEL_PROVIDER", "MEDIUM_MODEL_OPENROUTER_SUPPLIER",
    "SMALL_MODEL_API_KEY", "SMALL_MODEL_API_URL", "SMALL_MODEL_NAME",
    "SMALL_MODEL_PROVIDER", "SMALL_MODEL_OPENROUTER_SUPPLIER",
}


app = FastAPI(
    title="Humanoid AGI",
    description="类人智能架构系统 API",
    version=_CORTEX_VERSION,
    lifespan=lifespan
)

# ── TTS 音频输出静态挂载（/speech 生成的 mp3 通过 /audio/ 对外提供） ──
# check_dir=False：目录不存在时不崩溃启动，由 TTSEngine 首次合成时创建
_tts_dir = _get_tts_dir()
_tts_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(_tts_dir), check_dir=False), name="audio")

# ── 桌宠静态挂载（Live2D 需经 http 加载 wasm，file:// 会被 Chromium 阻止） ──
_PET_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "pet")
if os.path.isdir(_PET_DIR):
    app.mount("/pet", StaticFiles(directory=_PET_DIR), name="pet")

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
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Request-ID", "X-Trace-Id", "X-Request-Seq"],
)

# ---------------------------------------------------------------------------
# API Key 认证中间件
# ---------------------------------------------------------------------------

_SIMPLE_API_KEY = settings.SIMPLE_API_KEY
_AUTH_WHITELIST = {
    "/", "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico",
    "/dashboard", "/dashboard/",
    # 会话端点：前端无需录入 key 即可建会话/查询状态（低风险写操作，WS 不走 HTTP 中间件）
    "/stream/session", "/stream/status",
    "/stream/sessions", "/stream/sessions/",
    "/management/sessions", "/management/sessions/",
    "/config", "/config/",
    # 只读状态/列表接口：前端各页面 init 即调用，无 key 也可浏览
    "/management/dashboard",
    "/management/modules",
    "/management/thinking",
    "/management/database",
    "/management/info-process",
    "/management/models",
    "/management/perception",
    "/attention/status",
    "/tools", "/tools/", "/tools/events",
    "/security/status", "/security/audit",
}
_AUTH_WHITELIST_PREFIXES = ("/management/causal-graph", "/management/memory",
                             "/stream/session/", "/stream/sessions/",
                              "/stream/pet/",
                               "/management/sessions/", "/config/",
                               "/management/api-requests",
                               "/management/open-folder",
                               "/management/vision-models",
                               "/management/orchestration",
                               "/management/persona-presets",
                               "/management/skills",
                               "/management/config/",
                               "/management/todos",
                              "/tools/info/", "/tools/enabled/", "/tools/ai", "/tools/source/",
                              "/audio", "/pet/")  # TTS 音频供前端 <audio> 无鉴权播放；/pet/ 桌宠 Live2D 资源


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    # 敏感配置写入（模型 API Key/URL/名称）不走 /config/ 前缀豁免，必须认证：
    # 否则任何能访问后端的人可把模型 Key/URL 改成自己控制的地址（密钥外泄/DoS）
    _sensitive_config_write = (
        request.method == "PUT"
        and request.url.path.startswith("/config/")
        and (request.url.path[len("/config/"):] or "").split("/")[0].upper() in _MODEL_CONFIG_KEYS
    )
    # 白名单路径跳过
    if not _sensitive_config_write and (request.url.path in _AUTH_WHITELIST or request.url.path.startswith("/docs") or request.url.path.startswith("/redoc") or any(request.url.path.startswith(p) for p in _AUTH_WHITELIST_PREFIXES)):
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
# 高频本地轮询端点跳过限流（桌宠轮询/健康检查/前端初始化等只读查询，与 _API_GET_IGNORE 对齐）
_RATE_LIMIT_WHITELIST_PATHS = (
    "/stream/pet/move", "/stream/pet/last-reply",
    "/stream/pet/actions", "/stream/status", "/stream/sessions",
    "/config", "/health", "/metrics", "/dashboard", "/dashboard/",
)


# 每处理 500 次请求清理一次过期的分钟 key（key 格式: ip|minute）
def _cleanup_request_counts() -> None:
    current_minute = int(time.time() / 60)
    stale = [k for k in request_counts if isinstance(k, str) and "|" in k]
    for k in stale:
        try:
            _ip, minute = k.split("|", 1)
            if int(minute) < current_minute:
                del request_counts[k]
        except (ValueError, KeyError) as _swallowed_err:
            logger.debug(f"[_cleanup_request_counts] ignored (ValueError, KeyError): {_swallowed_err}")
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


# 高频轮询 GET（桌宠状态/健康检查等）不记录到仪表盘请求日志
_API_GET_IGNORE = {
    "/stream/pet/move", "/stream/pet/last-reply",
    "/stream/pet/actions", "/stream/status", "/stream/sessions",
    "/config", "/health", "/metrics", "/dashboard", "/dashboard/",
}

# 不记录 body 的内容类型（流式 / 二进制 / 表单上传）
_API_BODY_IGNORE_CT = ("text/event-stream", "application/octet-stream")
# 不读取请求体的内容类型
_API_REQ_BODY_IGNORE_CT = ("multipart/form-data", "application/x-www-form-urlencoded", "text/event-stream")

# 记录上限（与 ApiLogStore._MAX_RESP_BODY 配合，避免超大响应拖慢/撑爆）
_API_LOG_MAX_RESP_BODY = 8000
_API_LOG_MAX_REQ_BODY = 4000

# 脱敏：屏蔽常见密钥/口令字段的值（password/secret/api_key/token 等）
import re as _re
_API_SECRET_PAT = _re.compile(
    r'(?i)("?(?:password|passwd|secret|api[_-]?key|access[_-]?token|authorization|token)"?\s*[:=]\s*)([^",}]{4,})'
)


def _sanitize_body(text: str) -> str:
    if not text:
        return ""
    return _API_SECRET_PAT.sub(r"\1***", text)


def _truncate_body(text: str, limit: int) -> str:
    if text is None:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n...[已截断]"
    return text


@app.middleware("http")
async def api_request_log_middleware(request: Request, call_next):
    """记录 API 请求（持久化 SQLite，供仪表盘筛选/分析/追溯）

    记录详细参数与返回值（请求体/响应体），敏感字段脱敏，超大 body 截断；
    流式响应（SSE）与二进制内容不读取 body，保留原响应直通。
    """
    t0 = time.monotonic()
    # 读取并缓存请求体（Starlette 会写入 request._body，不影响下游消费）
    req_body = ""
    try:
        ct = request.headers.get("content-type", "")
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not any(k in ct for k in _API_REQ_BODY_IGNORE_CT):
            req_body = _truncate_body(
                _sanitize_body((await request.body()).decode("utf-8", errors="replace")), _API_LOG_MAX_REQ_BODY
            )
        # GET/DELETE 无 body：记录 query 参数，让详情有内容可看（否则恒为“无记录”）
        elif request.url.query:
            req_body = _truncate_body(
                _sanitize_body("?" + request.url.query), _API_LOG_MAX_REQ_BODY
            )
    except Exception as _swallowed_err:
        logger.debug(f"[api_request_log_middleware] ignored Exception: {_swallowed_err}")

    response = await call_next(request)
    try:
        # 高频轮询 GET 不记录
        if request.method == "GET" and request.url.path in _API_GET_IGNORE:
            return response

        resp_body = ""
        resp_ct = response.headers.get("content-type", "")
        if not any(k in resp_ct for k in _API_BODY_IGNORE_CT):
            # 消费响应体以便记录，随后重建响应（保留 headers / background）
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            resp_bytes = b"".join(chunks)
            if resp_bytes:
                resp_body = _truncate_body(
                    _sanitize_body(resp_bytes.decode("utf-8", errors="replace")), _API_LOG_MAX_RESP_BODY
                )
            new_resp = Response(
                content=resp_bytes,
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding")},
                media_type=response.media_type,
            )
            new_resp.background = response.background
            response = new_resp

        from modules.management.api_log_store import ApiLogStore
        ApiLogStore.get_instance().add(
            request.method, request.url.path, response.status_code,
            (time.monotonic() - t0) * 1000,
            request_body=req_body,
            response_body=resp_body,
        )
    except Exception as _swallowed_err:
        logger.debug(f"[api_request_log_middleware] ignored Exception: {_swallowed_err}")
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in _RATE_LIMIT_WHITELIST_PATHS:
        return await call_next(request)
    client_ip = _get_client_ip(request)
    current_minute = int(time.time() / 60)

    key = f"{client_ip}|{current_minute}"
    # 本地回环（127.0.0.1/::1）放宽上限，公网保持 100/分钟防滥用
    limit = 1000 if client_ip in ("127.0.0.1", "::1", "localhost") else 100
    # Q-7: Use async lock to prevent race condition between check and increment
    async with _rate_limit_lock:
        current_count = request_counts.get(key, 0)
        if current_count >= limit:
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
    app.include_router(desktop_pet_router)
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
    from infra.error_bus import error_bus, ErrorContext
    error_bus.report_error(
        exc,
        ErrorContext(
            module="api.main",
            function="general_exception_handler",
            extra={"method": request.method, "path": request.url.path},
        )
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
        "status": "running",
        "storage_path": getattr(settings, "storage_path", "") or "",
    }}


@app.get("/health")
async def health_check():
    """健康检查 — 验证关键依赖"""
    checks = {}
    all_healthy = True
    checks["version"] = _CORTEX_VERSION

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
        from sqlalchemy import text
        # get_session 是 @contextmanager 生成器，必须用 with 进入
        # （错误用法 get_session().close() 会抛 AttributeError，误报 unavailable）
        # 执行真实查询：确保每次检查都真正验证数据库可用（session 惰性连接）
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        logger.debug("健康检查: 数据库不可用: %s", e)
        checks["database"] = "unavailable"
        all_healthy = False

    status = "healthy" if all_healthy else "degraded"
    return {
        "success": True,
        "data": {"status": status, "checks": checks, "version": _CORTEX_VERSION},
    }


# ── 检查更新：GitHub 最新 release 版本号（服务端代理，规避浏览器 CORS 与 API 限流）──

_GITHUB_REPO = "15087312/cortex_agent"


def _parse_tag_version(tag: str) -> str:
    """从 tag 提取版本号（去 v 前缀）：'v2.4.0' → '2.4.0'"""
    return (tag or "").strip().lstrip("vV").strip()


def _cmp_versions(a: str, b: str) -> int:
    """x.y.z 数值分段比较：a>b 返回 1，a<b 返回 -1，相等返回 0"""
    def parts(v: str):
        return [int(p) if p.isdigit() else 0 for p in (v or "").split(".")]
    pa, pb = parts(a), parts(b)
    for i in range(max(len(pa), len(pb))):
        x = pa[i] if i < len(pa) else 0
        y = pb[i] if i < len(pb) else 0
        if x != y:
            return 1 if x > y else -1
    return 0


def _fetch_latest_release_version(timeout: float = 8.0) -> dict:
    """获取 GitHub 最新 release 版本。

    优先用 releases/latest 页面重定向（不受 REST API 60次/小时限流），
    失败再回退 GitHub API。
    """
    import requests as _requests

    # 1) 重定向技巧：/releases/latest → 302 Location: .../tag/vX.Y.Z（无 API 配额）
    try:
        r = _requests.get(
            f"https://github.com/{_GITHUB_REPO}/releases/latest",
            allow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": "cortex-agent-update-check"},
        )
        loc = r.headers.get("Location", "") or ""
        if r.status_code in (301, 302, 303, 307, 308) and "/tag/" in loc:
            tag = loc.rstrip("/").rsplit("/tag/", 1)[-1]
            version = _parse_tag_version(tag)
            if version:
                return {"latest": version, "url": f"https://github.com/{_GITHUB_REPO}/releases/tag/{tag}", "source": "redirect"}
    except Exception as e:
        logger.debug(f"检查更新: 重定向方式失败: {e}")

    # 2) 回退 GitHub API（有限流）
    try:
        r = _requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
            timeout=timeout,
            headers={"User-Agent": "cortex-agent-update-check", "Accept": "application/vnd.github+json"},
        )
        if r.ok:
            j = r.json()
            tag = j.get("tag_name") or ""
            version = _parse_tag_version(tag)
            if version:
                return {"latest": version, "url": j.get("html_url") or f"https://github.com/{_GITHUB_REPO}/releases/latest", "source": "api"}
    except Exception as e:
        logger.debug(f"检查更新: API 方式失败: {e}")

    raise RuntimeError("无法获取最新版本（GitHub 不可达或无发布）")


@app.get("/system/doctor")
async def system_doctor():
    """前置自检 — ffmpeg/原生依赖/密钥/权重逐项体检（附修复建议）"""
    from api.doctor import run_doctor
    return run_doctor()


@app.get("/system/licenses")
async def system_licenses():
    """第三方许可声明（含 GPL ffmpeg 源码获取告知）— SVG 打包态从 _MEIPASS 读，开发态从项目根读"""
    candidates = []
    base = getattr(sys, "_MEIPASS", "")
    if base:
        candidates.append(Path(base) / "THIRD_PARTY_LICENSES.md")
    candidates.append(Path(__file__).resolve().parents[1] / "THIRD_PARTY_LICENSES.md")
    for p in candidates:
        if p.is_file():
            return {"success": True, "data": {"content": p.read_text(encoding="utf-8")}}
    return JSONResponse(status_code=404, content={
        "success": False,
        "error": {"code": "LICENSES_NOT_FOUND", "message": "未找到许可声明文件"},
    })


@app.get("/system/latest-version")
async def latest_version():
    """检查更新 — 比较 GitHub 最新 release 与当前版本"""
    cur = _CORTEX_VERSION.lstrip("vV")
    try:
        info = await asyncio.to_thread(_fetch_latest_release_version)
    except Exception as e:
        return JSONResponse(status_code=502, content={
            "success": False,
            "error": {"code": "UPDATE_CHECK_FAILED", "message": str(e)},
            "data": {"current": cur},
        })
    latest = info["latest"]
    return {
        "success": True,
        "data": {
            "current": cur,
            "latest": latest,
            "update_available": _cmp_versions(latest, cur) > 0,
            "release_url": info.get("url", ""),
            "source": info.get("source", ""),
        },
    }


# ---------------------------------------------------------------------------
# Config Update API
# ---------------------------------------------------------------------------

class PutConfigRequest(BaseModel):
    value: str | int | float | bool
    system_override: Optional[str] = None  # 高级设置：完整系统提示词覆盖（可选）


class MemoryLibRequest(BaseModel):
    name: str = ""        # 新建 / 切换时用的记忆库名
    old_name: str = ""    # 重命名：旧名
    new_name: str = ""    # 重命名：新名


@app.get("/config")
async def get_config():
    """读取当前运行时配置（仅返回可修改的配置项；大小写字段名保持一致）"""
    config_data = {}
    for key in _MODIFIABLE_CONFIG_KEYS:
        val = getattr(settings, key, None)
        if val is None:
            val = getattr(settings, key.lower(), None)
        if val is not None:
            actual = key if key in type(settings).model_fields else key.lower()
            config_data[actual] = val
    return {"data": config_data}


@app.get("/config/personas")
async def get_personas():
    """返回所有角色的人设：默认 / 自定义 / 当前生效（供设置页编辑）"""
    from config.prompts.loader import get_loader
    roles = (get_loader().load("roles") or {}).get("roles") or {}
    result = []
    for key, data in roles.items():
        custom = settings.get_persona(key)
        result.append({
            "role": key,
            "name": data.get("name", key),
            "tier": data.get("tier", ""),
            "default": data.get("personality", ""),
            "custom": custom,
            "system_override": settings.get_system_override(key),
        })
    return {"success": True, "data": {"personas": result}}


# ── 记忆库管理（多记忆库切换 + 命名）──

@app.get("/config/memory-libs")
async def get_memory_libs():
    """列出所有记忆库（含当前库与事件数）"""
    data = settings.get_memory_libs()
    current = data.get("current", "")
    libs = []
    for name, lib in (data.get("libs", {}) or {}).items():
        libs.append({
            "name": name,
            "current": name == current,
            "event_count": settings.memory_lib_event_count(name),
        })
    return {"success": True, "data": {"current": current, "libs": libs}}


@app.post("/config/memory-libs")
async def create_memory_lib(body: MemoryLibRequest):
    """创建并命名一个新记忆库，并切换过去"""
    name = (body.name or "").strip()
    if not name:
        return JSONResponse(status_code=422, content=error_response(ErrorCode.VALIDATION_ERROR, "记忆库名不能为空").model_dump())
    try:
        lib = settings.create_memory_lib(name)
    except Exception as e:
        logger.error(f"记忆库创建失败: {name} - {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"创建失败: {e}").model_dump())
    if lib is None:
        return JSONResponse(status_code=409, content=error_response(ErrorCode.BAD_REQUEST, f"记忆库 '{name}' 已存在").model_dump())
    return {"success": True, "data": {"name": name, "lib": lib}}


@app.put("/config/memory-libs/current")
async def switch_memory_lib(body: MemoryLibRequest):
    """切换当前记忆库"""
    name = (body.name or "").strip()
    try:
        if not settings.switch_memory_lib(name):
            return JSONResponse(status_code=404, content=error_response(ErrorCode.NOT_FOUND, f"记忆库 '{name}' 不存在").model_dump())
    except Exception as e:
        logger.error(f"记忆库切换失败: {name} - {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"切换失败: {e}").model_dump())
    return {"success": True, "data": {"current": name}}


@app.put("/config/memory-libs/rename")
async def rename_memory_lib(body: MemoryLibRequest):
    """重命名记忆库"""
    old = (body.old_name or "").strip()
    new = (body.new_name or "").strip()
    if not old or not new:
        return JSONResponse(status_code=422, content=error_response(ErrorCode.VALIDATION_ERROR, "需提供 old_name 与 new_name").model_dump())
    try:
        if not settings.rename_memory_lib(old, new):
            return JSONResponse(status_code=409, content=error_response(ErrorCode.BAD_REQUEST, "重命名失败（记忆库不存在或名称冲突）").model_dump())
    except Exception as e:
        logger.error(f"记忆库重命名失败: {old} -> {new} - {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"重命名失败: {e}").model_dump())
    return {"success": True, "data": {"old_name": old, "new_name": new}}


@app.delete("/config/memory-libs/{name}")
async def delete_memory_lib(name: str):
    """删除记忆库（默认库不可删；若删的是当前库则切回默认）"""
    try:
        if not settings.delete_memory_lib(name):
            return JSONResponse(status_code=404, content=error_response(ErrorCode.NOT_FOUND, f"记忆库 '{name}' 不存在或不可删除").model_dump())
    except Exception as e:
        logger.error(f"记忆库删除失败: {name} - {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"删除失败: {e}").model_dump())
    return {"success": True, "data": {"deleted": name}}


# ── 知识库（文档 RAG，独立于事件记忆）──

class KnowledgeIngestRequest(BaseModel):
    path: str = ""                      # 空 = 按 KNOWLEDGE_ROOTS 白名单全量增量摄取


class KnowledgeSearchRequest(BaseModel):
    query: str = ""
    top_k: int = 5
    path_prefix: str = ""


class KnowledgeReindexRequest(BaseModel):
    full: bool = False


def _kb_service():
    """知识库门面（惰性导入：请求到来时才引入 knowledge 依赖链）"""
    from modules.knowledge.service import get_knowledge_service
    return get_knowledge_service()


@app.get("/knowledge/docs")
async def list_knowledge_docs(limit: int = 50, offset: int = 0):
    """列出已入库的知识文档（标题/路径/块数/更新时间）"""
    try:
        docs = await asyncio.to_thread(_kb_service().list_docs, max(1, min(200, limit)), max(0, offset))
    except Exception as e:
        logger.error(f"知识库文档列表读取失败: {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"读取失败: {e}").model_dump())
    return {"success": True, "data": {"documents": docs, "count": len(docs)}}


@app.post("/knowledge/ingest")
async def ingest_knowledge(body: KnowledgeIngestRequest = None):
    """摄取文件或目录（路径必须在 KNOWLEDGE_ROOTS 白名单内；空则全量增量）"""
    path = ((body.path if body else "") or "").strip()
    try:
        result = await asyncio.to_thread(_kb_service().ingest, path)
    except Exception as e:
        logger.error(f"知识库摄取失败: {path} - {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"摄取失败: {e}").model_dump())
    if result.get("error"):
        return JSONResponse(status_code=422, content=error_response(ErrorCode.VALIDATION_ERROR, result["error"]).model_dump())
    return {"success": True, "data": result}


@app.delete("/knowledge/docs/{doc_id}")
async def delete_knowledge_doc(doc_id: str):
    """删除知识文档（连带片段与向量）"""
    try:
        result = await asyncio.to_thread(_kb_service().delete, doc_id)
    except Exception as e:
        logger.error(f"知识库删除失败: {doc_id} - {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"删除失败: {e}").model_dump())
    if not result.get("deleted"):
        return JSONResponse(status_code=404, content=error_response(ErrorCode.NOT_FOUND, result.get("reason", "文档不存在")).model_dump())
    return {"success": True, "data": result}


@app.post("/knowledge/search")
async def search_knowledge(body: KnowledgeSearchRequest):
    """知识检索（调试/前端用；模型侧走 knowledge_search 工具）"""
    query = (body.query or "").strip()
    if not query:
        return JSONResponse(status_code=422, content=error_response(ErrorCode.VALIDATION_ERROR, "query 不能为空").model_dump())
    try:
        hits = await asyncio.to_thread(_kb_service().search, query, body.top_k, body.path_prefix.strip())
    except Exception as e:
        logger.error(f"知识库检索失败: {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"检索失败: {e}").model_dump())
    return {"success": True, "data": {"hits": hits, "count": len(hits)}}


@app.post("/knowledge/reindex")
async def reindex_knowledge(body: KnowledgeReindexRequest = None):
    """重建索引：full=false 按哈希增量；full=true 清空向量整库重嵌"""
    full = bool(body.full) if body else False
    try:
        result = await asyncio.to_thread(_kb_service().reindex, full)
    except Exception as e:
        logger.error(f"知识库重建失败: {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"重建失败: {e}").model_dump())
    return {"success": True, "data": result}


@app.get("/knowledge/stats")
async def knowledge_stats():
    """知识库统计：文档数 / 片段数 / 索引大小 / 维度 / 白名单根"""
    try:
        stats = await asyncio.to_thread(_kb_service().stats)
    except Exception as e:
        logger.error(f"知识库统计失败: {e}")
        return JSONResponse(status_code=500, content=error_response(ErrorCode.INTERNAL_ERROR, f"统计失败: {e}").model_dump())
    return {"success": True, "data": stats}


@app.put("/config/persona/{role}")
async def update_persona(role: str, body: PutConfigRequest):
    """更新指定角色的人设提示词（value 为空则恢复默认；system_override 可选）"""
    try:
        result = settings.set_persona_and_override(
            role, str(body.value or ""),
            body.system_override if body.system_override is not None else None,
        )
    except Exception as e:
        logger.error(f"人设保存失败: {role} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "SAVE_FAILED", "message": f"保存失败: {e}"}})
    logger.info(f"人设已更新: {role}")
    return {
        "success": True,
        "data": {
            "role": role,
            "custom": result["persona"],
            "system_override": result["system_override"],
        },
    }


@app.get("/config/tools/{role}")
async def get_role_tools(role: str):
    """读取指定角色的工具权限覆盖 {whitelist, blacklist}"""
    return {"success": True, "data": {"role": role, "tools": settings.get_role_tools(role)}}


@app.put("/config/tools/{role}")
async def update_role_tools(role: str, body: dict = None):
    """写入指定角色的工具权限覆盖 {whitelist: [], blacklist: []}（空则清除）"""
    body = body or {}
    cfg = body.get("tools") or {}
    if not isinstance(cfg, dict):
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR", "message": "tools 需为对象 {whitelist, blacklist}"}})
    try:
        settings.set_role_tools(role, cfg)
    except Exception as e:
        logger.error(f"工具权限保存失败: {role} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "SAVE_FAILED", "message": f"保存失败: {e}"}})
    logger.info(f"工具权限已更新: {role}")
    return {"success": True, "data": {"role": role, "tools": settings.get_role_tools(role)}}


@app.get("/config/model-params/{role}")
async def get_model_params(role: str):
    """读取指定角色的模型参数覆盖 {temperature, max_tokens}"""
    return {"success": True, "data": {"role": role, "params": settings.get_model_params(role)}}


@app.put("/config/model-params/{role}")
async def update_model_params(role: str, body: dict = None):
    """写入指定角色的模型参数覆盖（空则清除）"""
    body = body or {}
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR", "message": "params 需为对象"}})
    try:
        settings.set_model_params(role, params)
    except Exception as e:
        logger.error(f"模型参数保存失败: {role} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "SAVE_FAILED", "message": f"保存失败: {e}"}})
    # 重建模型实例（新参数立即生效，无需重启）
    try:
        from modules.thinking.model_factory import get_model_factory
        await get_model_factory().reload_from_config()
        logger.info(f"模型参数已更新并重建实例: {role}")
    except Exception as e:
        logger.warning(f"模型参数已保存但实例重建失败: {e}")
    return {"success": True, "data": {"role": role, "params": settings.get_model_params(role)}}


# ── 自定义 Agent 管理 ──

@app.post("/management/orchestration/agents")
async def create_custom_agent(body: dict = None):
    """新增自定义 agent（存入 personas.yaml）"""
    body = body or {}
    role = (body.get("role") or "").strip()
    name = (body.get("name") or "").strip()
    tier = (body.get("tier") or "").strip()
    if not role or not name or not tier:
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR", "message": "role/name/tier 不能为空"}})
    if tier not in ("large", "supervisor", "expert"):
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR", "message": "tier 必须是 large/supervisor/expert"}})
    # 检查 role 是否与内置 agent 冲突
    from config.prompts.loader import get_loader
    builtin_roles = ((get_loader().load("roles") or {}).get("roles") or {}).keys()
    if role in builtin_roles:
        return JSONResponse(status_code=409, content={"success": False,
                            "error": {"code": "CONFLICT", "message": f"角色 {role} 与内置 agent 冲突"}})
    # 检查是否已存在
    if settings.get_custom_agent(role):
        return JSONResponse(status_code=409, content={"success": False,
                            "error": {"code": "CONFLICT", "message": f"角色 {role} 已存在"}})
    agent_data = {
        "role": role,
        "name": name,
        "tier": tier,
        "personality": body.get("personality", ""),
        "speaking_style": body.get("speaking_style", ""),
        "expertise": body.get("expertise", ""),
        "model_id": body.get("model_id", ""),
    }
    try:
        settings.set_custom_agent(role, agent_data)
    except Exception as e:
        logger.error(f"自定义 agent 保存失败: {role} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "SAVE_FAILED", "message": f"保存失败: {e}"}})
    logger.info(f"自定义 agent 已创建: {role}")
    return {"success": True, "data": {"agent": agent_data}}


@app.delete("/management/orchestration/agents/{role}")
async def delete_custom_agent(role: str):
    """删除自定义 agent（persona/override/tools/params 由 delete_custom_agent 一并清理）"""
    try:
        if settings.delete_custom_agent(role):
            logger.info(f"自定义 agent 已删除: {role}")
            return {"success": True}
    except Exception as e:
        logger.error(f"自定义 agent 删除失败: {role} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "DELETE_FAILED", "message": f"删除失败: {e}"}})
    return JSONResponse(status_code=404, content={"success": False,
                        "error": {"code": "NOT_FOUND", "message": f"未找到自定义 agent: {role}"}})


@app.put("/management/orchestration/agents/{role}/active")
async def toggle_agent_active(role: str, body: dict = None):
    """切换 agent 启用/禁用状态"""
    body = body or {}
    active = body.get("active", True)
    # 检查 agent 是否存在（内置或自定义）
    from config.prompts.loader import get_loader
    builtin_roles = (get_loader().load("roles") or {}).get("roles") or {}
    is_builtin = role in builtin_roles
    is_custom = settings.get_custom_agent(role) is not None
    if not is_builtin and not is_custom:
        return JSONResponse(status_code=404, content={"success": False,
                            "error": {"code": "NOT_FOUND", "message": f"未找到 agent: {role}"}})
    # 获取该 agent 的 tier
    agent_tier = None
    if role in builtin_roles:
        agent_tier = builtin_roles[role].get("tier", "")
    elif is_custom:
        agent_tier = (settings.get_custom_agent(role) or {}).get("tier", "")
    # 总指挥层(large)只能有一个激活：激活时自动停用同层其他（原子操作）
    try:
        if active and agent_tier == "large":
            settings.deactivate_and_set_active(role, agent_tier, builtin_roles, active)
        else:
            settings.set_agent_active(role, active)
    except Exception as e:
        logger.error(f"Agent 启用状态保存失败: {role} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "SAVE_FAILED", "message": f"保存失败: {e}"}})
    logger.info(f"Agent {role} active={active}")
    return {"success": True, "data": {"role": role, "active": active}}


# ── 人设预设管理 ──

@app.get("/management/persona-presets")
async def list_persona_presets():
    """列出所有人设预设"""
    return {"success": True, "data": {"presets": settings.get_persona_presets()}}


@app.post("/management/persona-presets")
async def save_persona_preset(body: dict = None):
    """保存人设预设（将当前所有人设存为预设）"""
    body = body or {}
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=422, content={"success": False,
                            "error": {"code": "VALIDATION_ERROR", "message": "预设名不能为空"}})
    import uuid
    preset_id = body.get("id") or str(uuid.uuid4())[:8]
    personas = body.get("personas") or {}
    if not personas:
        # 读取当前所有角色的人设
        from config.prompts.loader import get_loader
        roles = (get_loader().load("roles") or {}).get("roles") or {}
        for key in list(roles.keys()) + [ca["role"] for ca in settings.get_custom_agents()]:
            p = settings.get_persona(key)
            if p:
                personas[key] = p
    try:
        preset = settings.save_persona_preset(preset_id, name, personas)
    except Exception as e:
        logger.error(f"人设预设保存失败: {name} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "SAVE_FAILED", "message": f"保存失败: {e}"}})
    logger.info(f"人设预设已保存: {name} ({preset_id})")
    return {"success": True, "data": {"preset": preset}}


@app.delete("/management/persona-presets/{preset_id}")
async def delete_persona_preset(preset_id: str):
    """删除人设预设"""
    try:
        if settings.delete_persona_preset(preset_id):
            logger.info(f"人设预设已删除: {preset_id}")
            return {"success": True}
    except Exception as e:
        logger.error(f"人设预设删除失败: {preset_id} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "DELETE_FAILED", "message": f"删除失败: {e}"}})
    return JSONResponse(status_code=404, content={"success": False,
                        "error": {"code": "NOT_FOUND", "message": f"未找到预设: {preset_id}"}})


@app.put("/management/persona-presets/{preset_id}/apply")
async def apply_persona_preset(preset_id: str):
    """应用人设预设（将预设人设写入当前生效值）"""
    try:
        if settings.apply_persona_preset(preset_id):
            logger.info(f"人设预设已应用: {preset_id}")
            return {"success": True}
    except Exception as e:
        logger.error(f"人设预设应用失败: {preset_id} - {e}")
        return JSONResponse(status_code=500, content={"success": False,
                            "error": {"code": "SAVE_FAILED", "message": f"应用失败: {e}"}})
    return JSONResponse(status_code=404, content={"success": False,
                        "error": {"code": "NOT_FOUND", "message": f"未找到预设: {preset_id}"}})


@app.get("/config/api-key")
async def get_api_key(request: Request):
    """返回 API key 配置状态（供前端自动注入，免手动录入）。

    - /config/ 前缀在白名单内，本端点无需鉴权即可访问
    - 开发/测试环境：直接返回明文 key（本地免手动录入）
    - 生产环境：仅本地回环客户端（localhost/127.0.0.1/::1）返回明文 key，
      其余网络客户端只返回是否已配置——防止密钥泄露到局域网/公网。
      前端 vite 代理在本机运行，请求来自回环地址，故自动检测仍可用。
    """
    # 复用 _get_client_ip：直接连接来自受信代理时解析 X-Forwarded-For 取真实 IP，
    # 避免生产环境经同机反向代理时误判为回环而泄露明文 key
    is_loopback = _get_client_ip(request) in ("127.0.0.1", "::1", "localhost")
    if settings.APP_ENV == "production" and not is_loopback:
        return {"success": True, "data": {"configured": bool(_SIMPLE_API_KEY), "api_key": ""}}
    return {"success": True, "data": {"configured": bool(_SIMPLE_API_KEY), "api_key": _SIMPLE_API_KEY}}


@app.put("/config/{key}")
async def update_config(key: str, body: PutConfigRequest):
    """更新运行时配置项（仅限允许列表内的 key）"""
    key_upper = key.upper()
    if key_upper not in _MODIFIABLE_CONFIG_KEYS:
        return JSONResponse(
            status_code=403,
            content=error_response(ErrorCode.FORBIDDEN, f"配置项 '{key}' 不允许通过 API 修改").model_dump()
        )

    # 检查字段是否存在（兼容大小写字段名：大写如 CORTEX_MODE，小写如 launch_at_startup）
    field_info = type(settings).model_fields.get(key_upper) or type(settings).model_fields.get(key)
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
        actual = key_upper if key_upper in type(settings).model_fields else key
        old_value = getattr(settings, actual, None)
        # 先落盘、再应用：写失败时内存/环境变量保持原值，避免出现
        # “接口报 500 但配置已生效、重启又回滚”的不一致（§102）
        settings.save_user_config([actual], values={actual: validated})
        object.__setattr__(settings, actual, validated)
        # CORTEX_MODE 需要同步写回环境变量，保证 chat_gateway._resolve_mode 生效
        if key_upper == "CORTEX_MODE":
            os.environ["CORTEX_MODE"] = str(validated)
        # 主模型配置变更 → 重建模型实例（新 API Key/URL/模型名立即生效，无需重启）
        if key_upper in _MODEL_CONFIG_KEYS:
            try:
                from modules.thinking.model_factory import get_model_factory
                await get_model_factory().reload_from_config()
                logger.info(f"模型配置已变更，重建模型实例: {key_upper}")
            except Exception as _reload_err:
                logger.warning(f"模型实例重建失败（可手动重启后端）: {_reload_err}")
        # 系统级配置：变更时立即生效（防休眠 caffeinate / 开机启动 LaunchAgent / 感知系统开关）
        try:
            if key_upper == "PREVENT_SLEEP":
                from utils.power import apply as _apply_power, get_process as _power_proc
                _apply_power(bool(validated))
                if _power_proc() is not None:
                    from infra.process_registry import register as _reg_proc
                    _reg_proc("caffeinate", _power_proc())
            elif key_upper == "LAUNCH_AT_STARTUP":
                from utils.autostart import apply as _apply_autostart
                _apply_autostart(bool(validated))
            elif key_upper == "PERCEPTION_ENABLED":
                from modules.perception.setup import get_perception_system
                ps = get_perception_system()
                if validated:
                    ps.setup()
                    ps.start()
                    logger.info("感知系统已启动（运行时开启）")
                else:
                    ps.stop()
                    logger.info("感知系统已停止（运行时关闭）")
            elif key_upper == "DESKTOP_PET_ENABLED":
                from modules.desktop_pet.pet_engine import PetEngine
                try:
                    engine = PetEngine.get_instance()
                    if validated:
                        engine.start()
                        logger.info("桌宠已启动（运行时开启）")
                    else:
                        engine.stop()
                        logger.info("桌宠已停止（运行时关闭）")
                except Exception as _pet_err:
                    logger.warning(f"桌宠运行时切换失败: {_pet_err}")
        except Exception as e:
            logger.warning(f"系统级配置应用失败 {key}: {e}")
        logger.info(f"配置已更新: {actual} = {validated} (旧值: {old_value})")
        return {
            "success": True,
            "data": {"key": actual, "old_value": old_value, "new_value": validated},
        }
    except Exception as e:
        logger.error(f"更新配置失败: {key} -> {e}")
        return JSONResponse(
            status_code=500,
            content=error_response(ErrorCode.INTERNAL_ERROR, f"更新配置失败: {e}").model_dump()
        )


