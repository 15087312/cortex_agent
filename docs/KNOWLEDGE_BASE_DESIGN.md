# 知识库（文档 RAG）设计计划

> 状态：M1–M5 已实施（存储 / 切分 / 摄取 / 检索 / 工具与能力注册）；模型接入已完成（启动预热 + 可见性 + 提示词 + function call 链路）；M6 自动注入已实施（纯对话被动注入，与事件记忆同款），效果评测待做
> 目标：用现有基础设施增加一个**只存知识**的文档检索库，与事件记忆（`EventStore`）功能区分、互不污染。
> 已落地文件：`modules/knowledge/{models,chunker,store,ingestor,retrieval,service}.py`、`infra/tool_manager/tools/knowledge_tools.py`

---

## 1. 目标与边界

### 1.1 做什么

- 把**稳定知识**（规范、接口说明、设计文档、项目文档、代码注释级说明）切分、向量化、入库，支持按语义检索并**带回出处**（文件 → 标题路径 → 片段）。
- 与现有记忆系统**并存**，共用模型与工具装配方式，但**独立存储、独立检索、独立生命周期**。

### 1.2 不做什么（明确 out of scope）

| 不做 | 原因 |
|------|------|
| 从对话中抽取知识 | 那是 `EventReducer` 的职责，属于情景记忆 |
| 知识随时间衰减 / 被检索强化 | 文档不会因为放了三个月就变不相关 |
| 把知识挂到因果图节点 | 因果图描述"发生过的事"的因果；文档不是事件 |
| PDF / Word / 图片 OCR 解析 | 无对应依赖（`requirements.txt` 只有 jieba / sentence-transformers / faiss-cpu / tiktoken），留作二期 |
| rerank 模型、知识图谱抽取 | 一期先用 RRF + 阈值，够用再加 |

---

## 2. 与事件记忆的功能区分

| 维度 | 事件记忆 `EventStore` | 知识库 `KnowledgeBase` |
|------|----------------------|----------------------|
| 回答的问题 | 我**经历过**什么 | 世界/项目**是什么样** |
| 最小单位 | 一条事件（fact/thought/lesson） | 一个文档片段（chunk，带原文） |
| 写入来源 | 会话结束后 LLM 抽取（会改写原文） | 文件摄取，**原文保真** |
| 生命周期 | 追加 + 时间衰减 + 访问强化 | 版本覆盖，**不衰减** |
| 失效方式 | 遗忘曲线、`causal_graph_edit` 删除 | 文件哈希变化 / 显式删除 / 版本替换 |
| 归属 | `owner_id` 按模型隔离 | 全局共享（不分模型） |
| 必须能引用 | 否（摘要即可） | **是**（文件 + 标题路径 + 字符区间） |
| 打分 | 语义/重要性/新近/使用/提及 加权和 | 纯相关性（向量 + 关键词），**无时间项** |
| 注入标题 | 【曾经发生的事】 | 【参考资料】 |

**关键约束**：知识库不写入 `events` 表、不写 `causal_node_ids`、不进 `EventRetrieval` 的打分公式、不受 `memory_libs` 切库影响（见 §9.3）。

---

## 3. 复用清单（全部已有）

| 复用项 | 位置 | 用途 |
|--------|------|------|
| `EmbeddingEngine` | `modules/memory/embedding.py` | 向量化；延迟加载、双检锁、推理串行锁、维度变更重建 |
| `faiss_file_lock` | `utils/faiss_lock.py` | 索引文件跨进程互斥 |
| `settings` 路径模式 | `config/settings.py:265-267` | 新增 `KNOWLEDGE_*` 配置项 |
| `register_capability` | `bootstrap.py` / `infra/tool_manager/service_registry.py` | 注册 `knowledge_base` 能力 |
| `ToolRegistry.register` | `infra/tool_manager/tool_registry.py` | 注册 `knowledge_search` 等工具 |
| 工具白名单 | `modules/thinking/identity.py` | 给各 tier 分配知识库工具权限 |
| 注入点 | `ContinuousThinker._recall_memories` / `PromptComposer.build_system` | 拼装检索结果 |
| `ResultFusion` 模板风格 | `modules/memory/result_fusion.py` | 统一格式化输出 |
| `tiktoken` | `requirements.txt:68` | token 计数与预算控制 |
| `jieba` | `requirements.txt:27` | 中文分词（关键词路） |
| `# @link:block` 注释 | 全仓库约定 | 新模块需带逻辑块标注，兼容 logiclink 校验 |

