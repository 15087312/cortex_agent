# Cortex Agent — Humanoid AGI Backend System

**项目名称**: Cortex Agent (cortex-agent)  
**版本**: 2.0.0  
**技术栈**: Python 3.11+ / FastAPI / WebSocket / SQLite + FAISS / MCP Protocol / Docker  

## 项目简介

这是一个**类人智能 (Humanoid AGI) 后端系统**，模拟人类的认知架构——感知、思考、记忆、注意力、安全决策——来构建一个能够**自主观察环境、主动思考、安全执行**的 AI Agent 后端。

## 核心架构（8大子系统）

| 子系统 | 核心能力 |
|--------|----------|
| **感知系统 (Perception)** | 屏幕 OCR + 帧差检测 + 窗口监控 + 文件变化监听 + 语音识别 + MCP 资源感知 |
| **注意力系统 (Attention)** | 关键词重要性分类 + TF-IDF 相关记忆检索，动态调整记忆召回阈值 |
| **思考系统 (Thinking)** | 多模型编排器 (Multi-Model Orchestrator) + 反思状态机 (5触发点闭环) + 认知黑板 |
| **记忆系统 (Memory)** | SQLite + FAISS 向量双引擎，支持遗忘曲线衰减、access_count 强化、关键词/向量混合检索 |
| **安全系统 (Security)** | 三层安全门控：极端危害硬阻断 → LLM 安全专家审批 → 用户确认，支持 plan/edit/yolo/control 四种执行模式 |
| **工具系统 (Tool/MCP)** | MCP 协议双传输 (stdio+SSE)，动态工具注册、参数自动纠错、指数退避重试 |
| **输出系统 (Output)** | 内容安全校验 + 格式校验 + 硬件输入控制 (鼠标/键盘/截屏) |
| **指标系统 (Metrics)** | Prometheus 格式导出、P50/P95/P99 延迟直方图、系统资源监控 |

## 技术亮点（简历可写）

### 1. 多层安全门控架构

实现了 4 种执行模式 (plan/edit/yolo/control) 的安全策略矩阵：极端危险操作 (rm -rf / 等) 硬阻断、HIGH 风险工具经 LLM 安全专家审批后再用户确认（AND 逻辑）、相同调用缓存避免重复审批、Plan 模式下模型输出反向检查是否含写操作指令。

### 2. MCP 协议双通道工具编排

基于 MCP (Model Context Protocol) 实现 stdio (本地子进程) + SSE (远程 HTTP) 双传输层，支持运行时动态添加 MCP Server、工具列表缓存、统一工具路由 (ToolManager → MCPToolService)，以及参数名模糊匹配自动纠错。

### 3. 类人感知-注意力-记忆闭环

屏幕变化/文件变更/语音等多源感知事件 → EventBus → PerceptionIntegrator 订阅并注入模型上下文；AttentionCore 根据紧急关键词和 TF-IDF 余弦相似度动态调整记忆召回阈值；EventStore 实现遗忘曲线 (recency_decay + access_count 强化) 的记忆衰减。

### 4. 反思状态机 (Reflection State Machine)

5 个硬编码触发点 (单步完成/工具错误/探针失败/卡住超时/协作回合) 构成代码级闭环，输出结构化决策 (proceed/retry/rollback/ask_user/terminate)，确保模型不会自主决定何时反思。

### 5. 多模型流式调度

MultiModelOrchestrator 实现 "模型不直接调用模型 → 模型调用工具 → 工具操纵探针 → 探针激活模型" 的间接调度链；支持 DashScope/OpenAI/Anthropic 三种 API 格式的 SSE 流式解析，含工具调用 delta 累积。

### 6. 认知黑板 (Cognitive Blackboard)

唯一认知状态源 (Single Source of Truth)，统一管理委托任务 (Delegation)、观察结果 (Observation)、专家发现 (ExpertFinding)、对话框 (DialogEntry)，支持按 tier 权限控制读写。

### 7. 主动感知触发

ProactiveTrigger 订阅 SCREEN_DIFF 事件，当屏幕变化幅度 ≥ 15% 且用户空闲 ≥ 5 分钟时，自动调用 LLM 生成主动询问并通过 WebSocket 推送，模拟真人助手的主动关怀行为。

### 8. 一键部署 + 容器化

CLI 入口 `cortex` 命令一键启动后端 + TUI；Docker 多阶段构建 (builder → runtime → cli)，非 root 用户运行，支持 docker-compose 编排 (含 SearXNG 搜索引擎)。

---

**规模**: 100+ Python 模块，12 个核心子系统，50+ 可配置参数，完整的单元测试和集成测试覆盖。
