# 已知问题清单

> 基于全项目逐文件分析，按优先级分类。评估时间：2026-06-08

---

## P0 — 高优先级（生产风险）

### 1. ~~版本号不一致~~ ✅ 已修复

- **修复**：pyproject.toml 改为 `dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {file = "VERSION"}`；api/main.py 从 `cortex.version.__version__` 读取

### 2. ~~Pipe Buffer Deadlock~~ ✅ 已修复

- **修复**：`cortex/main.py` 中 `stdout=subprocess.PIPE` 改为 `subprocess.DEVNULL`

### 3. ~~Config Mutation Bypasses Validation~~ ✅ 已修复

- **修复**：`update_config` 端点改用 `TypeAdapter(field_info.annotation).validate_python(body.value)` 触发 Pydantic 校验，校验失败返回 422

### 4. ~~Rate Limiter Per-Process~~ ✅ 已修复

- **修复**：`cortex/main.py` 强制 `workers` 最大为 1，限流使用单进程内存计数。多 worker 需要分布式限流（Redis），当前架构不支持。

### 5. exec_command 极端命令硬阻断 + 快照兜底 ✅ 已修复

- **修复**：新增 `_EXTREME_DANGER_PATTERNS` 列表（`rm -rf /`、fork bomb、`mkfs`、`dd`、reverse shell listener），匹配时直接返回 `{"blocked": True}` 不执行。其余危险命令保留快照+警告策略。

---

## P1 — 中优先级（代码质量 / 潜在风险）

### 6. ~~asyncio.Lock 单例跨事件循环失效~~ ✅ 已修复

- **修复**：`MessageBus._lock` 改为 `@property lock`，每次访问检测当前事件循环 ID，变化时自动重建 `asyncio.Lock`

### 7. 大文件需拆分

- **问题**：`model_runner.py`（2345行）、`continuous_thinker.py`（1265行）职责过多
- **影响**：可维护性差，难以定位 bug，代码审查困难
- **位置**：`modules/thinking/core/model_runner.py`、`modules/thinking/core/continuous_thinker.py`
- **修复**：按职责拆分为多个文件（lifecycle、delegation、tool_execution、prompt_building 等）

### 8. ~~AppError 未调用 super().__init__~~ ✅ 已修复

- **修复**：`AppError.__init__` 中添加 `super().__init__(message)`

### 9. ~~路径安全检查重复实现~~ ✅ 已修复

- **修复**：统一到 `SecurityPolicy`（`centralized_policy.py`）。`file_manager.py` 和 `file_extra.py` 的本地实现全部改为委托 `get_security_policy()`。补齐 `file_exists`、`get_file_info`、`append_file` 的路径检查。修复 fail-open 为 fail-closed。修复 macOS symlink（`/etc → /private/etc`）导致的路径匹配失败。

### 10. Value System 非原子写入

- **问题**：`ValueSystem.save()` 直接写入文件，无 write-then-rename 原子操作
- **影响**：写入过程中崩溃可能损坏 `core_values.txt`
- **位置**：`modules/thinking/evolution/value_system.py`
- **修复**：写入临时文件后 `os.rename` 替换

### 11. _extract_colors 全像素遍历

- **问题**：`ImageAnalyzer._extract_colors()` 使用 `list(image.getdata())` 遍历所有像素
- **影响**：大图片（如 4K 截图）极慢且内存占用高
- **位置**：`infra/data_process/core/image_analyzer.py`
- **修复**：使用 `image.resize((1, 1))` 或 `collections.Counter` 采样

### 12. analyze_url 同步阻塞

- **问题**：`ImageAnalyzer.analyze_url()` 内部使用同步 `requests.get()`
- **影响**：阻塞 asyncio 事件循环，影响并发请求处理
- **位置**：`infra/data_process/core/image_analyzer.py`
- **修复**：使用 `aiohttp` 或 `asyncio.to_thread(requests.get, ...)`

### 13. 使用已废弃的 asyncio.get_event_loop()

- **问题**：多处使用 `asyncio.get_event_loop().create_task()`，Python 3.10+ 已废弃
- **影响**：可能返回已关闭的事件循环，抛出 RuntimeError
- **位置**：`modules/thinking/probes/probe_tools.py`、部分 expert 文件
- **修复**：使用 `asyncio.get_running_loop()` + try/except fallback

### 14. _parse_version 静默失败

- **问题**：`VersionManager._parse_version()` 解析失败时返回 `(0, 0, 0, None)` 而非抛异常
- **影响**：损坏的 VERSION 文件会导致 `increment_patch()` 写入 `0.0.1`
- **位置**：`cortex/version_manager.py`
- **修复**：解析失败时抛出 `ValueError` 或返回 `None`