---

## 4. 数据模型

新增独立 SQLite 库：`data/knowledge.db`（与 `memory.db`、`causal.db` 分离）。

```sql
CREATE TABLE documents (
    id            TEXT PRIMARY KEY,          -- kd_<uuid12>
    title         TEXT NOT NULL,
    source_path   TEXT NOT NULL UNIQUE,      -- 相对 KNOWLEDGE_DIR 的路径
    content_hash  TEXT NOT NULL,             -- sha256(规范化全文)，用于增量
    version       INTEGER NOT NULL DEFAULT 1,
    mime          TEXT DEFAULT 'text/markdown',
    size_bytes    INTEGER DEFAULT 0,
    chunk_count   INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    indexed_at    TEXT DEFAULT ''            -- 最近一次索引成功时间
);

CREATE TABLE chunks (
    id            TEXT PRIMARY KEY,          -- kc_<uuid12>
    doc_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,          -- 文档内顺序，用于相邻合并
    heading_path  TEXT DEFAULT '',           -- "3.2 鉴权流程"，引用用
    text          TEXT NOT NULL,             -- 原文片段（不改写）
    char_start    INTEGER DEFAULT 0,         -- 在规范化全文中的起止，供精确回溯
    char_end      INTEGER DEFAULT 0,
    token_count   INTEGER DEFAULT 0,
    terms         TEXT DEFAULT ''            -- jieba 分词后空格分隔，供关键词路
);

CREATE INDEX idx_chunks_doc   ON chunks(doc_id, seq);
CREATE INDEX idx_docs_hash    ON documents(content_hash);
```

**ID 映射**：`data/knowledge_id_map.json` 保持 `FAISS 位置 → chunk_id`，与 `documents/chunks` 一一对应。删除时以 `chunk_id` 反查位置。

**注意**：SQLite 的 `ON DELETE CASCADE` 默认不生效，连接建立后必须执行 `PRAGMA foreign_keys=ON`；删除逻辑本身仍显式删 `chunks` 行（级联只作兜底），避免依赖 PRAGMA 是否被漏设。

---

## 5. 索引与切分

### 5.1 支持格式（一期，零新依赖）

`.md`、`.txt`、`.py`、`.json`、`.yaml/.yml`、`.toml`

### 5.2 切分策略

```
规范化文本（统一换行、压缩连续空行）
  → 结构切分
      .md           按 ATX 标题（#, ##, ###）分节，维护 heading_path 栈
      .py           按顶层 def/class（ast 解析），无则按空行分块
      其他           按空行分段
  → 段内聚合：累积到 target_tokens（默认 400）成块；超长段按句号/换行硬切
  → 块间重叠：overlap 80 tokens（按句边界对齐，不切断句中）
  → 过滤：token < 20 的碎片并入相邻块；纯符号/空块丢弃
```

**参数**（`settings`）：`KNOWLEDGE_CHUNK_TOKENS=400`、`KNOWLEDGE_CHUNK_OVERLAP=80`、`KNOWLEDGE_MIN_CHUNK_TOKENS=20`、`KNOWLEDGE_MAX_FILE_BYTES=5_000_000`。

**上限**：单文件最多 5000 chunks，超出拒绝并记 warning（防误 ingest 巨物）。

### 5.3 向量索引

- 文件：`data/knowledge_faiss.index` + `data/knowledge_id_map.json`（独立于 events）
- 结构：**`faiss.IndexIDMap2(faiss.IndexFlatIP(dim))`**
  - 与 `EventStore` 的裸 `IndexFlatIP` 不同：`IndexIDMap2` 支持 `remove_ids`，删文档时**不用全量重建**（这是 `EventStore` 现存短板，知识库不重蹈）
- 写入：`EmbeddingEngine.embed_batch(texts)` → `faiss.normalize_L2` → `add_with_ids`
- 一致性：所有索引读写包在 `with faiss_file_lock(index_path):` 内
- 维度变更：启动时比对 `index.d` 与 `EmbeddingEngine.dim`，不一致则整库重建（保留 SQLite 原文，可重建）

### 5.4 增量与删除

