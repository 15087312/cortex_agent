"""
思维模块

活跃子模块：
- core/: ContinuousThinker, model_manager
- cognition/: CognitiveBlackboard, ContextSlicer, SessionLifecycle, DomainEvent
- communication/: ModelMessageBus, MessageBusPort
- experts/: PreGenExpertPipeline (小模型驱动大模型 prompt 引导，含 EmotionExpert)
- evolution/: SelfReflection, ValueSystem
- probes/: 探针系统 (5 类探针)
- integration/: PerceptionThinkIntegrator
- skills/: YAML 技能系统 (角色+规章+流程)
- session/: 层级会话管理 (主会话+子会话)

API 入口：
- api_stream.py: StreamThinkingSystem (WebSocket + SSE)
"""