### 15. ~~IPv6 Rate-Limit Key 解析 Bug~~ ✅ 已修复

- **修复**：key 格式从 `{ip}:{minute}` 改为 `{ip}|{minute}`，cleanup 函数同步更新

---

## P2 — 低优先级（代码整洁）

### 16. 死代码

- **问题**：`thought_splitter.py` 的 `split()` 直接返回 `[thought]`；`systems/__init__.py` 是废弃存根
- **位置**：`modules/thinking/utils/thought_splitter.py`、`modules/thinking/systems/__init__.py`
- **修复**：删除文件

### 17. 未使用的导入

- **问题**：`api/main.py` 导入 `defaultdict` 但未使用
- **位置**：`api/main.py`
- **修复**：删除导入

### 18. 日志级别过高

- **问题**：`logging_middleware` 每个请求都以 INFO 级别记录，生产环境日志量大
- **位置**：`api/main.py`
- **修复**：改为 DEBUG 级别或添加采样

### 19. 单例模式不统一

- **问题**：项目中存在 8+ 种不同的单例实现方式
- **影响**：新开发者难以理解，容易引入 bug
- **位置**：全局
- **修复**：提取统一的 `@singleton` 装饰器或基类

### 20. __build_date__ 硬编码

- **问题**：`version.py` 中 `__build_date__` 硬编码为 `"2026-06-07"`
- **影响**：发布时显示错误的构建日期
- **位置**：`cortex/version.py`
- **修复**：从文件修改时间或构建系统动态获取

### 21. Security Monitor 正则可绕过

- **问题**：`_check_forbidden_commands` 的 `\brm\s+-rf\b` 正则无法匹配 `rm -r -f` 或 `rm -r --force`
- **影响**：可被简单变体绕过
- **位置**：`modules/thinking/experts/security_monitor.py`
- **修复**：使用更全面的模式或 AST 级别检测

### 22. 路径匹配未规范化

- **问题**：`SecurityPolicy` 使用 `startswith()` 匹配路径，未做规范化
- **影响**：`/etc/passwd-` 会匹配 `/etc/passwd` 前缀
- **位置**：`infra/security/centralized_policy.py`
- **修复**：使用 `pathlib.Path.resolve()` 规范化后比较

---

## 已修复问题记录

以下问题在之前的迭代中已修复，记录于此供参考：

| 编号 | 问题 | 修复内容 | 修复时间 |
|------|------|---------|---------|
| CONC-5 | ToolRegistry._tools 无锁保护 | 添加 `threading.RLock` | 2026-06 |
| CONC-6 | LiteModelClient 单例非线程安全 | 添加 `threading.Lock` | 2026-06 |
| CONC-8 | SessionLifecycle 等待 runner 时持锁导致死锁 | 移到锁外等待 | 2026-06 |
| RES-1 | ModelClient 每次调用创建新 session | 改为会话池化 | 2026-06 |
| SEC-15 | Prompt injection 未做防御 | 添加 XML 标签包裹 + 转义 | 2026-06 |
| S7 | WebSocket 消息乱序 | 添加 per-session 请求队列 | 2026-06 |
| P0-1 | 版本号不一致 | pyproject.toml 动态版本 + api 读取 cortex.version | 2026-06-08 |
| P0-2 | Pipe buffer deadlock | `stdout=subprocess.DEVNULL` | 2026-06-08 |
| P0-3 | Config 绕过 Pydantic 校验 | TypeAdapter 校验 + object.__setattr__ | 2026-06-08 |
| P0-4 | Rate limiter per-process 限流失效 | 强制 workers=1，单进程内存计数 | 2026-06-08 |
| P0-5 | exec_command 极端命令无硬阻断 | `_EXTREME_DANGER_PATTERNS` 硬拦截 + 快照兜底 | 2026-06-08 |
| P1-8 | AppError stack trace 无消息 | 添加 `super().__init__(message)` | 2026-06-08 |
| P1-9 | 路径安全检查三处重复 | 统一到 SecurityPolicy + 补齐覆盖 + fail-closed | 2026-06-08 |
| P1-15 | IPv6 rate-limit key 解析 bug | key 分隔符从 `:` 改为 `\|` | 2026-06-08 |

---

## 跟踪建议

- **P0 问题**：建议在下一个 sprint 中修复
- **P1 问题**：建议在当前版本周期内逐步修复
- **P2 问题**：可在代码审查时顺带修复