```
ingest(path):
  hash = sha256(规范化文本)
  若 documents.source_path 已存在:
      hash 未变 → 跳过（仅更新 indexed_at）
      hash 变了 → version+1, 删旧 chunks 的向量(remove_ids), 删旧 chunks 行,
                  重新切分 + 嵌入 + 写入
  否则 → 新建文档 + 切分 + 嵌入 + 写入
delete(doc_id):
  删 chunks 向量(remove_ids) → 删 chunks 行 → 删 documents 行
```

---

## 6. 检索

### 6.1 两路召回

| 路 | 实现 | 说明 |
|----|------|------|
| 向量路（主） | FAISS `search`，Top-K×3 | 余弦相似度（向量已 L2 归一化） |
| 关键词路 | 优先 SQLite **FTS5**（`chunks_fts(text, terms)`，jieba 预分词写入 `terms`）；启动时用 `PRAGMA compile_options` 检测 FTS5 是否可用，不可用则降级为 `terms` 交集打分 | 无新依赖；中文靠 jieba 预分词 |

### 6.2 融合：RRF 而非加权和

两路分数尺度不同（余弦 ∈ [-1,1]，BM25/词频 ∈ 无界），直接加权不可比。用 **Reciprocal Rank Fusion**：

```
score(chunk) = Σ_route  1 / (k + rank_route(chunk)),  k = 60
```

### 6.3 后处理管线

```
候选合并
 → 绝对相关性门槛（向量路 cosine < 0.30 且关键词路无命中 → 丢弃）
 → RRF 排序
 → 相邻块合并：同 doc 且 seq 连续 → 拼接为一段（避免半句话）
 → 文档级去重：每 doc 最多 KNOWLEDGE_MAX_CHUNKS_PER_DOC=3 段
 → token 预算裁剪：KNOWLEDGE_MAX_INJECT_TOKENS=1200
 → 输出
```

### 6.4 返回结构

```python
@dataclass
class KnowledgeHit:
    chunk_id: str
    doc_id: str
    title: str
    heading_path: str      # "3.2 鉴权流程"
    text: str              # 原文，不改写
    score: float
    source_path: str       # 相对路径
    char_start: int
    char_end: int
```

渲染为可引用的文本：

```
【参考资料】
[1] 《项目架构说明》 §3.2 鉴权流程  (docs/ARCHITECTURE.md)
    双令牌：access 短期 + refresh 长期，refresh 落库...
[2] ...
```

---

## 7. 模块结构（新增文件）

```
modules/knowledge/
    __init__.py
    models.py          # KnowledgeDoc / KnowledgeChunk / KnowledgeHit / IngestResult
    store.py           # KnowledgeStore：SQLite CRUD + FTS5 + FAISS IndexIDMap2（单例）
    chunker.py         # 切分（md/py/text），纯函数，好测
    ingestor.py        # 根目录白名单 / 文件扫描 / 哈希增量 / 删除 / 重建
    retrieval.py       # KnowledgeRetrieval：两路召回 + RRF + 后处理 + 意图判定
    service.py         # KnowledgeService：工具层与 REST 的统一门面

infra/tool_manager/tools/knowledge_tools.py   # 5 个模型可调用工具
```

职责约束：

- `chunker.py` 纯函数、无 IO（单测重点）
- `store.py` 只碰存储，不做业务判断
- `retrieval.py` 不直接读文件，只用 store
- 三者都不 import `modules.memory.*`，只 import `EmbeddingEngine`（向量化）与 `faiss_file_lock`

---

## 8. 工具与 API

### 8.1 模型可调用工具

| 工具 | 风险级 | 说明 |
|------|--------|------|
| `knowledge_search(query, top_k=5, path_prefix="")` | LOW | 语义+关键词检索，返回带出处的片段 |
| `knowledge_list(limit=50)` | LOW | 列出已入库文档（标题/路径/块数/更新时间） |
| `knowledge_ingest(path)` | MEDIUM | 摄取单文件或目录（走安全门审批） |
| `knowledge_delete(doc_id)` | MEDIUM | 删除文档（连带 chunks 与向量） |
| `knowledge_reindex(full=false)` | MEDIUM | 重建索引（full=true 时整库重嵌） |

注册方式照 `causal_graph_edit.py` / `event_query.py`：`@ToolRegistry.register(name=..., risk_level=..., category="query"|"mutation", core=True, tags=["knowledge"])`，能力经 `get_capability("knowledge_base")` 获取，**不直接 import modules**。

