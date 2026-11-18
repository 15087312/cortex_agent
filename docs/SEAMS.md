# 缝隙目录（生成物，别手改）

由 `scripts/seam_catalogue.py` 扫描 `modules, infra, config, api, cortex, cli_tui, utils, tools` 得到。
重跑：`python scripts/seam_catalogue.py --out docs/SEAMS.md`；漂移检查：`--check`。

本项目不做插件安全，所以「插件能碰到什么」没有声明可查——这份表是从源码的
import/调用关系算出来的，不是人写的。它按「今天能不能真的接管」分三档：

- 端口 73 个（业务 58 / 门面 15）：有人经端口取用 73 个，**死端口 0 个**
- 候选缝隙 182 处：单例访问器 64 处，端口优先访问器 76 处，端口包装的对象 39 处，import 期快照 4 处
- ✅ 已可接管（端口在用、无人硬 import）：**137 处**
- ❌ 还没有端口、又被别的文件硬 import：**2 处**
- ⚠️ 有端口但代码**行为上**绕过它（只接了一半）：**2 处**
- 只在类型标注里点名、行为不绕过的：3 处（见文末附表）

## 端口

| 端口 | 体系 | provider | 包装的东西 | 经端口取用的文件 | 状态 |
|---|---|---|---|---|---|
| `api_log_store` | 业务 | `_api_log_store` | （对象）`modules.management.api_log_store:ApiLogStore`, （对象）`modules.management.api_log_store:ApiLogStore._builtin_instance` | `modules/management/api_log_store.py` | 在用 |
| `authored_plugin_store` | 业务 | `_authored_plugin_store` | `modules.shared.plugin_authoring:_default_plugin_store` | `modules/shared/plugin_authoring.py` | 在用 |
| `blackboard_query` | 业务 | `_blackboard_query` | （对象）`modules.database.blackboard_repo:query_observations` | `infra/tool_manager/tools/blackboard_history.py` | 在用 |
| `causal_graph` | 业务 | `_causal_graph` | （对象）`modules.memory.causal_graph:CausalGraph`, （对象）`modules.memory.causal_graph:CausalGraph._builtin_instance` | `infra/tool_manager/tools/causal_graph_edit.py`, `modules/memory/causal_graph.py` | 在用 |
| `cdp_scanner` | 业务 | `_cdp_scanner` | `infra.data_process.core.cdp_scanner:_default_cdp_scanner` | `infra/data_process/core/cdp_scanner.py` | 在用 |
| `compression_engine` | 业务 | `_compression_engine` | `modules.thinking.context.compression:_default_compression_engine` | `modules/thinking/context/compression.py` | 在用 |
| `conscience` | 业务 | `_conscience` | `modules.thinking.conscience:_default_conscience` | `modules/thinking/conscience.py` | 在用 |
| `context_controller` | 业务 | `_context_controller` | `modules.thinking.context.controller:_default_context_controller` | `infra/tool_manager/context_budget.py`, `modules/thinking/context/controller.py` | 在用 |
| `context_history_read_limit` | 业务 | `_context_history_read_limit` | `modules.thinking.context.dialog_memory:_default_history_read_limit` | `modules/thinking/context/dialog_memory.py` | 在用 |
| `db_manager` | 业务 | `_db_manager` | `modules.database.connection:_default_db_manager` | `modules/database/connection.py` | 在用 |
| `detector_router` | 业务 | `_detector_router` | `modules.perception.screen.router:get_detector_router` | `infra/tool_manager/tools/perception_tools.py` | 在用 |
| `difference_detector` | 业务 | `_difference_detector` | `modules.perception.difference.detector:_default_detector` | `modules/perception/difference/detector.py` | 在用 |
| `disk_cache` | 业务 | `_disk_cache` | `modules.database.disk_cache:_default_disk_cache` | `modules/database/disk_cache.py` | 在用 |
| `embedding_engine` | 业务 | `_embedding_engine` | （对象）`modules.memory.embedding:EmbeddingEngine`, （对象）`modules.memory.embedding:EmbeddingEngine._builtin_instance` | `modules/memory/embedding.py` | 在用 |
| `error_bus` | 业务 | `_error_bus` | `infra.error_bus:_default_error_bus` | `infra/error_bus.py` | 在用 |
| `event_bus` | 业务 | `_event_bus` | `modules.perception.events.bus:_default_event_bus` | `modules/perception/events/bus.py` | 在用 |
| `event_reducer` | 业务 | `_event_reducer` | `modules.memory.event_reducer:_default_reducer` | `modules/memory/event_reducer.py` | 在用 |
| `event_retrieval` | 业务 | `_event_retrieval` | （对象）`modules.memory.event_retrieval:_default_event_retrieval` | `infra/tool_manager/tools/event_query.py`, `modules/memory/event_retrieval.py` | 在用 |
| `event_store` | 业务 | `_event_store` | （对象）`modules.memory.event_store:EventStore`, （对象）`modules.memory.event_store:EventStore._builtin_instance` | `infra/tool_manager/tools/causal_graph_edit.py`, `modules/memory/event_store.py` | 在用 |
| `external_identities` | 业务 | `_external_identities` | （对象）`modules.thinking.identity:_default_external_identities` | `modules/thinking/identity.py` | 在用 |
| `file_history` | 业务 | `_file_history` | （对象）`modules.cortex.file_history:_default_file_history` | `infra/tool_manager/tools/file_history_tools.py`, `modules/cortex/file_history.py` | 在用 |
| `heartbeat` | 业务 | `_heartbeat` | `modules.perception.difference.heartbeat:_default_heartbeat` | `modules/perception/difference/heartbeat.py` | 在用 |
| `identities` | 业务 | `_identities` | `modules.thinking.identity:_default_identities` | `modules/thinking/identity.py` | 在用 |
| `knowledge_base` | 业务 | `_knowledge_base` | `modules.knowledge.service:_default_knowledge_service` | `infra/tool_manager/tools/knowledge_tools.py`, `modules/knowledge/service.py` | 在用 |
| `knowledge_store` | 业务 | `_knowledge_store` | （对象）`modules.knowledge.store:KnowledgeStore`, （对象）`modules.knowledge.store:KnowledgeStore._builtin_instance` | `modules/knowledge/store.py` | 在用 |
| `mcp_server_manager` | 业务 | `_mcp_server_manager` | `infra.mcp.factory:_default_server_manager` | `infra/mcp/factory.py` | 在用 |
| `mcp_tool_service` | 业务 | `_mcp_tool_service` | `infra.mcp.factory:_default_mcp_tool_service` | `infra/mcp/factory.py` | 在用 |
| `message_bus` | 业务 | `_message_bus` | `modules.thinking.communication.message_bus:_default_message_bus` | `modules/thinking/communication/message_bus.py` | 在用 |
| `model_factory` | 业务 | `_model_factory` | `modules.thinking.model_factory:_default_model_factory` | `modules/thinking/model_factory.py` | 在用 |
| `perception_integrator` | 业务 | `_perception_integrator` | `modules.perception.integration:_default_perception_integrator` | `modules/perception/integration.py` | 在用 |
| `perception_system` | 业务 | `_perception_system` | `modules.perception.setup:_default_perception_system` | `modules/perception/setup.py` | 在用 |
| `pet_engine` | 业务 | `_pet_engine` | （对象）`modules.desktop_pet.pet_engine:PetEngine`, （对象）`modules.desktop_pet.pet_engine:PetEngine._builtin_instance` | `modules/desktop_pet/pet_engine.py` | 在用 |
| `pet_memory` | 业务 | `_pet_memory` | （对象）`modules.desktop_pet.memory:PetMemory`, （对象）`modules.desktop_pet.memory:PetMemory._builtin_instance` | `modules/desktop_pet/memory.py` | 在用 |
| `pet_memory_bank` | 业务 | `_pet_memory_bank` | （对象）`modules.desktop_pet.memory_bank:PetMemoryBank`, （对象）`modules.desktop_pet.memory_bank:PetMemoryBank._builtin_instance` | `modules/desktop_pet/memory_bank.py` | 在用 |
| `pet_store` | 业务 | `_pet_store` | （对象）`modules.desktop_pet.store:PetStore`, （对象）`modules.desktop_pet.store:PetStore._builtin_instance` | `modules/desktop_pet/store.py` | 在用 |
| `pet_task_queue` | 业务 | `_pet_task_queue` | （对象）`modules.desktop_pet.task_queue:PetTaskQueue`, （对象）`modules.desktop_pet.task_queue:PetTaskQueue._builtin_instance` | `modules/desktop_pet/task_queue.py` | 在用 |
| `pet_window_supervisor` | 业务 | `_pet_window_supervisor` | （对象）`modules.desktop_pet.window_supervisor:PetWindowSupervisor`, （对象）`modules.desktop_pet.window_supervisor:PetWindowSupervisor._builtin_instance` | `modules/desktop_pet/window_supervisor.py` | 在用 |
| `prevent_sleep` | 业务 | `_prevent_sleep` | `utils.power:_default_prevent_sleep` | `utils/power.py` | 在用 |
| `probe_permission_manager` | 业务 | `_probe_permission_manager` | `modules.thinking.probes.probe_permission:_default_probe_permission_manager` | `modules/thinking/probes/probe_permission.py` | 在用 |
| `projection_repo` | 业务 | `_projection_repo` | `modules.database.session_projections:_default_projection_repo` | `modules/database/session_projections.py` | 在用 |
| `prompt_loader` | 业务 | `_prompt_loader` | `config.prompts.loader:_default_loader` | `config/prompts/loader.py` | 在用 |
| `screen_diff_source` | 业务 | `_screen_diff_source` | `modules.perception.difference.sources.mcp_screen_source:_default_screen_diff_source` | `modules/perception/difference/sources/mcp_screen_source.py` | 在用 |
| `security_api` | 业务 | `_security_api` | `modules.security_system.api:_default_security_api` | `modules/security_system/api.py` | 在用 |
| `security_port` | 业务 | `_security_port` | `modules.security_system.interface:_default_security_port` | `modules/security_system/interface.py` | 在用 |
| `session_event_repo` | 业务 | `_session_event_repo` | `modules.database.session_events:_default_session_event_repo` | `modules/database/session_events.py` | 在用 |
| `session_graph_store` | 业务 | `_session_graph_store` | `modules.thinking.session_graph:_default_session_graph_store` | `modules/thinking/session_graph.py` | 在用 |
| `session_repo` | 业务 | `_session_repo` | `modules.database.session_repo:_default_session_repo` | `modules/database/session_repo.py` | 在用 |
| `skill_manager` | 业务 | `_skill_manager` | （对象）`modules.thinking.skills:_default_skill_manager` | `infra/tool_manager/tools/create_skill.py`, `infra/tool_manager/tools/skill_tools.py`, `modules/thinking/chat_light/continuous_thinker.py`, `modules/thinking/skills/__init__.py` | 在用 |
| `task_manager` | 业务 | `_task_manager` | `modules.thinking.scheduled_tasks:_default_task_manager` | `modules/thinking/scheduled_tasks.py` | 在用 |
| `thinking_system` | 业务 | `_thinking_system` | `modules.thinking.api_stream:_default_thinking_system` | `modules/thinking/api_stream.py` | 在用 |
| `tool_permission_controller` | 业务 | `_tool_permission_controller` | `modules.security_system.tool_permission_controller:_default_tool_permission_controller` | `modules/security_system/tool_permission_controller.py` | 在用 |
| `tool_security_gate` | 业务 | `_tool_security_gate` | `modules.security_system.tool_security_gate:get_tool_security_gate` | `infra/tool_manager/api.py` | 在用 |
| `touchpoint_detector` | 业务 | `_touchpoint_detector` | （对象）`modules.perception.detectors.touchpoint_detector:TouchpointDetector` | `infra/tool_manager/tools/list_windows.py`, `infra/tool_manager/tools/open_app.py` | 在用 |
| `tts_output_dir` | 业务 | `_tts_output_dir` | `modules.output_system.tts:_default_tts_output_dir` | `modules/output_system/tts.py` | 在用 |
| `turn_images` | 业务 | `_turn_images` | （对象）`modules.thinking.turn_images:clear_turn_images`, （对象）`modules.thinking.turn_images:get_turn_images` | `infra/model/large_model_client.py` | 在用 |
| `value_formatter` | 业务 | `_value_formatter` | （对象）`config.values_store:get_value_system`, （对象）`modules.thinking.value_formatter:ValueFormatter` | `infra/tool_manager/tools/value_tools.py` | 在用 |
| `value_system` | 业务 | `_value_system` | `config.values_store:_default_value_system` | `config/values_store.py` | 在用 |
| `video_analyzer` | 业务 | `_video_analyzer` | `infra.data_process.core.video_analyzer:_default_video_analyzer` | `infra/data_process/core/video_analyzer.py` | 在用 |
| `perception.activity_notifier` | 门面 | `?` | （对象）`modules.perception:_get_notifier` | `modules/thinking/adapters.py` | 在用 |
| `perception.integrator` | 门面 | `?` | （对象）`modules.perception:_get_integrator` | `modules/thinking/context/sources/perception_source.py` | 在用 |
| `perception.manager` | 门面 | `?` | （对象）`modules.perception:_get_compat_proxy` | `modules/management/api.py` | 在用 |
| `perception.system` | 门面 | `?` | （对象）`modules.perception:_builtin_perception_system` | `modules/management/api.py`, `modules/management/core/interfaces.py`, `modules/perception/__init__.py` | 在用 |
| `security.api` | 门面 | `provider` | — | `infra/security_gate.py` | 在用 |
| `security.audit_logger` | 门面 | `SecurityAuditLogger` | — | `modules/management/api.py` | 在用 |
| `security.identities` | 门面 | `provider, ?` | （对象）`modules.thinking.identity:_IdentityProvider` | `infra/security_gate.py` | 在用 |
| `security.permission_controller` | 门面 | `provider` | — | `infra/security_gate.py` | 在用 |
| `security.port` | 门面 | `?` | — | `modules/management/core/interfaces.py` | 在用 |
| `security.tool_gate` | 门面 | `provider` | — | `infra/security_gate.py` | 在用 |
| `security.validators` | 门面 | `?` | — | `modules/output_system/core.py` | 在用 |
| `thinking.control_tools` | 门面 | `?` | （对象）`modules.thinking.core.control_tools:_ControlToolsProvider` | `infra/security_gate.py` | 在用 |
| `thinking.model_factory` | 门面 | `provider, ?` | （对象）`modules.thinking.model_factory:_FactoryProvider` | `infra/security_gate.py` | 在用 |
| `security.ai_narrowing` | 门面 | `(没检测到登记点)` | — | `infra/security_gate.py` | 在用 |
| `security.delegate_write_keywords` | 门面 | `(没检测到登记点)` | — | `infra/security_gate.py` | 在用 |

