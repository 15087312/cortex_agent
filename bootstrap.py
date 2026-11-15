"""应用装配层 — 向 infra 业务能力端口注册 modules 实现。

依赖方向：modules → infra（单向、正常方向）。
业务能力（感知/记忆/技能/安全门等）由本模块在应用启动时统一注册到
infra.tool_manager.service_registry，infra 工具层通过 get_capability 获取，
不再直接 import modules，从而消除 infra → modules 的逆向依赖环。

各应用入口（api/main.py、cortex/main.py、frontend/main.py 等）启动时调用
register_business_capabilities()。
"""
from __future__ import annotations

from utils.logger import get_logger

logger = get_logger("app_bootstrap")

# 注册失败记录：_register_all 结束时统一告警，避免静默缺失（DI 启动期校验）
_REGISTER_FAILURES: list = []


def _safe_register(name: str, loader) -> None:
    """单个能力注册失败不影响其余能力（避免一个模块初始化异常拖垮启动）。"""
    from infra.tool_manager.service_registry import register_capability

    try:
        provider = loader()
        register_capability(name, provider)
    except Exception as e:
        _REGISTER_FAILURES.append((name, f"{type(e).__name__}: {e}"))
        logger.warning(f"[bootstrap] 能力 {name} 注册失败: {e}")


def _report_capability_status(expected: list) -> None:
    """启动期校验：报告缺失/失败的能力（fail-fast 提前暴露配置问题）。"""
    from infra.tool_manager.service_registry import registered_names

    registered = set(registered_names())
    missing = [n for n in expected if n not in registered]
    if _REGISTER_FAILURES or missing:
        logger.warning(
            f"[bootstrap] 能力注册不完整: "
            f"失败 {len(_REGISTER_FAILURES)} 个 {_REGISTER_FAILURES}，"
            f"缺失 {len(missing)} 个 {missing}"
        )
    else:
        logger.info(f"[bootstrap] 全部 {len(expected)} 个业务能力注册成功")


