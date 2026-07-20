# Cortex Agent — Humanoid AGI Backend System

**项目名称**: Cortex Agent (cortex-agent)  
**版本**: 2.0.0  
**技术栈**: Python 3.11+ / FastAPI / WebSocket / SQLite + FAISS / MCP Protocol / Docker  

## 项目简介

这是一个**类人智能 (Humanoid AGI) 后端系统**，模拟人类的认知架构——感知、思考、记忆、注意力、安全决策——来构建一个能够**自主观察环境、主动思考、安全执行**的 AI Agent 后端。

## 核心架构（8大子系统）

| 子系统 | 核心能力 |
|--------|----------|
| **感知系统 (Perception)** | 多源屏幕感知：OmniParser + OCR + UI 树 + 帧差检测 + 窗口监控 + 语音识别 + MCP 资源感知，Pipeline 架构 + EventBus 事件驱动 |
| **注意力系统 (Attention)** | 关键词重要性分类 + TF-IDF 相关记忆检索，动态调整记忆召回阈值，跨模态融合注意力 |
| **思考系统 (Thinking)** | 多模型编排器 (Multi-Model Orchestrator) + 认知黑板 (Cognitive Blackboard) + 反思状态机 + Probes 探针系统 + Experts 专家池 + Skills 技能系统 + Communication MessageBus |
| **记忆系统 (Memory)** | SQLite + FAISS 向量双引擎，支持遗忘曲线衰减、access_count 强化、关键词/向量混合检索 |
| **安全系统 (Security)** | 四级执行模式 (plan/edit/yolo/control) × 三级风险审查，极端操作硬阻断 → LLM 审批 → 用户确认 AND 逻辑，审计日志追踪 |
| **工具系统 (Tool/MCP)** | MCP 协议双传输 (stdio+SSE)，工具注册/发现/执行流水线，参数自动纠错，指数退避重试，角色权限控制 |
| **输出系统 (Output)** | 内容安全校验 (Core/Content/Output 三层) + 格式校验 + 硬件输入控制 (鼠标/键盘/截屏) |
| **数据库系统 (Database)** | SQLite 连接池 + Repository 模式 + diskcache 缓存层，零配置可打包，支持 WAL 模式并发读 |

### 隐藏子系统：管理系统 (Management)

独立于 8 大子系统之外，管理提供全局错误总线和状态聚合：

| 组件 | 职责 |
|------|------|
| **GlobalErrorBus** | 全局未处理异常捕获 (`sys.excepthook` + `threading` + `asyncio`)，通过 WebSocket 推送到 TUI 前端 |
| **ErrorReporter** | 结构化错误上报，被所有模型客户端调用，记录日志 + 热发到 ErrorBus |
| **StatusCollector** | 模块自动发现 + 状态聚合，扫描 `modules/` 下所有子目录 |
| **Management API** | 50+ HTTP 端点，聚合所有模块状态供前端控制面板 |

---

## 技术亮点（简历可写）

### 1. 多层安全门控架构 + 四种执行模式

实现了 4 种执行模式 (plan/edit/yolo/control) 的安全策略矩阵，由 **ToolSecurityGate** 统一审查：

- **极端危险操作** (`rm -rf /` 等) → 硬阻断，不可绕过
- **HIGH 风险工具** → LLM 安全专家审批 + 用户确认（AND 逻辑），相同调用缓存避免重复审批
- **plan 模式** → 模型输出反向检查是否含写操作指令，写操作直接拒绝
- **control 模式** → 所有非 LOW 工具需用户确认

工具权限由 **ToolPermissionController** 集中管理：基于角色类别 (large/expert/supervisor) + tier 过滤 + skill 规则，统一控制工具的可见性和可执行性，替代了之前分散在 identity/tool_security_gate/tool_manager 三处的权限逻辑。所有安全事件写入 JSONL 审计日志，支持回溯查证。

### 2. MCP 协议双通道工具编排

基于 MCP (Model Context Protocol) 实现 **stdio (本地子进程)** + **SSE (远程 HTTP)** 双传输层：

- **MCPServerManager** — 管理 MCP Server 生命周期（启动/连接/关闭/重试）
- **CombinedToolProvider** — 统一本地工具注册表 + 远程 MCP Server 工具列表
- **MCPToolService** — 工具调用权限检查 + 执行 + 事件记录一条龙
- **参数名模糊匹配自动纠错** — 模型传错参数名时自动校正
- **指数退避重试** — 网络抖动时自动重试