「体系」说的是这条端口由哪套注册表管：**业务** = bootstrap 调
`register_capability` 填的蛇形名端口（`ctx.provide_capability` 直接接管）；**门面** = 提供方模块在自己 import 期调 `infra/ports.py::register` 登记的点号名
端口（`security.gate` 这种横切契约）。两套的接管入口不同，但「换掉实现」这件事
一样成立，所以判据（在用/死端口/包装了什么/谁绕过）共用同一套算法，合成一张表。
分表列会让「业务端口全部可接管」被读成「全部端口可接管」，而门面那一半本来
不在任何表里。

「在用」说的是**有文件真的 ``get_capability(name)``**。这只说明有人经端口取，
不等于接管生效——生效还要求同一条路径上没人硬 import 原实现，所以下面按
「硬 import 读者数」再分档。

「（对象）」是 provider 体里引用到、但不算单例访问器的符号（模块级实例、类、
工具函数）。它们同样被这条端口包着，接管端口就会换掉它们，所以也是一等候选
缝隙；只算访问器会漏掉整类接管点。

**端口优先访问器**（下表标「端口访问器」的）是降级完成后的标准形状：函数自己
先取端口（业务端口 ``get_capability(name)``，门面端口 ``ports.resolve(name)``），
取不到才回退内置。调用点全都写在它身上，所以它的
调用点**不算绕过端口**（接管端口就换掉了它们），单独按「调用点」计数列出。
它的 provider 必须是内置实现而不是它自己——否则端口→访问器→端口，第一次调用就
无限递归；那种接线本工具直接报错，不写进表里。

