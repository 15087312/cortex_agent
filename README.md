# Cortex Agent

> **类人智能后端系统** — 多模型协作 · 连续思考 · 三层记忆 · 安全审计

---

## 快速开始

### 一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/15087312/cortex_agent/main/install.sh | bash
```

安装完成后直接运行：

```bash
cortex
```

### 手动安装

```bash
# 1. 克隆
git clone https://github.com/15087312/cortex_agent.git
cd cortex_agent

# 2. 安装
pip install -e .

# 3. 配置
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 4. 启动
cortex
```

---

## 使用方式

```bash
# 一键启动（后端 + 交互式终端）
cortex

# 指定端口
cortex --port 9000

# 只启动后端（API 服务模式，无终端界面）
cortex --no-tui

# 连接已有的远程后端
cortex --api-url http://192.168.1.100:8080

# 指定 API 密钥
cortex --api-key your-secret-key
```

启动后你会看到：

```
🚀 启动 Cortex Agent (:8080)...
⏳ 等待后端就绪...
✓ 后端就绪: http://127.0.0.1:8080
```

然后进入交互式终端，直接输入问题即可对话。按 `Ctrl+C` 优雅退出。

---

## 核心架构 — 与众不同之处

### 🎯 事件驱动黑板架构（Event-Driven Blackboard）
传统多agent系统存在 **N²复杂度**（所有agent都读全部history），导致重复回复、超时、上下文污染。Cortex_agent从根本上重构为**事件驱动黑板**：

- **单一真理来源**：`CognitiveBlackboard` 维护完整思维状态
- **分层上下文切片**：
  - 📘 **Large模型** → 看全局目标、计划、风险、委托、发现
  - 📗 **Supervisor** → 看任务目标、可用工具
  - 📙 **Expert** → 只看当前步骤、工具状态、最近5步执行历史
- **消除N²污染**：每个turn完全隔离，agent间无噪音干扰

### 🧠 6时机记忆系统（T1-T6 Memory Pipeline）
对标Claude内部设计的多阶段记忆，支持真正的"长期学习"：

| 阶段 | 触发时机 | 作用 |
|------|---------|------|
| **T1** | 会话预加载 | 4路并行读取过去对话、笔记、技能、行为准则 |
| **T2** | 输入后检索 | Per-turn缓存，找相关历史对话（毫秒级FAISS搜索） |
| **T3** | 工具后关联 | 执行工具后立即检索相关知识 |
| **T4** | 水位线压缩 | 当对话长度达70%时触发，自动总结与删除 |
| **T5** | 任务后沉淀 | 任务完成后30秒，沉淀关键发现到长期记忆 |
| **T6** | 深度整合 | 每12小时运行，跨会话知识融合与演化 |

**关键约束**：所有T1-T6都用 `fire-and-forget` 异步，**绝不阻塞**主请求流程。

### 🔄 显式生命周期管理（SessionLifecycle + TurnState）
从隐式状态（易竞态、难debug）升级为显式状态机：

```
IDLE → PLANNING → EXECUTING → INTEGRATING → COMPLETE
```

- 每个turn有独立的 `TurnContext` 和 `TurnState`
- `SessionLifecycle` 管理会话全生命周期
- 杜绝竞态条件，支持真正的多会话并发

### 🎪 13个专家角色系统（Intelligent Delegation）
不是简单的prompt，而是完整的角色定义和任务路由：

| 角色 | 专长 |
|------|------|
| `code_supervisor` | 代码架构设计、技术风险评估 |
| `code_reviewer` | 代码质量审查 |
| `code_writer` | 代码实现 |
| `security_monitor` | 安全审计、漏洞检测 |
| `test_writer` | 测试策略规划 |
| `emotion` | 情感分析、用户体验 |
| `memory_manager` | 知识整合与演化 |
| `orchestrator` | 任务协调与流程编排 |
| 其他8个... | — |

智能委托引擎自动根据任务复杂度选择最优expert，支持**并行执行**和**串行依赖**。

### 🛡️ 多层安全防护（Defense in Depth）
- **输入检查** → 内容审核、意图识别
- **执行审查** → 工具调用前预检，分级审批
- **输出审查** → 回复内容合规性校验
- **完整审计链** → 所有决策可追溯

已修复11个P0级安全漏洞，通过认知黑板实现**完全可审计的AI决策**。

---

## 智能体协作系统

### 🎬 总指挥 → 主管 → 专家三层循环

Cortex_agent采用**树形任务分解架构**，实现渐进式细化和并行执行：

```
用户输入
   ↓