### 3. 类人感知-注意力-记忆闭环

**感知流水线 (Perception Pipeline)**: capture → frame_diff → roi_dispatcher 三级流水线，8 个专用 Detector（OmniParser / OCR / UI 树 / Touchpoint / Voice / Window / MCP / File Change）通过 EventBus 解耦，ProactiveTrigger 在屏幕变化 ≥ 15% 且用户空闲 ≥ 5 分钟时主动发起。

**注意力系统**: AttentionCore 分析关键词紧急程度 + TF-IDF 余弦相似度，动态调整记忆召回阈值。新版 V2 引擎支持跨模态融合、自适应衰减、资源分配。

**事件记忆系统**: EventReducer → EventStore (SQLite + FAISS) 持久化会话事件，EventRetrieval 每轮思考注入 `【历史记忆】` 至模型 prompt。新增**因果树深度回忆** (CausalGraph + CausalTree)：对溯源/预测/归纳类查询自动触发因果图邻域扩散→树下钻→事件池复合排序召回，输出因果链路+佐证事件+反例。

### 4. 认知黑板 (Cognitive Blackboard)

**唯一认知状态源 (Single Source of Truth)**，统一管理：
- **委托任务 (Delegation)** — 模型之间的任务委派记录
- **观察结果 (Observation)** — 感知系统的环境观察
- **专家发现 (ExpertFinding)** — 专家模型的结论
- **对话框 (DialogEntry)** — 多模型对话历史

支持按 tier (large/expert/supervisor) 权限控制读写，所有认知状态在此汇聚，避免分散存储。

### 5. 多模型流式调度 + 反思状态机

**MultiModelOrchestrator** 实现 "模型不直接调用模型 → 模型调用工具 → 工具操纵探针 → 探针激活模型" 的间接调度链：

- 支持 DashScope / OpenAI / Anthropic 三种 API 格式的 SSE 流式解析，含工具调用 delta 累积
- **Probes 探针系统** — 5 种模板化探针，携带超时/Token 预算/白名单，安全子进程运行
- **Skills 技能系统** — YAML 定义的可复用技能，支持条件触发和参数模板
- **Experts 专家池** — 预置多领域专家（代码/翻译/安全审查/客户），runtime 按需加载

**反思状态机 (Reflection SM)**: 5 个硬编码触发点（单步完成 / 工具错误 / 探针失败 / 卡住超时 / 协作回合）构成代码级闭环，输出结构化决策 (proceed / retry / rollback / ask_user / terminate)，确保模型不会自主决定何时反思。配套 **ValueSystem** 记录价值约束学习和演化。

### 6. 感知系统多源融合

- **OmniParser** — 自动下载 + 启动管理，截图 → 解析 UI 元素树（按钮/输入框/图标）
- **UI Detector** — 基于 OmniParser 的结构化 UI 元素检测
- **OCR Detector** — 无 OmniParser 时的备选方案
- **Touchpoint Detector** — 检测屏幕可交互区域
- **Window Detector** — 监控活动窗口变化
- **Voice Detector** — 语音输入检测
- **MCP Detector** — 通过 MCP 协议获取屏幕资源
- **Difference 引擎** — 帧差强度计算 + 变化率 + 心跳检测

### 7. 认知上下文管理

**Context 系统** 管理模型上下文窗口：

- **ContextCompression** — JSON 转义还原 + 结构化压缩，适配不同模型的 Token 限制
- **ContextManager** — 按 tier 差异化组装上下文：大模型含完整感知/记忆/黑板，小模型仅标题摘要
- **GlobalContextPool** — 跨会话上下文池，按 domain 隔离
- **CognitiveBlackboard** — 认知状态上下文供应

### 8. 零配置数据库层

- **SQLite** — 单文件、零配置、WAL 模式支持并发读写
- **diskcache** — 替代 Redis 的磁盘缓存层，内存后备模式 (5000 条上限自动淘汰)
- **SessionRepository** — 会话持久化，支持前端断线重连恢复上下文
- **EventStore** — 事件记忆持久化 (SQLite + FAISS)，会话结束后 30s 异步写入

### 9. 一键部署 + 容器化

CLI 入口 `cortex` 命令一键启动后端 + TUI；Docker 多阶段构建 (builder → runtime → cli)，非 root 用户运行，支持 docker-compose 编排 (含 SearXNG 搜索引擎)。

---

**规模**: 100+ Python 模块，12 个核心子系统 (含 Management 和 Database)，50+ 可配置参数，完整的单元测试和集成测试覆盖。