## ✅ 一、已可接管

端口注册了、有人经端口取用、且没有任何别的文件**取用**这个符号（只在类型标注
里点名不算取用，那类单列在文末附表）。
插件挂上去这条路径的行为就整个换掉：业务端口用 ``ctx.provide_capability(name, ...)``，门面（点号名）端口用 ``ctx.provide_port(name, ...)``——用哪个由上面端口表的
「体系」那一列决定。

| 符号 | 种类 | 端口 | 调用点 | 标注引用 |
|---|---|---|---|---|
| `config.prompts.loader:_default_loader` | 访问器 | `prompt_loader` | — | — |
| `config.prompts.loader:get_loader` | 端口访问器 | `prompt_loader` | 5 | — |
| `config.values_store:_default_value_system` | 访问器 | `value_system` | — | — |
| `config.values_store:get_value_system` | 端口访问器 | `value_formatter` | 1 | — |
| `infra.data_process.core.cdp_scanner:_default_cdp_scanner` | 访问器 | `cdp_scanner` | — | — |
| `infra.data_process.core.cdp_scanner:get_cdp_scanner` | 端口访问器 | `cdp_scanner` | 1 | — |
| `infra.data_process.core.video_analyzer:_default_video_analyzer` | 访问器 | `video_analyzer` | — | — |
| `infra.data_process.core.video_analyzer:get_video_analyzer` | 端口访问器 | `video_analyzer` | 2 | — |
| `infra.error_bus:_default_error_bus` | 访问器 | `error_bus` | — | — |
| `infra.error_bus:get_error_bus` | 端口访问器 | `error_bus` | 2 | — |
| `infra.mcp.factory:_default_mcp_tool_service` | 访问器 | `mcp_tool_service` | — | — |
| `infra.mcp.factory:_default_server_manager` | 访问器 | `mcp_server_manager` | — | — |
| `infra.mcp.factory:get_mcp_tool_service` | 端口访问器 | `mcp_tool_service` | 4 | — |
| `infra.mcp.factory:get_server_manager` | 端口访问器 | `mcp_server_manager` | 1 | — |
| `infra.security_gate:get_control_tools` | 端口访问器 | `thinking.control_tools` | 1 | — |
| `infra.security_gate:get_identities` | 端口访问器 | `security.identities` | 1 | — |
| `infra.security_gate:get_model_factory` | 端口访问器 | `thinking.model_factory` | 1 | — |
| `infra.security_gate:get_model_permissions_cls` | 端口访问器 | `security.identities` | 1 | — |
| `infra.security_gate:get_security_api` | 端口访问器 | `security.api` | 1 | — |
| `infra.security_gate:get_tool_permission_controller` | 端口访问器 | `security.permission_controller` | 3 | — |
| `infra.security_gate:get_tool_security_gate` | 端口访问器 | `security.tool_gate` | 3 | — |
| `infra.security_gate:get_tool_security_gate_cls` | 端口访问器 | `security.tool_gate` | 2 | — |
| `modules.cortex.file_history:_default_file_history` | 端口包装 | `file_history` | — | — |
| `modules.cortex.file_history:get_file_history` | 端口访问器 | `file_history` | 1 | — |
| `modules.database.blackboard_repo:query_observations` | 端口包装 | `blackboard_query` | — | — |
| `modules.database.connection:_default_db_manager` | 访问器 | `db_manager` | — | — |
| `modules.database.connection:get_db_manager` | 端口访问器 | `db_manager` | 5 | — |
| `modules.database.disk_cache:_default_disk_cache` | 访问器 | `disk_cache` | — | — |
| `modules.database.disk_cache:get_disk_cache` | 端口访问器 | `disk_cache` | 2 | — |
| `modules.database.session_events:_default_session_event_repo` | 访问器 | `session_event_repo` | — | — |
| `modules.database.session_events:get_session_event_repo` | 端口访问器 | `session_event_repo` | 3 | — |
| `modules.database.session_projections:_default_projection_repo` | 访问器 | `projection_repo` | — | — |
| `modules.database.session_projections:get_projection_repo` | 端口访问器 | `projection_repo` | 1 | — |
| `modules.database.session_repo:_default_session_repo` | 访问器 | `session_repo` | — | — |
| `modules.database.session_repo:get_session_repo` | 端口访问器 | `session_repo` | 8 | — |
| `modules.desktop_pet.memory:PetMemory` | 端口包装 | `pet_memory` | — | — |
| `modules.desktop_pet.memory:PetMemory._builtin_instance` | 端口包装 | `pet_memory` | — | — |
| `modules.desktop_pet.memory:PetMemory.get_instance` | 端口访问器 | `pet_memory` | 1 | — |
| `modules.desktop_pet.memory_bank:PetMemoryBank` | 端口包装 | `pet_memory_bank` | — | — |
| `modules.desktop_pet.memory_bank:PetMemoryBank._builtin_instance` | 端口包装 | `pet_memory_bank` | — | — |
| `modules.desktop_pet.memory_bank:PetMemoryBank.get_instance` | 端口访问器 | `pet_memory_bank` | 2 | — |
| `modules.desktop_pet.pet_engine:PetEngine` | 端口包装 | `pet_engine` | — | — |
| `modules.desktop_pet.pet_engine:PetEngine._builtin_instance` | 端口包装 | `pet_engine` | — | — |
| `modules.desktop_pet.pet_engine:PetEngine.get_instance` | 端口访问器 | `pet_engine` | 2 | — |
| `modules.desktop_pet.pet_engine:get_running_pet_engine` | 端口访问器 | `pet_engine` | 1 | — |
| `modules.desktop_pet.store:PetStore` | 端口包装 | `pet_store` | — | 1 |
| `modules.desktop_pet.store:PetStore._builtin_instance` | 端口包装 | `pet_store` | — | — |
| `modules.desktop_pet.store:PetStore.get_instance` | 端口访问器 | `pet_store` | 3 | — |
| `modules.desktop_pet.task_queue:PetTaskQueue` | 端口包装 | `pet_task_queue` | — | — |
| `modules.desktop_pet.task_queue:PetTaskQueue._builtin_instance` | 端口包装 | `pet_task_queue` | — | — |
| `modules.desktop_pet.task_queue:PetTaskQueue.get_instance` | 端口访问器 | `pet_task_queue` | 1 | — |
| `modules.desktop_pet.window_supervisor:PetWindowSupervisor` | 端口包装 | `pet_window_supervisor` | — | — |
| `modules.desktop_pet.window_supervisor:PetWindowSupervisor._builtin_instance` | 端口包装 | `pet_window_supervisor` | — | — |
| `modules.knowledge.service:_default_knowledge_service` | 访问器 | `knowledge_base` | — | — |
| `modules.knowledge.service:get_knowledge_service` | 端口访问器 | `knowledge_base` | 3 | — |
| `modules.knowledge.store:KnowledgeStore` | 端口包装 | `knowledge_store` | — | 3 |
| `modules.knowledge.store:KnowledgeStore._builtin_instance` | 端口包装 | `knowledge_store` | — | — |
| `modules.knowledge.store:KnowledgeStore.get_instance` | 端口访问器 | `knowledge_store` | 3 | — |
| `modules.management.api_log_store:ApiLogStore` | 端口包装 | `api_log_store` | — | — |
| `modules.management.api_log_store:ApiLogStore._builtin_instance` | 端口包装 | `api_log_store` | — | — |
| `modules.management.api_log_store:ApiLogStore.get_instance` | 端口访问器 | `api_log_store` | 2 | — |
| `modules.memory.causal_graph:CausalGraph._builtin_instance` | 端口包装 | `causal_graph` | — | — |
| `modules.memory.causal_graph:CausalGraph.get_instance` | 端口访问器 | `causal_graph` | 6 | — |
| `modules.memory.embedding:EmbeddingEngine` | 端口包装 | `embedding_engine` | — | 2 |
| `modules.memory.embedding:EmbeddingEngine._builtin_instance` | 端口包装 | `embedding_engine` | — | — |
| `modules.memory.embedding:get_embedding_engine` | 端口访问器 | `embedding_engine` | 9 | — |
| `modules.memory.event_reducer:_default_reducer` | 访问器 | `event_reducer` | — | — |
| `modules.memory.event_reducer:get_reducer` | 端口访问器 | `event_reducer` | 2 | — |
| `modules.memory.event_retrieval:EventRetrieval.get_instance` | 端口访问器 | `event_retrieval` | 2 | — |
| `modules.memory.event_retrieval:_default_event_retrieval` | 端口包装 | `event_retrieval` | — | — |
| `modules.memory.event_retrieval:get_event_retrieval` | 端口访问器 | `event_retrieval` | 3 | — |
| `modules.memory.event_store:EventStore._builtin_instance` | 端口包装 | `event_store` | — | — |
| `modules.memory.event_store:EventStore.get_instance` | 端口访问器 | `event_store` | 9 | — |
| `modules.output_system.tts:_default_tts_output_dir` | 访问器 | `tts_output_dir` | — | — |
| `modules.output_system.tts:get_tts_output_dir` | 端口访问器 | `tts_output_dir` | 1 | — |
| `modules.perception.detectors.touchpoint_detector:TouchpointDetector` | 端口包装 | `touchpoint_detector` | — | — |
| `modules.perception.difference.detector:_default_detector` | 访问器 | `difference_detector` | — | — |
| `modules.perception.difference.detector:get_detector` | 端口访问器 | `difference_detector` | 5 | — |
| `modules.perception.difference.heartbeat:_default_heartbeat` | 访问器 | `heartbeat` | — | — |
| `modules.perception.difference.heartbeat:get_heartbeat` | 端口访问器 | `heartbeat` | 2 | — |