[总指挥 Large Model] ← 大局思考（目标、计划、风险评估）
   ↓ 分解为子任务
[主管 Supervisor] ← N个主管并行接收任务
   ├─ code_supervisor → 代码架构设计
   ├─ creative_supervisor → 创意方案规划
   ├─ query_supervisor → 信息检索指导
   └─ ...
   ↓ 每个主管分配给专家
[专家 Expert] ← N×M个专家并行执行
   ├─ code_writer, code_reviewer, test_writer
   ├─ creative_writer, emotion, memory_manager
   └─ ...
   ↓ 所有结果汇聚到黑板
[认知黑板] ← 统一的思维状态
   ↓
[总指挥整合] ← 大模型综合所有专家发现，生成最终答案
```

**关键特性**：
- 🔀 **多主管并行**：同一个请求可同时启动多个主管（code_supervisor、query_supervisor等），并行处理不同维度
- 📊 **专家池化**：每个主管下可动态分配多个专家，支持串行依赖（如code_reviewer依赖code_writer）
- ♻️ **黑板驱动循环**：所有结果写入认知黑板，总指挥据此迭代或调整策略
- ⚡ **并行加速**：独立任务真正并行，依赖关系自动检测

### 💚 情感智能专家（Emotion Expert）

Cortex_agent内置**情感感知与用户体验优化**：

- **情感识别**：分析用户输入的情绪状态（积极/消极/困惑/急切）
- **共情回应**：根据情感调整回答风格（冷静分析 vs 鼓励安慰）
- **节奏适应**：检测用户耐心，长问题时主动分步骤、设置进度条
- **满足度追踪**：根据追问、反复、停顿判断用户满意度，主动优化
- **长期人格塑造**：记忆用户的沟通偏好，逐渐适应其风格

情感专家贯穿整个对话流程，是**"懂用户"的核心**。

### 🎯 价值观与安全专家（Security Monitor + Values Evolution）

不同于静态的内容审核，Cortex_agent的**价值观系统是动态的、可进化的**：

#### 行为准则（初始值）
```yaml
values:
  honesty: "准确表述、承认不确定性"
  helpfulness: "优先用户利益，避免伤害"
  transparency: "解释推理过程，可审计"
  safety: "拒绝危险请求，提供安全替方案"
```

#### 安全专家职责
- ✓ **请求前评估**：输入阶段判断意图是否安全
- ✓ **执行中监控**：工具调用前检查参数、权限、副作用
- ✓ **输出后审查**：回复发送前检查是否包含有害内容
- ✓ **学习与反馈**：记录安全事件，动态调整防护策略

#### 价值观进化
- 从用户反馈自动学习：用户说"这个建议太激进"→ 调整保守度参数
- 从任务结果学习：某个决策导致失败→ 下次相似情景采取更谨慎的值
- 跨会话积累：每个会话沉淀安全洞察到长期记忆(T6)，系统越用越谨慎越聪慧

### ⚙️ 多专家主管并行执行

一个请求可同时启动**多个不同维度的主管**，各自带领专家团队独立工作：

```
用户：帮我写一个支付模块，要安全高效
   ↓
