"""
思维模块

活跃子模块：
- core/: ContinuousThinker, DelegationPort, ThinkingProcessCollector
- cognition/: CognitiveBlackboard, ContextSlicer
- communication/: ModelMessageBus, MessageBusPort
- runtime_expert: RuntimeExpert 常驻专家基类
- conscience: 良知系统（值演化在 config/values_store.py）
- probes/: 探针系统
- skills/: YAML 技能系统
- context/: 上下文池化与压缩
- model_factory: ModelInstanceFactory 统一模型入口

API 入口：
- api_stream.py: StreamThinkingSystem (WebSocket + SSE)
"""