工具输出统一由 `knowledge_tools._encode()` 收缩到 3600 字符内：MCP 层对 str 结果按 4000 字符硬截断，超长 JSON 会变成半截字符串，模型无法解析。`hits` 只带元数据（出处/章节/分数），正文统一放在 `rendered`，避免同一段文本重复占预算；溢出时补 `truncated=true` 与 `total`（真实条数）。

`identity.py` 白名单：`orchestrator` / `large` / 各 `supervisor` / 代码与测试类 `expert` 加 `knowledge_search`、`knowledge_list`；`knowledge_ingest`/`delete`/`reindex` 仅 `orchestrator` 与 `large` 可用。

**接入状态（模型真的能调用）**

- **暴露**：`knowledge_search` 标记 `core=True`，随 `ToolPermissionController.get_visible_tools()` 的白名单直接进入原生 function schema；`knowledge_list` 与写工具非 core，名称列在 system prompt 的「其他可用工具」，模型用 `query_tool_details` 取 schema 后调用。
- **调用链路**：模型 function call → `CombinedToolExecutor`（`infra/mcp/combined_provider.py`）→ 工具函数 → `knowledge_base` 能力 → `KnowledgeService`。
- **提示词**：`ModelRunner._build_knowledge_prompt_section()` 仅在 `knowledge_search` 可见时向 system prompt 注入使用指引（含“查历史用 event_query”“空结果不要编造出处”）；不可见角色（如客户/情绪专家）不注入，避免提示调用不存在的工具。
- **启动预热**：`modules/knowledge/warmup.py` 在 Embedding 就绪后后台增量摄取 `KNOWLEDGE_ROOTS`（`KNOWLEDGE_AUTO_INGEST`），幂等、不阻塞启动、异常只告警，保证首次查询就有内容。
- **边界**：`CORTEX_MODE=chatonly` 的纯对话链路没有工具循环，知识库只能经 agent 链路调用；纯对话的自动注入属于 M6。

### 8.2 REST（管理面）

照 `/config/memory-libs` 的 `success/data` + `error_response(ErrorCode.*)` 风格：

```
GET    /knowledge/docs              列表（含 chunk_count、indexed_at）
POST   /knowledge/ingest            摄取路径或目录
DELETE /knowledge/docs/{doc_id}
POST   /knowledge/search            {query, top_k}
POST   /knowledge/reindex           {full}
GET    /knowledge/stats             documents / chunks / 索引大小 / 维度
```

### 8.3 能力注册

`bootstrap.py` 增加：

```python
def _knowledge_base():
    from modules.knowledge.store import get_knowledge_store
    return get_knowledge_store
_safe_register("knowledge_base", _knowledge_base)
```

并加入 `_report_capability_status([...])` 的期望清单（当前 12 个 → 13 个）。

---

## 9. 注入策略与隔离

### 9.1 何时自动检索（避免每轮污染）

**默认不自动注入**，只有满足任一条件才检索：

1. 查询含知识型意图词：`文档 / 规范 / 接口 / 配置 / 怎么用 / 定义 / 参数 / API / 架构`（新增 `_KNOWLEDGE_INTENT_PATTERNS`）
2. 用户附带了文档引用（`@文件` 或路径样式字符串）
3. 模型显式调用 `knowledge_search`

其余情况走现有记忆路径。这样"今天午饭吃什么"不会去查架构文档。

### 9.2 注入位置

纯对话（chatonly）在 `ContinuousThinker.think()` 中由 `_recall_knowledge()` 召回，与事件记忆合并后交给
`PromptComposer.build_system(memory_context)` 统一拼装。与事件记忆不同，**知识库不依赖会话历史**（新会话第一条消息也会注入），
且两段独立拼接，便于模型区分“文档”与“经历”：

```
【参考资料】（来自知识库，引用时请标注文档与章节）
...
```

不与【深度回忆】【曾经发生的事】混排，避免模型把文档当成自己的经历。

### 9.3 与多记忆库的关系

**知识库默认不随 `memory_libs` 切换**：

- 理由：知识（项目规范、接口文档）对所有人、所有会话、所有记忆库都成立；跟着"记忆库"切换会导致切库后模型突然"没读过文档"。
- 实现：`KNOWLEDGE_DB_PATH` 等路径为全局配置，**不**加入 `_apply_current_memory_lib` / `switch_memory_lib`。
- 预留：若将来需要"按项目隔离知识"，用 `documents.collection` 字段分区，而不是换库文件。