[总指挥] 分析：需要代码实现 + 安全审计 + 测试规划
   ↓ 并行启动3个主管：
   ┌─ code_supervisor
   │   └─ code_writer → 实现支付逻辑
   │      code_reviewer → 审查代码质量
   │
   ├─ query_supervisor（可选）
   │   └─ 搜索支付行业最佳实践
   │
   └─ (隐含) security_monitor
       └─ 检查PCI DSS合规性
   
   ↓ 所有结果合并
[最终答案] = 代码 + 架构评估 + 安全建议 + 测试计划 + 行业最佳实践
```

**优势**：
- 🚀 避免串行瓶颈，多维度并行思考
- 🎯 每个主管专注自己的领域，减少token浪费
- 📈 同样的输入，获得更全面的输出

---

## 感知系统与自我进化

### 👁️ 多维感知系统（Perception System）

Cortex_agent不仅"回答问题"，还**感知对话全景**：

| 感知维度 | 说明 | 用途 |
|---------|------|------|
| **时间感知** | 知道当前时间、距上次对话间隔、会话总时长 | 调整记忆策略、判断用户忙碌度 |
| **身份感知** | 识别用户、多人会话中分辨说话者 | 个性化回应、多人协作场景 |
| **情感感知** | 分析输入的情绪、语气、急切度 | 调整回答风格、提供情感支持 |
| **认知感知** | 检测用户理解程度、是否需要简化或深化 | 自动调整解释粒度 |
| **意图感知** | 识别隐含需求（问"怎样提高效率"，实际想要具体工具推荐） | 主动补充答案维度 |
| **满足度感知** | 通过追问、沉默、反复判断用户满意度 | 主动优化、提供替代方案 |
| **上下文感知** | 理解对话全局、识别话题转移 | 保持连贯性、自动关闭过期话题 |

感知系统驱动**自适应对话**，而非"一问一答"的机械问答。

### 🧬 自我进化机制（Self-Evolution）

Cortex_agent在与用户互动中**持续演化**：

#### 短期进化（Per-Turn）
- **即时反馈学习**：用户说"这个不对"→ 立即调整后续回答
- **动态价值调整**：用户说"太保守了"→ 当前会话内提高主动性参数
- **风格适应**：用户倾向简洁→ 自动缩短输出

#### 中期进化（Per-Session）
- **专业积累**：某个领域重复提问→ 建立该领域的专业知识点
- **用户模型更新**：积累对该用户的偏好、背景、目标
- **工具有效性学习**：某个工具/方案反复有效→ 提高推荐权重

#### 长期进化（Cross-Session，T6触发）
- **知识融合**：跨会话找到模式（用户总在周一问工作问题→ 周一更激进主动）
- **价值观演化**：多个会话的安全事件汇总→ 更新全局防护策略
- **角色微调**：发现某个专家在某类问题上表现差→ 调整其角色参数

进化存储在**长期记忆**中，永久保留，持续影响未来对话。

---

## 陪伴模式（Companion Mode）

与传统"问答机器"不同，Cortex_agent支持**陪伴型长期交互**：

### 核心特性

| 模式 | 说明 | 场景 |
|------|------|------|
| **回忆模式** | 主动提起过去对话、项目进展、用户目标 | 长期项目协作、学习辅导 |
| **关切模式** | 追踪用户状态（"最近工作繁忙？"），主动慰问或鼓励 | 用户支持、心理陪伴 |
| **成长陪伴** | 跟踪用户能力进展，逐步提升任务难度 | 技能培养、学习加速 |
| **价值探讨** | 不仅解决问题，还讨论"为什么这样做更好" | 深度思考、决策支持 |
| **问题推动** | 用户陷入困境时，主动提出启发性问题 | 创意突破、问题转化 |

### 技术实现

**感知输入**：
- 用户最常提的话题、重复的困扰、核心目标
- 对话频率、时间规律、语气变化
- 完成的任务、失败的尝试、学到的教训

**记忆驱动**：
- T1 会话加载：预读用户过去的对话、笔记、行为准则
- T5 沉淀：任务完成后，自动总结用户进展
- T6 融合：每天一次深度分析，更新用户画像

**主动行为**：
```python
# 陪伴模式示例
if user_last_session > 3_days:
    main_pain_point = memory.get_top_frustration(user_id)
    proactive_msg = f"我注意你最近在纠结{main_pain_point}，我有几个新想法..."
    