（本表列前 80 行，共 137 处。）

## ❌ 二、还没有端口的特权核心

直接 ``from x import get_y`` 就引用它的文件数（= 硬 import 的读者）。这些调用点
插件接管不到：得先把访问器降级成端口、把读者改成经端口取，切换才成立。
按读者数降序：读者越多，改成经端口取的代价越大，也越说明这一处不该指望插件接管。

| 访问器 | 文件 | 硬 import 的读者 |
|---|---|---|
| `modules.memory.event_reducer:get_memory_model_client` | `modules/memory/event_reducer.py` | `modules/desktop_pet/memory_bank.py`, `modules/thinking/api_stream.py`, `modules/thinking/chat_light/continuous_thinker.py` |
| `modules.thinking.core.workflow_checkpoint:get_checkpoint_manager` | `modules/thinking/core/workflow_checkpoint.py` | `modules/thinking/core/model_runner.py` |

（本表列前 60 行，共 2 处；完整数据用 `--json` 取。）

## ⚠️ 三、有端口、但代码绕过它的（只接了一半）

端口注册了，也确实有人经端口取；可是同一个符号还有别的文件在硬 import。
这种地方最容易被当成已经可切换：插件接管端口后，经端口的路径换了，硬 import
的那几处还在用原实现——同一个能力跑出两套行为，比完全没有端口更难查。