def register_business_capabilities() -> None:
    """注册全部业务能力到 infra 端口。可安全重复调用（重复注册覆盖）。"""
    # 失败记录是本次装配的结论，不是进程史。不清空的话：一次偶发失败之后每次
    # 重复调用都报「注册不完整」，数字还一路累加（lifespan 在测试里被反复触发
    # 时就是这么骗过 fail-fast 校验的）。
    _REGISTER_FAILURES.clear()
    # ── 黑板查询 ──
    def _blackboard_query():
        from modules.database.blackboard_repo import query_observations
        return lambda: query_observations

    # ── 技能管理 ──
    def _skill_manager():
        from modules.thinking.skills import _default_skill_manager
        return _default_skill_manager

    # ── 事件检索（记忆）──
    def _event_retrieval():
        from modules.memory.event_retrieval import _default_event_retrieval
        return _default_event_retrieval

    # ── 文件历史 ──
    def _file_history():
        from modules.cortex.file_history import _default_file_history
        return _default_file_history

    # ── 触控点检测（感知）──
    def _touchpoint_detector():
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector
        return lambda: TouchpointDetector

    # ── 检测路由（感知）──
    def _detector_router():
        from modules.perception.screen import get_detector_router
        return get_detector_router

    # ── 价值体系（价值观存储）──
    def _value_system():
        from config.values_store import _default_value_system
        return _default_value_system

    # ── 价值观叙述器（组合上面的价值体系）──
    def _value_formatter():
        from modules.thinking.value_formatter import ValueFormatter
        from config.values_store import get_value_system
        # 这里在调用时才取价值体系：插件换了 value_system，格式化器跟着换，
        # 不会出现「端口换了、叙述还在读内置价值观」的两套行为。
        return lambda: ValueFormatter(get_value_system())

    # ── 工具安全门 ──
    def _tool_security_gate():
        from modules.security_system.tool_security_gate import get_tool_security_gate
        return get_tool_security_gate

    # ── 回合图片（多模态直连）──
    def _turn_images():
        from modules.thinking.turn_images import get_turn_images, clear_turn_images
        return lambda: (get_turn_images, clear_turn_images)

    # ── 上下文控制器（统一 token 计数口径）──
    def _context_controller():
        from modules.thinking.context.controller import _default_context_controller
        return _default_context_controller

    # ── 事件存储（记忆持久层）──
    def _event_store():
        from modules.memory.event_store import EventStore
        return EventStore._builtin_instance

    # ── 因果图（记忆因果模型）──
    def _causal_graph():
        from modules.memory.causal_graph import CausalGraph
        return CausalGraph._builtin_instance

    # ── 知识库（文档 RAG，独立于事件记忆）──
    def _knowledge_base():
        from modules.knowledge.service import _default_knowledge_service
        return _default_knowledge_service

    # ── 向量化引擎（记忆/知识检索共用）──
    # ``EmbeddingEngine.get_instance()`` 自己先查这条端口，所以 provider 只能指不读
    # 端口的内置实现；指回 get_instance 就是端口→它→端口的一调用即递归。
    def _embedding_engine():
        from modules.memory.embedding import EmbeddingEngine
        return EmbeddingEngine._builtin_instance


    # ── 内置单例的端口（对应 docs/SEAMS.md 的「端口访问器」行）─────────────────
    # 这一批 provider 一律指向模块里的内置实现 `_default_x`，**不**指向对外的 `get_x()`：
    # `get_x()` 自己就先经这个端口取用，端口再指回它就是 端口→get_x→端口 的一调用即
    # 递归。那种接线肉眼看不出来，由 scripts/seam_catalogue.py 在生成目录时判错。

    # ── 会话仓储 ──
    def _session_repo():
        from modules.database.session_repo import _default_session_repo
        return _default_session_repo

    # ── 模型间消息总线 ──
    def _message_bus():
        from modules.thinking.communication.message_bus import _default_message_bus
        return _default_message_bus

    # ── 感知事件总线 ──
    def _event_bus():
        from modules.perception.events.bus import _default_event_bus
        return _default_event_bus

    # ── 提示词加载器 ──
    def _prompt_loader():
        from config.prompts.loader import _default_loader
        return _default_loader

    # ── 屏幕差异检测器 ──
    def _difference_detector():
        from modules.perception.difference.detector import _default_detector
        return _default_detector

    # ── 上下文压缩引擎 ──
    def _compression_engine():
        from modules.thinking.context.compression import _default_compression_engine
        return _default_compression_engine

    # ── 身份/角色模板表 ──
    def _identities():
        from modules.thinking.identity import _default_identities
        return _default_identities

    # ── 模型实例工厂 ──
    def _model_factory():
        from modules.thinking.model_factory import _default_model_factory
        return _default_model_factory

    # ── MCP 工具服务 ──
    def _mcp_tool_service():
        from infra.mcp.factory import _default_mcp_tool_service
        return _default_mcp_tool_service

    # ── MCP server 管理器 ──
    def _mcp_server_manager():
        from infra.mcp.factory import _default_server_manager
        return _default_server_manager

    # ── 视频分析器 ──
    def _video_analyzer():
        from infra.data_process.core.video_analyzer import _default_video_analyzer
        return _default_video_analyzer

    # ── CDP 页面扫描器 ──
    def _cdp_scanner():
        from infra.data_process.core.cdp_scanner import _default_cdp_scanner
        return _default_cdp_scanner

    # ── 数据库管理器 ──
    def _db_manager():
        from modules.database.connection import _default_db_manager
        return _default_db_manager

    # ── 会话事件仓储 ──
    def _session_event_repo():
        from modules.database.session_events import _default_session_event_repo
        return _default_session_event_repo

    # ── 会话投影仓储 ──
    def _projection_repo():
        from modules.database.session_projections import _default_projection_repo
        return _default_projection_repo

    # ── 事件归并器 ──
    def _event_reducer():
        from modules.memory.event_reducer import _default_reducer
        return _default_reducer

    # ── 感知整合器 ──
    def _perception_integrator():
        from modules.perception.integration import _default_perception_integrator
        return _default_perception_integrator

    # ── 感知系统 ──
    def _perception_system():
        from modules.perception.setup import _default_perception_system
        return _default_perception_system

    # ── 存在心跳 ──
    def _heartbeat():
        from modules.perception.difference.heartbeat import _default_heartbeat
        return _default_heartbeat

    # ── 屏幕差异源 ──
    def _screen_diff_source():
        from modules.perception.difference.sources.mcp_screen_source import _default_screen_diff_source
        return _default_screen_diff_source

    # ── 安全系统 API ──
    def _security_api():
        from modules.security_system.api import _default_security_api
        return _default_security_api

    # ── 默认安全端口对象 ──
    def _security_port():
        from modules.security_system.interface import _default_security_port
        return _default_security_port

    # ── 工具权限裁决器 ──
    def _tool_permission_controller():
        from modules.security_system.tool_permission_controller import _default_tool_permission_controller
        return _default_tool_permission_controller

    # ── 自制插件存储 ──
    def _authored_plugin_store():
        from modules.shared.plugin_authoring import _default_plugin_store
        return _default_plugin_store

    # ── 流式思考系统 ──
    def _thinking_system():
        from modules.thinking.api_stream import _default_thinking_system
        return _default_thinking_system

    # ── 良知（自省叙述） ──
    def _conscience():
        from modules.thinking.conscience import _default_conscience
        return _default_conscience

    # ── 会话图谱存储 ──
    def _session_graph_store():
        from modules.thinking.session_graph import _default_session_graph_store
        return _default_session_graph_store

    # ── 探针权限管理器 ──
    def _probe_permission_manager():
        from modules.thinking.probes.probe_permission import _default_probe_permission_manager
        return _default_probe_permission_manager

    # ── 定时任务管理器 ──
    def _task_manager():
        from modules.thinking.scheduled_tasks import _default_task_manager
        return _default_task_manager

    _safe_register("blackboard_query", _blackboard_query)
    _safe_register("skill_manager", _skill_manager)
    _safe_register("event_retrieval", _event_retrieval)
    _safe_register("file_history", _file_history)
    _safe_register("touchpoint_detector", _touchpoint_detector)
    _safe_register("detector_router", _detector_router)
    _safe_register("value_formatter", _value_formatter)
    _safe_register("value_system", _value_system)
    _safe_register("tool_security_gate", _tool_security_gate)
    _safe_register("turn_images", _turn_images)
    _safe_register("context_controller", _context_controller)
    _safe_register("event_store", _event_store)
    _safe_register("causal_graph", _causal_graph)
    _safe_register("knowledge_base", _knowledge_base)
    _safe_register("embedding_engine", _embedding_engine)

    # 端口优先访问器：每个名字都对应一个 `get_x()`，插件接管它 = 换掉全部调用点。

    # ── classmethod 单例的端口 ────────────────────────────────────────────────
    # 这些类的 ``get_instance()`` 自己就先查端口（所以 82 处调用点一行没改就能被接管），
    # 内置的那份懒初始化挪进了 ``_builtin_instance``。provider 必须指它：指回
    # ``get_instance`` 就是 端口→get_instance→端口 的死循环。

    # ── 桌宠数据存储 ──
    def _pet_store():
        from modules.desktop_pet.store import PetStore
        return PetStore._builtin_instance

    # ── 桌宠记忆 ──
    def _pet_memory():
        from modules.desktop_pet.memory import PetMemory
        return PetMemory._builtin_instance

    # ── 桌宠事件记忆库 ──
    def _pet_memory_bank():
        from modules.desktop_pet.memory_bank import PetMemoryBank
        return PetMemoryBank._builtin_instance

    # ── 桌宠任务队列 ──
    def _pet_task_queue():
        from modules.desktop_pet.task_queue import PetTaskQueue
        return PetTaskQueue._builtin_instance

    # ── 桌宠引擎 ──
    def _pet_engine():
        from modules.desktop_pet.pet_engine import PetEngine
        return PetEngine._builtin_instance

    # ── 桌宠窗口守护 ──
    def _pet_window_supervisor():
        from modules.desktop_pet.window_supervisor import PetWindowSupervisor
        return PetWindowSupervisor._builtin_instance

    # ── API 调用日志存储 ──
    def _api_log_store():
        from modules.management.api_log_store import ApiLogStore
        return ApiLogStore._builtin_instance

    # ── 知识库存储 ──
    def _knowledge_store():
        from modules.knowledge.store import KnowledgeStore
        return KnowledgeStore._builtin_instance



    # ── 全局错误总线 ──
    def _error_bus():
        from infra.error_bus import _default_error_bus
        return _default_error_bus

    # ── 磁盘缓存 ──
    def _disk_cache():
        from modules.database.disk_cache import _default_disk_cache
        return _default_disk_cache

    # ── TTS 输出目录 ──
    def _tts_output_dir():
        from modules.output_system.tts import _default_tts_output_dir
        return _default_tts_output_dir

    # ── 防休眠（caffeinate 起停 + 子进程句柄）──
    def _prevent_sleep():
        from utils.power import _default_prevent_sleep
        return _default_prevent_sleep

    # ── 外部身份刷新 ──
    def _external_identities():
        from modules.thinking.identity import _default_external_identities
        return _default_external_identities

    # ── 对话历史读取条数上限 ──
    def _context_history_read_limit():
        from modules.thinking.context.dialog_memory import _default_history_read_limit
        return _default_history_read_limit

    _safe_register("session_repo", _session_repo)
    _safe_register("message_bus", _message_bus)
    _safe_register("event_bus", _event_bus)
    _safe_register("prompt_loader", _prompt_loader)
    _safe_register("difference_detector", _difference_detector)
    _safe_register("compression_engine", _compression_engine)
    _safe_register("identities", _identities)
    _safe_register("model_factory", _model_factory)
    _safe_register("mcp_tool_service", _mcp_tool_service)
    _safe_register("mcp_server_manager", _mcp_server_manager)
    _safe_register("video_analyzer", _video_analyzer)
    _safe_register("cdp_scanner", _cdp_scanner)
    _safe_register("db_manager", _db_manager)
    _safe_register("session_event_repo", _session_event_repo)
    _safe_register("projection_repo", _projection_repo)
    _safe_register("event_reducer", _event_reducer)
    _safe_register("perception_integrator", _perception_integrator)
    _safe_register("perception_system", _perception_system)
    _safe_register("heartbeat", _heartbeat)
    _safe_register("screen_diff_source", _screen_diff_source)
    _safe_register("security_api", _security_api)
    _safe_register("security_port", _security_port)
    _safe_register("tool_permission_controller", _tool_permission_controller)
    _safe_register("authored_plugin_store", _authored_plugin_store)
    _safe_register("thinking_system", _thinking_system)
    _safe_register("conscience", _conscience)
    _safe_register("session_graph_store", _session_graph_store)
    _safe_register("probe_permission_manager", _probe_permission_manager)
    _safe_register("task_manager", _task_manager)

    _safe_register("pet_store", _pet_store)
    _safe_register("pet_memory", _pet_memory)
    _safe_register("pet_memory_bank", _pet_memory_bank)
    _safe_register("pet_task_queue", _pet_task_queue)
    _safe_register("pet_engine", _pet_engine)
    _safe_register("pet_window_supervisor", _pet_window_supervisor)
    _safe_register("api_log_store", _api_log_store)
    _safe_register("knowledge_store", _knowledge_store)

    _safe_register("error_bus", _error_bus)
    _safe_register("disk_cache", _disk_cache)
    _safe_register("tts_output_dir", _tts_output_dir)
    _safe_register("prevent_sleep", _prevent_sleep)
    _safe_register("context_history_read_limit", _context_history_read_limit)
    _safe_register("external_identities", _external_identities)

    _report_capability_status([
        "blackboard_query", "skill_manager", "event_retrieval", "file_history",
        "touchpoint_detector", "detector_router", "value_formatter", "value_system",
        "tool_security_gate", "turn_images",
        "context_controller", "event_store", "causal_graph", "knowledge_base",
        "embedding_engine",
        "session_repo", "message_bus", "event_bus", "prompt_loader", "difference_detector", "compression_engine", "identities", "model_factory", "mcp_tool_service", "mcp_server_manager", "video_analyzer", "cdp_scanner", "db_manager", "session_event_repo", "projection_repo", "event_reducer", "perception_integrator", "perception_system", "heartbeat", "screen_diff_source", "security_api", "security_port", "tool_permission_controller", "authored_plugin_store", "thinking_system", "conscience", "session_graph_store", "probe_permission_manager", "task_manager",
        "pet_store", "pet_memory", "pet_memory_bank", "pet_task_queue", "pet_engine", "pet_window_supervisor", "api_log_store", "knowledge_store", "error_bus", "disk_cache", "tts_output_dir", "context_history_read_limit", "prevent_sleep", "external_identities",
    ])

    # 触发探针工具注册（原在 infra/tool_manager/tools/__init__.py 随工具集加载；
    # 移至装配层使 infra 不再依赖 modules.thinking.probes）
    try:
        import modules.thinking.probes.probe_tools  # noqa: F401  触发 @ToolRegistry.register
    except Exception as e:
        logger.warning(f"[bootstrap] 探针工具注册失败: {e}")

    # 触发模型面自制插件工具注册（mount_plugin / unmount_plugin / list_plugins /
    # delete_plugin）。同上一条：装饰器自注册是 import 副作用，只能由装配层触发，
    # 否则 infra 要反过来 import modules。放在恢复 AI 自创工具之前——这几个是内置
    # 声明自己槽位的注册，走的是同名即报错那条，先后顺序不影响它们，但装配层里
    # 「先给模型长出入口，再把上一次自创的东西搬回来」读起来才是启动的真实依赖。
    try:
        import modules.shared.plugin_authoring_tools  # noqa: F401  触发 @ToolRegistry.register
    except Exception as e:
        logger.warning(f"[bootstrap] 自制插件工具注册失败: {e}")

    # 恢复 AI 自创工具（原也挂在 tools/__init__.py 的 import 上）。这一句必须排在
    # 工具包 import 之后：恢复走的是 register_tool（同名覆盖），内置先就位才不会
    # 出现「自创工具先占了名字、内置随后被 DuplicateToolError 拒掉」的顺序依赖。
    from infra.tool_manager.tools import ai_tools

    try:
        ai_tools.restore_ai_tools()
    except Exception as e:
        logger.warning(f"[bootstrap] AI 自创工具恢复失败: {e}")