if user_shows_burnout_signal:
    conversation_style = "empathetic"  # 切换为共情风格
    suggest_break = True
    
if user_achieved_milestone:
    celebrate_and_suggest_next_step()
```

### 隐私与边界

- ✓ 所有记忆仅存储于用户数据库，Cortex_agent无法跨用户访问
- ✓ 用户可随时查看、编辑、删除长期记忆
- ✓ 陪伴功能可独立关闭（配置`COMPANION_MODE=off`）

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **多模型协作** | 大/中/小三层模型分工合作，像团队一样推理 |
| **连续深度思考** | 不急于回答，多轮迭代推理直到满意 |
| **6时机记忆** | T1-T6多阶段记忆，fire-and-forget异步，越用越懂你 |
| **安全多层防护** | 工具分级审批、输入输出双向校验、完整审计链 |
| **认知黑板** | 所有角色共享同一份"思维画布"，消除N²上下文污染 |
| **13个专家角色** | 智能委托系统，自动路由最优expert执行任务 |
| **显式生命周期** | SessionLifecycle + TurnState，杜绝竞态、支持并发 |
| **联网搜索** | crawl4ai 无头浏览器抓取网页全文 |
| **价值观进化** | AI 可根据经验动态调整自身行为准则 |
| **时间感知** | AI 知道当前时间、距上次对话多久、谁在说话 |
| **实时流式** | WebSocket 推送思考过程、专家调度、工具调用 |
| **三格式兼容** | 同时支持 OpenAI / DashScope / Anthropic API |
| **CLI 终端** | 配套 TUI 界面，实时可视化所有内部过程 |

---

## 项目结构

```
cortex_agent/
├── cortex/           # CLI 入口（cortex 命令）
├── api/              # FastAPI 应用 + WebSocket
├── cli_tui/          # Textual TUI 终端界面
├── config/           # 配置系统
├── modules/          # 核心业务模块
│   ├── thinking/     # 多模型思考引擎
│   ├── memory/       # 三层记忆系统
│   ├── security/     # 安全与审计
│   ├── attention/    # 注意力系统
│   └── ...
├── infra/            # 基础设施（模型客户端、工具管理、数据处理）
├── skills/           # YAML 技能定义
├── tests/            # 测试（248 个用例）
├── docs/             # 文档
├── frontend/         # 前端（预留）
├── pyproject.toml    # 项目配置
├── requirements.txt  # Python 依赖
└── Dockerfile        # Docker 部署
```

---

## 配置

核心配置通过 `.env` 文件：

```env
# 必填：模型 API Key
LARGE_MODEL_API_KEY=sk-your-key-here

# 可选：HTTP API 认证
SIMPLE_API_KEY=your-secret

# 可选：指定模型
LARGE_MODEL_NAME=deepseek-v4-flash
LARGE_MODEL_API_URL=https://api.deepseek.com/v1/chat/completions
```

完整配置项见 [.env.example](.env.example)。

---

## Docker 部署

```bash
docker-compose up -d
```

---

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /metrics` | Prometheus 指标 |
| `WS /stream/ws/{session_id}` | WebSocket 实时对话 |
| `GET /config` | 获取配置 |
| `PUT /config/{key}` | 更新配置（需 API Key） |

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -q

# 启动后端（开发模式）
python -m scripts.start_all
```

---

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLite / FAISS / aiohttp
- **终端**: Textual (TUI) / Rich
- **模型**: DeepSeek / OpenAI / Anthropic（三格式兼容）
- **搜索**: crawl4ai（无头浏览器）
- **部署**: Docker / Docker Compose

---

## 协议

[Apache License 2.0](LICENSE)