---

## 10. 配置项（`config/settings.py`）

### 10.1 摄取根：可配置多目录白名单

`KNOWLEDGE_ROOTS` 支持**多根**（换行 / 逗号 / 分号分隔），解决「知识散落在多处」的问题：

```dotenv
# docs/ 与 specs/ 都作为知识来源，还可直接授权单个文件
KNOWLEDGE_ROOTS=docs,specs
```

规则：

- 相对路径按**项目根**解析（`Path(__file__).parents[2]`），也接受绝对路径与 `~`
- 条目可以是目录（按子孙匹配）或单个文件（按全等匹配 → 精确授权）
- 不存在的条目启动时告警并忽略；全部不存在 → ingest 直接报错，不静默成功
- `ingest` 只接受 `resolve()` 后落在白名单内的路径，否则拒绝（防路径穿越 / 误摄取）
- 扫描时跳过 `.git` / `node_modules` / `__pycache__` / `.venv*` / `dist` / `build` 等目录

```python
KNOWLEDGE_ENABLED: bool = True
KNOWLEDGE_DB_PATH: str = <data/knowledge.db>
KNOWLEDGE_FAISS_INDEX: str = <data/knowledge_faiss.index>
KNOWLEDGE_ID_MAP: str = <data/knowledge_id_map.json>
KNOWLEDGE_ROOTS: str = "docs"              # 摄取根白名单（可多根，见 10.1）
KNOWLEDGE_CHUNK_TOKENS: int = 400
KNOWLEDGE_CHUNK_OVERLAP: int = 80
KNOWLEDGE_MIN_CHUNK_TOKENS: int = 20
KNOWLEDGE_MAX_FILE_BYTES: int = 5_000_000
KNOWLEDGE_TOP_K: int = 5
KNOWLEDGE_MIN_SEMANTIC: float = 0.30        # 与记忆检索口径一致
KNOWLEDGE_MAX_CHUNKS_PER_DOC: int = 3
KNOWLEDGE_MAX_INJECT_TOKENS: int = 1200
KNOWLEDGE_AUTO_INJECT: bool = True          # 关闭后只允许显式工具调用
KNOWLEDGE_AUTO_INGEST: bool = True          # 启动时后台增量摄取 KNOWLEDGE_ROOTS
```

---

## 11. 实施阶段（每阶段可独立验证）

| 阶段 | 内容 | 交付文件 | 验收 |
|------|------|---------|------|
| **M1 存储** | 建表、`PRAGMA foreign_keys=ON`、CRUD、FTS5 检测与降级、单例 | `modules/knowledge/models.py`、`store.py` | 单测：插入/查询/级联删除；FTS5 缺失时降级可用 |
| **M2 切分** | md 标题栈、py ast、段落聚合、重叠、边界过滤 | `chunker.py` | 单测：heading_path 正确、不切断句中、空文件/超长行/代码块边界 |
| **M3 摄取与索引** | 哈希增量、`IndexIDMap2` 写入与 `remove_ids`、维度重建 | `ingestor.py` | 单测：改文件→版本+1 且向量数不变；删文档→向量减少；维度变化→重建 |
| **M4 检索** | 两路召回、RRF、相邻合并、doc 去重、token 预算 | `retrieval.py` | 单测：RRF 排序单调、同 doc 不超过 3 段、超预算截断；集成：ingest→search 命中 |
| **M5 工具与 API** | 5 个工具、bootstrap 能力、REST、identity 白名单 | 工具文件、`bootstrap.py`、`api/main.py` | 单测：工具权限（LOW 免审批、MEDIUM 进审批）；REST 冒烟 |
| **M6 注入与评测** | 知识型意图触发、注入模板、评测脚本 | `continuous_thinker.py`、`config/prompts`、`scripts/eval_knowledge_rag.py` | 20 条问答集：命中率 / 引用正确率 / 无无关注入 |

依赖顺序：M1 → M2 → M3 → M4，M5/M6 可在 M4 后并行。

**实施进度**：M1–M5 已完成（单测 `tests/unit/test_knowledge_{chunker,store,ingestor,retrieval,tools}.py` 覆盖，
端到端见 `tests/integration/test_knowledge_flow.py`）。

接入（启动预热 + 工具可见性 + prompt 指引 + function call 链路）已完成：
`tests/unit/test_knowledge_warmup.py`、`tests/unit/test_knowledge_ai_access.py`、`tests/integration/test_knowledge_ai_call.py`。

