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

### 10. ~~Value System 非原子写入~~ ✅ 已修复

- **修复**：`save()` 改为 `tempfile.mkstemp` → `fsync` → `os.replace` 原子替换，崩溃时自动清理临时文件。

### 11. ~~_extract_colors 全像素遍历~~ ✅ 已删除

- **修复**：`_extract_colors()` 方法已删除（死代码，仅 REST API 端点调用，无已知客户端）。`_analyze_qwen_vl()` 中的调用替换为 `"colors": []`。

### 12. ~~analyze_url 同步阻塞~~ ✅ 已删除

- **修复**：`analyze_url()` 方法已删除（死代码，仅 REST API 端点调用，无已知客户端）。对应的 `/image/analyze-url` REST 端点一并删除。感知系统中的 `analyze_screen_with_api()` 及其依赖 `_get_info_process_api()` 也一并清理。

### 13. ~~使用已废弃的 asyncio.get_event_loop()~~ ✅ 已修复

- **修复**：全部 11 处生产代码替换为 `asyncio.get_running_loop()`。sync 上下文中用 `try: get_running_loop() / except RuntimeError:` 结构。docstring 示例同步更新。

### 14. ~~_parse_version 静默失败~~ ✅ 已修复

- **修复**：`_parse_version()` 返回 `None` 而非零值。6 个调用方全部加 `if parsed is None: return False` 守卫。

### 15. ~~IPv6 Rate-Limit Key 解析 Bug~~ ✅ 已修复

- **修复**：key 格式从 `{ip}:{minute}` 改为 `{ip}|{minute}`，cleanup 函数同步更新

---

## P2 — 低优先级（代码整洁）

### 16. ~~死代码~~ ✅ 已删除

- **修复**：删除 `thought_splitter.py`（空 stub）、`systems/__init__.py`（废弃存根）。清理 `utils/__init__.py` 的 re-export。

### 17. ~~未使用的导入~~ ✅ 已修复

- **修复**：`api/main.py` 中删除未使用的 `import hashlib`。

### 18. ~~日志级别过高~~ ✅ 已修复

- **修复**：`logging_middleware` 从 `logger.info` 改为 `logger.debug`。

### 19. 单例模式不统一

- **问题**：项目中存在 8+ 种不同的单例实现方式
- **影响**：新开发者难以理解，容易引入 bug
- **位置**：全局
- **修复**：提取统一的 `@singleton` 装饰器或基类

### 20. ~~__build_date__ 硬编码~~ ✅ 已修复

- **修复**：改为 `datetime.date.today().isoformat()` 动态生成。

### 21. ~~Security Monitor 正则可绕过~~ ✅ 已修复

- **修复**：`rm -rf` 正则改为匹配 `-rf`/`-fr`/分开写/长参数/混合写法。`chmod 777` 同时匹配 `0777`。

### 22. ~~路径匹配未规范化~~ ✅ 已修复

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
| P1-10 | Value System 非原子写入 | tempfile + fsync + os.replace 原子写入 | 2026-06-08 |
| P1-11 | _extract_colors 全像素遍历 | 已删除（死代码） | 2026-06-08 |
| P1-12 | analyze_url 同步阻塞 | 已删除（死代码） | 2026-06-08 |
| P1-13 | asyncio.get_event_loop() 废弃 | 全部替换为 get_running_loop() | 2026-06-08 |
| P1-14 | _parse_version 静默失败 | 返回 None + 调用方守卫 | 2026-06-08 |
| P1-15 | IPv6 rate-limit key 解析 bug | key 分隔符从 `:` 改为 `\|` | 2026-06-08 |
| P2-16 | 死代码 | 删除 thought_splitter.py + systems/__init__.py | 2026-06-08 |
| P2-17 | 未使用的导入 | 删除 `import hashlib` | 2026-06-08 |
| P2-18 | 日志级别过高 | logger.info → logger.debug | 2026-06-08 |
| P2-20 | __build_date__ 硬编码 | datetime.date.today().isoformat() | 2026-06-08 |
| P2-21 | SecurityMonitor 正则可绕过 | 扩展 rm/chmod 正则模式 | 2026-06-08 |
| P2-22 | 路径匹配未规范化 | P1#9 一并修复 | 2026-06-08 |

---

## 跟踪建议

- **P0 问题**：建议在下一个 sprint 中修复
- **P1 问题**：建议在当前版本周期内逐步修复
- **P2 问题**：可在代码审查时顺带修复
