# 错误原因与修复记录 · 流程 · 审计方法论 / 文档治理

[← 返回总索引](./README.md) | [English](./09-process-docs.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 2 节：§15、§38。

---
## 15. 补充历史经验（此前未记录）

### 15.1 限流额度被高频轮询耗尽 → 正常操作 429（后端）

**现象：** 健康检查/桌宠轮询后，用户正常打开页面也报"请求频率超限"，日志刷 `限流触发: 127.0.0.1 (GET /xxx)`。

**根因：** `api/main.py` 限流中间件每 IP 每分钟 100 次，白名单只有 `/stream/pet/move`；`/health`（每秒多次）+ 桌宠轮询（`last-reply`/`state`）把额度耗尽，之后所有请求 429。

**修复：** ① 高频只读轮询端点（`/health`、`/stream/pet/*`、`/stream/status`、`/stream/sessions`、`/config`、`/dashboard`、`/metrics`）加入限流白名单；② 本地回环（127.0.0.1/::1）上限放宽到 1000/分钟，公网仍 100。

**同类排查：** 加新端点时区分"高频轮询（只读状态）"与"业务操作"——前者应进限流白名单/日志忽略名单，否则会互相挤占额度。

### 15.2 macOS 截图 could not create image from display（后端）

**现象：** 终端反复打印 `could not create image from display`（每次截图一次）。

**根因（两层）：**
1. macOS 上 `PIL ImageGrab.grab()` 走 X11，主动打印该错误到 fd（`except` 拦不住）；
2. 回退的 `screencapture` 子进程错误输出未捕获，直接继承到父终端。

**修复：** `utils/screen_capture.py` macOS 跳过 ImageGrab 直接用 `screencapture`，`subprocess.run(..., capture_output=True)` 捕获子进程输出；`_try_screencapture` 用 `try/finally` 删除临时文件（曾泄漏 3.8 万个临时 png）。

**同类排查：** 所有 `subprocess.run` 未捕获 stdout/stderr 的调用——子进程错误会泄漏到父进程终端。

### 15.3 时段判断用字符串比较 → 跨午夜永不触发（后端）

**现象：** 主动搭话 `time_windows` 配 `22:00-02:00`（跨午夜）永不触发。

**根因：** `_check_time_windows` 用 `start <= cur <= end` **字符串比较**——`"23:38" <= "02:00"` 恒 False。

**修复：** 改为分钟数数值比较，`end < start` 视为跨午夜（`cur >= s or cur <= e`）。

**同类排查：** 任何 `"HH:MM"` 时间比较都应转分钟数再比，字符串比较在跨天/整点边界必然出错。

### 15.4 模板误用未定义变量（前端）

**现象：** 打开因果图/编排图报 `Cannot read properties of undefined (reading 'type'/'color')` → ErrorBoundary 显示"页面加载失败"。

**根因：** 模板里 `v-for="node in ..."` 但表达式写 `{{ n.type }}` / `n.color`（变量名笔误 `n` vs `node`）——Vue 编译不报未定义变量，运行时渲染才崩。

**修复：** `n` → `node`（Causal.vue / Graph.vue 共 7 处）。

**同类排查：** 写脚本提取模板根标识符对照 script 定义 + v-for 局部变量，唯一真 bug 模式是"v-for 变量笔误"；`:style`/`:class` 对象键、箭头函数参数、`(v,k)` 第二变量均为误报。

**经验：** Vue 模板未定义变量在构建期不报错、运行期才崩——新增/重构页面后**务必打开每个页面实测**，或跑模板变量静态检查。

---

## 38. bug 文档系统性审计 —— 发现 2 处同类问题并修复

**背景：** 对 §1-37 逐条提取根因模式，在代码中搜索同类问题。

**审计范围与结论（大部分模式已正确处理）：**

| 模式 | 结论 |
|---|---|
| §29 缺 `import sys` | ✅ 无遗漏（全量搜索 `sys.stderr/stdout` 使用处均已 import） |
| §31 `sys.modules` 模块级置 None | ⚠️ **发现 2 处同类** → 修复 |
| §34 硬编码版本断言 | ✅ 均为测试内部假数据，非项目版本断言 |
| §24 线程内事件循环自锁 | ✅ frontend_channel/api_stream 已处理（注释明确避免自锁） |
| §30 后台线程遗留 | ✅ 已全面 weakref 注册表 + conftest 清理 |
| §28 无鉴权 WS | ✅ chat_gateway 显式 `_ws_auth_ok` + 4401 拒绝，test_ws_auth 覆盖 |
| §21 主动搭话绕过开关 | ✅ trigger gate 52 处 enabled/allowed 测试充分 |
| §25 工具调用被套人设 | ✅ model_runner 经 PromptComposer 统一构建；工具型调用用 `_NEUTRAL_SYSTEM_PROMPT` |
| §37 `__del__` 陷阱 | ⚠️ **发现 1 处同类** → 修复 |

**同类 Bug 1（§31 型）：** `test_mcp_screen_diff.py` / `test_mcp_screen_monitor.py` 模块级 `sys.modules["rapidocr_onnxruntime"] = None` 永久污染——conftest 的 `block_real_native_libs` 已全局置 None，此处冗余且保留"模块级污染"反模式。
**修复：** 移除两个文件的模块级置 None，统一由 conftest 管理。验证 93 passed。

**同类 Bug 2（§37 型）：** `causal_graph.CausalGraph.__del__` 直接 `self.close()`，无 `sys.is_finalizing()` 防护、无 try/except——GC/解释器关闭期若 builtins/模块已清理，`__del__` 抛异常可能触发 GC 死循环。
**修复：** 对齐 `event_store.__del__` 防护模式：`is_finalizing()` 提前返回 + try/except 吞异常。验证 171 passed。

**经验：** 把已知 bug 的根因做成"可搜索模式"清单（`sys.modules` 污染 / `__del__` 无防护 / 缺 import / 版本硬编码），每次审计按清单批量 grep，能高效发现同类问题。
