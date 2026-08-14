# 内存泄漏测试与检测体系

> 本文档记录项目的内存泄漏**检测机制**、**泄漏测试套件**、**运行方法与配置**。
> 相关 bug 修复见 `docs/ERRORS_AND_FIXES.md` §30/32/35/36/37。

## 一、体系总览（三层 + 覆盖证明）

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 检测（默认开启）  conftest 全局，unit+integration 全覆盖  │
│    · muppy 字节采样 + 趋势判定（每 100 测试）                 │
│    · pympler 会话前后类型 diff                               │
│ 2. 验证              tests/leak/ 10 类泄漏套件               │
│    · scripts/verify_leak_detection.py 逐个断言可识别          │
│ 3. 终止              内存看门狗：超限 os._exit(1)            │
│    · 防内存失控拖垮本机/CI runner                            │
│ 4. 覆盖证明          [MODULE-COVERAGE] 模块执行清单           │
│    · 195/195 生产模块均被测试执行                            │
└─────────────────────────────────────────────────────────────┘
```

## 二、检测机制（conftest 默认开启）

每次 pytest 会话（unit / integration）自动执行，**零配置**：

| 层 | 实现 | 检测内容 |
|---|---|---|
| 字节趋势 | `pympler.muppy.get_size` 每 `LEAK_INTERVAL`(100) 测试采样存活字节 | 跨测试持续增长 = 泄漏（含 bytes/numpy 原始内存） |
| 趋势判定 | 会话结束对后半段采样点拟合斜率 | `> LEAK_RATE_THRESHOLD`(256 KiB/测试) 报 `⚠ 疑似内存泄漏` |
| 类型定位 | pympler `SummaryTracker` 会话前后 diff | 未释放对象类型 top |
| 采样定位 | 采样点记录 nodeid | 可定位跳涨区间对应的测试 |

输出入口：`pytest_sessionfinish`（capture 释放后可靠显示），标记 `[LEAK-DETECT]`。

### 判定输出示例

```
[LEAK-DETECT] 内存泄漏检测报告
  [趋势] 采样点(测试数, 存活KiB, 节点):
     100  94860 KiB  ...
     200  97236 KiB  ...
  [趋势] 后 27 个采样点: 187021 → 500315 KiB (每测试新增 120.5 KiB)
  ✓ 内存稳定（每测试 120.5 KiB，阈值 256 KiB/测试）
```

### 配置环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LEAK_INTERVAL` | 100 | 采样间隔（测试数） |
| `LEAK_RATE_THRESHOLD` | 256 | 每测试新增 KiB，超过判泄漏 |
| `LEAK_REPORT` | 1 | 0 关闭报告 |
| `CORTEX_TEST_MEM_LIMIT_MB` | 4096 | 内存看门狗上限（0 关闭） |
| `CORTEX_TEST_MODULE_UNCOVERED_MAX` | 10 | 未覆盖生产模块数上限 |

## 三、泄漏测试套件（tests/leak/）

10 个独立文件，每种**故意构造**特定类型/模块的泄漏，验证检测系统能识别。

| # | 文件 | 泄漏类型 | 模块域 |
|---|---|---|---|
| A | `test_leak_object_growth.py` | 对象引用累积（list/dict 无界） | 通用 |
| B | `test_leak_bytes_raw.py` | 原始内存（bytes，对象数不变字节涨） | 通用 |
| C | `test_leak_reference_cycle.py` | 引用循环（带 `__del__`，GC 无法回收） | 通用 |
| D | `test_leak_threads.py` | 线程对象累积 | 通用 |
| E | `test_leak_global_cache.py` | 无界缓存（dict 键持续增长） | 通用 |
| F | `test_leak_module_memory.py` | 记忆事件/黑板观测无界追加 | modules/memory |
| G | `test_leak_module_perception.py` | 事件订阅表 + 感知事件队列 | modules/perception |
| H | `test_leak_module_model.py` | 模型 client 实例累积 | infra/model |
| I | `test_leak_module_database.py` | 未关闭 DB session 对象 | modules/database |
| J | `test_leak_files_resources.py` | 文件句柄/内容未释放 | utils/output |

- 全部标记 `pytestmark = pytest.mark.leak`，**默认被 deselected**（不进入正常套件）
- 运行验证：`python scripts/verify_leak_detection.py`（每个文件独立进程，断言输出 `⚠ 疑似内存泄漏`）

## 四、定位工具（scripts/leak_check.py）

| 模式 | 命令 | 用途 |
|---|---|---|
| RSS 监控 | `python scripts/leak_check.py tests/unit/test_xxx.py` | 外部采样子进程真实物理内存，判线性增长 |
| tracemalloc 定位 | `python scripts/leak_check.py --tracemalloc ...` | 输出存活内存分配位置 top（慢，适合小范围） |
| 全量 | `python scripts/leak_check.py` | 全量 + RSS + 报告 |

## 五、模块覆盖清单（保证"所有模块在检测范围"）

泄漏检测是 session 级聚合，**只对"被测试执行"的模块有效**。conftest 会话结束输出：

```
[MODULE-COVERAGE] 生产模块执行覆盖清单
  生产模块总数: 195  测试已执行: 195  未执行: 0
  ✓ 所有生产模块均被测试执行，全部在泄漏检测覆盖范围内
```

- 未执行模块逐个列出（⚠），超 `CORTEX_TEST_MODULE_UNCOVERED_MAX` 报警
- 当前验证：**195/195 生产模块全覆盖**

## 六、内存看门狗（自动终止）

conftest 启动 daemon 看门狗线程，每 0.5s 采样进程 RSS（psutil），超 `CORTEX_TEST_MEM_LIMIT_MB`（默认 4096MB）立即 `os._exit(1)`。

- 验证：故意累积 60MB + 200MB 上限 → 立即终止并打印 `[MEM-LIMIT]`
- 目的：任何内存失控（真泄漏/失控测试）2s 内被杀，不拖垮本机/CI runner

## 七、运行入口汇总

```bash
# 正常全量（检测默认开启）
python3 -m pytest tests/unit

# 泄漏检测能力验证（10 类）
python3 scripts/verify_leak_detection.py

# 精确定位泄漏
python3 scripts/leak_check.py tests/unit/test_xxx.py

# 内存看门狗低限测试（应自动终止）
CORTEX_TEST_MEM_LIMIT_MB=200 python3 -m pytest tests/unit/test_leak_bytes_raw.py
```

## 八、已知检测边界

- pympler 对纯 C 缓冲（BytesIO 等）有盲区 → 泄漏测试应同时累积内容字节（见 §32 bug）
- 全量 5353 测试进程存活 ~500MB（muppy 估算）/ RSS ~1.1GB，属多文件模块累积（阶梯收敛），非泄漏
- `tracemalloc` 全程跟踪拖慢 20-30 倍，仅用于小范围精确定位
- `resource.setrlimit(RLIMIT_AS)` 在 macOS 不可设低（见 §35 bug），用看门狗替代
