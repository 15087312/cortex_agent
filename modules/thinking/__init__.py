"""
思维模块

活跃子模块：
- core/: ContinuousThinker, model_manager
- cognition/: CognitiveBlackboard, ContextSlicer, SessionLifecycle, DomainEvent
- communication/: ModelMessageBus, MessageBusPort
- runtime_expert: RuntimeExpert 常驻专家基类
- conscience（已迁移到 config/conscience.py）: 良知系统
- evolution/: ValueSystem
- probes/: 探针系统 (5 类探针)
- integration/: empty (placeholder)
- skills/: YAML 技能系统 (角色+规章+流程)
- session/: 层级会话管理 (主会话+子会话)

API 入口：
- api_stream.py: StreamThinkingSystem (WebSocket + SSE)
"""
