"""
思维模块 — 多模型认知架构

核心执行模型：双层 ReAct 循环

  思考轮次 (Round): ContinuousThinker.continuous_think()
     每轮重建 prompt，注入黑板状态、记忆检索、感知上下文
     └─ 工具循环 (Turn): ModelRunner._generate_with_tools()
         在同一个上下文窗口中连续执行工具调用链
         chat → tool_calls → execute → 结果注入 → next chat
         直到模型输出文本结束

  会话循环: ModelRunner._think_loop() — 跨委托的 while 循环
     发出委托 → 退出思考 → 等待唤醒 → 重建 prompt → 新一轮思考

子模块：
- core/: ContinuousThinker, ModelRunner, DelegationPort, ControlTools
- cognition/: CognitiveBlackboard (单源认知状态), ContextSlicer (分层视图)
- communication/: ModelMessageBus (发布/订阅 + 点对点)
- context/: TurnContext (上下文池), CompressionEngine (压缩), ContextController (路由)
- probes/: 探针工具 (probe_start/probe_stop/probe_list) + 权限管理
- skills/: YAML 技能系统 (手动激活)
- runtime_expert: RuntimeExpert 抽象基类 (预留，当前未在生产中使用)

API 入口：
- api_stream.py: StreamThinkingSystem (WebSocket + SSE)
"""