M6 自动注入已完成（纯对话链路由 `should_retrieve()` 门控，`KNOWLEDGE_AUTO_INJECT` 可关闭）：
`tests/unit/test_chat_light_knowledge.py`。M6 剩余：效果评测脚本（`scripts/eval_knowledge_rag.py`）。

---

## 12. 测试与验收

### 12.1 单测（`tests/unit/test_knowledge_*.py`）

- 切分：标题路径、重叠、最小块过滤、代码文件 ast 边界
- 存储：哈希去重、版本递增、级联删除、FTS5 降级
- 索引：`add_with_ids` / `remove_ids` 后 `ntotal` 与 `id_map` 长度一致
- 检索：RRF 融合、doc 去重、token 预算、空库返回空
- 隔离：**知识库操作不改变 `events` 表行数与 `causal.db` 节点数**（关键回归）

### 12.1c 纯对话注入（`tests/unit/test_chat_light_knowledge.py`）

- 意图门控：非知识型查询不取知识库（service 不被调用）
- 兜底：知识库异常 / 意图判定异常都不影响回复
- 合并：新会话第一条消息无事件记忆时仍注入；有事件记忆时两段分块且事件记忆在前

### 12.1b 接入（`tests/unit/test_knowledge_warmup.py`、`tests/unit/test_knowledge_ai_access.py`）

- 预热：开关门控（`KNOWLEDGE_ENABLED` / `KNOWLEDGE_AUTO_INGEST`）、进程内幂等、摄取异常被吞掉不阻断启动
- 可见性：`large` 全量、`supervisor`/`expert` 只读、未授权角色不可见
- schema：`knowledge_search.required == ["query"]`，参数集合与工具定义一致
- 提示词：仅工具可见时注入【知识库】段落
- 调用：`ToolCallRequest → CombinedToolExecutor → knowledge_search` 返回可引用命中

### 12.2 集成（`tests/integration/test_knowledge_flow.py`、`test_knowledge_ai_call.py`）

`ingest(docs/*.md) → search("鉴权流程") → 注入文本包含出处 → 修改文件 → 再检索命中新版本`

全部使用 `tmp_path` 独立库，`monkeypatch` 单例（照 `test_causal_memory.py` 的写法）。

### 12.3 效果评测（`scripts/eval_knowledge_rag.py`）

- 20 条问答，标注标准答案所在文档与章节
- 指标：`hit@3`（正确文档进前三）、`citation_ok`（返回的 heading 与标注一致）、`irrelevant_rate`（无关查询触发注入的比例）
- 基线：同问题仅走事件记忆，证明知识库的增量价值

### 12.4 回归

- 现有测试全绿（重点：`tests/integration/test_causal_memory.py`、`tests/unit/test_memory_*.py`、`tests/unit/test_tool_permission_controller*.py`）
- `bootstrap` 能力数从 12 → 13，`_report_capability_status` 期望清单同步更新

---

## 13. 风险与对策

| 风险 | 对策 |
|------|------|
| FTS5 在某些 Python 构建中不可用 | 启动检测 `PRAGMA compile_options`，不可用则降级为 `terms` 交集打分（功能降级不报错） |
| 双份 embedding 模型内存 | 复用 `EmbeddingEngine` 单例，**不新建**（`memory_matcher.py` 已有一份独立实例，不要再加第三份） |
| `IndexIDMap2` 与 `EventStore` 索引结构不同 | 知识库独立文件、独立代码路径，不复用 `EventStore` 的 `_save_faiss` |
| 大文件拖垮索引 | 单文件 5MB / 5000 chunks 上限 + 明确报错 |
| ingest 任意路径读取 | `source_path` 必须 resolve 到 `KNOWLEDGE_ROOTS` 白名单之下，否则拒绝（防路径穿越） |
| 切库误解 | 知识库不随 `memory_libs` 切换，文档与 UI 明确说明 |
| 知识被误当事件 | 独立注入标题【参考资料】；不写 `events` / `causal_node_ids` |

---

## 14. 后续可选（二期）

- PDF / docx 解析（引入 `pypdf`、`python-docx`）
- rerank（cross-encoder 或小模型重排）
- 父子块检索（命中子块、返回父块上下文）
- 文档版本历史与回滚
- `documents.collection` 分集合，按项目/领域隔离