硬 import 本身不一定是错（例如按人设各自 new 一个实例、切换数据库时重置单例）；
这张表只说明「接管端口换不掉这些调用点」，要不要让它们也走端口是设计判断。

「引用」这里只算**取用**：调用它、读它的属性、把它递出去。只在类型标注里出现
（``def _get_embedder(self) -> EmbeddingEngine``、``store: PetStore = None``）不算
这一档——标注是文档，接管端口后返回的对象未必是那个类的实例，名字就此失真，但
行为没有第二套。那类单列在文末附表，改不改是命名判断，不是接线缺口。

标「import 期快照」的那几行是另一类绕过，也是最难看出来的一类：模块级写
``db_manager = get_db_manager()``，实现被抓在 import 那一刻，之后接管端口换不
掉这个变量——它长得和普通模块变量一模一样。修法是把调用点改回取访问器。

| 符号 | 种类 | 端口 | 仍在硬 import 的文件 | 仅标注引用 |
|---|---|---|---|---|
| `modules.memory.causal_graph:CausalGraph` | 端口包装 | `causal_graph` | `config/settings.py`, `modules/desktop_pet/memory_bank.py` | 2 |
| `modules.memory.event_store:EventStore` | 端口包装 | `event_store` | `config/settings.py`, `modules/desktop_pet/memory_bank.py` | 3 |

（本表列前 60 行，共 2 处。）

## 附、名字写死在类型标注里的

这些符号**行为上已经可切换**（上面的档里没它们），但文件里把内置实现的类名写死
在标注上。插件接管后返回的对象通常不是那个类的实例，标注就成了假话：读代码的人
会被它误导，静态检查也会报不匹配。要修的是名字（改成协议类型或放宽标注），不是接线。

| 符号 | 端口 | 把名字写死在该文件标注里的 |
|---|---|---|
| `modules.desktop_pet.store:PetStore` | `pet_store` | `modules/desktop_pet/memory.py` |
| `modules.knowledge.store:KnowledgeStore` | `knowledge_store` | `modules/knowledge/ingestor.py`, `modules/knowledge/retrieval.py`, `modules/knowledge/service.py` |
| `modules.memory.embedding:EmbeddingEngine` | `embedding_engine` | `modules/memory/event_reducer.py`, `modules/memory/event_retrieval.py` |

（本表列前 60 行，共 3 处。）

其余 41 处不进表：没有硬 import 的读者，
也没有「在用的端口」——要么是模块内部自己的封装，要么端口注册了却没人经端口取。
口径限制见生成器 docstring：端口名要写成字面量；第三方、局部同名遮蔽等
解析不出来源的引用不算读者。
