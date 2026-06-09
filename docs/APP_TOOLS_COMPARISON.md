# 应用管理工具对比与使用指南

## 工具全景

项目现在有三个与应用/进程相关的工具，各有不同用途：

| 工具 | 输入 | 风险等级 | 审批 | 使用场景 | 易用性 |
|------|------|--------|------|--------|------|
| **open_app** | 应用名/路径/URL | MEDIUM | 快速通过 | 日常：打开浏览器、文件等 | ⭐⭐⭐⭐⭐ 最高 |
| **close_app** | 应用名/进程名 | MEDIUM | 快速通过 | 日常：关闭应用、进程 | ⭐⭐⭐⭐ 很高 |
| **kill_process** | PID（进程ID） | HIGH | 需用户审批 | 异常：强杀僵尸进程 | ⭐ 最低 |

---

## 工具选择决策树

```
我想打开一个应用或文件
  ↓
用 open_app("Chrome") 
或 open_app("https://example.com")
✓ 最简单，跨平台自适配

───────────────────────────────

我想关闭一个应用
  ↓
是否知道应用名？
  ├─ 是 → 用 close_app("Chrome")
  │       ✓ 简单，支持温和关闭
  │
  └─ 否 → 先用 ps aux | grep 找到名称
          再用 close_app()
          ✓ 仍然比 kill_process 容易

───────────────────────────────

我需要精确控制进程
  ↓
是否知道确切的 PID？
  ├─ 是 → 用 kill_process(pid=1234, force=True)
  │       ✓ 精确控制，功能强大
  │
  └─ 否 → 先用工具获取 PID
          再用 kill_process()
```

---

## 使用示例对比

### 场景 1：打开网页并阅读

```python
# ✓ 推荐方案 — close_app
open_app("https://github.com")
# 自动在默认浏览器打开，无需指定应用名
```

### 场景 2：关闭后台应用节省资源

```python
# ✓ 推荐方案 — close_app（温和）
close_app("Chrome", force=False)
# 给应用保存机会，避免数据丢失

# ❌ 不推荐
kill_process(pid=4567)  # 需要知道 PID，且强杀无机会保存
```

### 场景 3：编写自动化脚本

```python
# 启动开发服务器，运行测试，然后关闭
open_app("terminal")
# ... 运行命令 ...
close_app("python", force=True)  # 测试完成，强行结束

# ✓ 简洁直观
```

### 场景 4：处理僵尸进程

```python
# 进程名称不清楚，但知道 PID
# ✓ 唯一选择
kill_process(pid=9999, force=True)  # 强杀僵尸进程

# 或如果知道进程名
close_app("zombie_process", force=True)
```

---

## 权限与安全性

### Plan 模式（方案规划）

```
❌ open_app  → 禁止（会修改系统状态）
❌ close_app → 禁止（会修改系统状态）
❌ kill_process → 禁止（属于 HIGH 风险）

即使是 MEDIUM 工具也被禁用，确保 Plan 阶段不执行任何操作
```

### Edit 模式（编辑/修改）

```
✓ open_app  → 允许（MEDIUM 风险，快速路径）
✓ close_app → 允许（MEDIUM 风险，快速路径）
❌ kill_process → 需要用户审批（HIGH 风险）

MEDIUM 工具在 Edit 模式下无需显式审批，仅记录日志
```

### 自由模式

```
✓ open_app  → 直接执行
✓ close_app → 直接执行
✓ kill_process → 直接执行

所有工具都可使用
```

---

## 跨平台细节

### open_app

| 系统 | 实现 | 例子 |
|------|------|------|
| macOS | `open -a "应用名"` | `open_app("Chrome")` → `open -a "Google Chrome"` |
| Windows | `start 应用名` | `open_app("Chrome")` → `start chrome.exe` |
| Linux | `xdg-open` 或直接执行 | `open_app("Firefox")` → 直接执行 firefox 命令 |

### close_app

| 系统 | 实现 | 例子 |
|------|------|------|
| macOS | `pkill -f "进程名"` | `close_app("python")` → 查找并关闭 python 进程 |
| Windows | `taskkill /IM 应用名.exe` | `close_app("python")` → `taskkill /IM python.exe` |
| Linux | `pkill -f "进程名"` | `close_app("node")` → 查找并关闭 node 进程 |

---

## 常见错误与修复

### ❌ 错误 1：使用了 Plan 模式中不允许的工具

```python
# Plan 模式
open_app("Chrome")  # ❌ AttributeError: tool not available in plan mode

# 解决方案：切换到 Edit 模式或自由模式
```

### ❌ 错误 2：close_app 匹配了多个进程

```python
close_app("python")  
# 如果有多个 python 进程会全部关闭
# 解决方案：
#   1. 更具体的应用名（如 "jupyter" 而不是 "python"）
#   2. 使用 kill_process(pid=xxx) 精确控制
```

### ❌ 错误 3：kill_process 权限不足

```python
kill_process(pid=other_user_process)
# ❌ PermissionError: Only current user processes can be killed

# 解决方案：
#   1. 只能 kill 当前用户的进程
#   2. 要 kill 其他用户的进程需要 sudo（security gate 会阻止）
```

---

## 何时添加新工具

- **需要新工具吗**？通常不需要
- **open_app + close_app + kill_process** 已覆盖 99% 的需求
- 特殊场景示例：
  - 需要监听进程输出 → 扩展 open_app 的 wait 参数
  - 需要资源限制 → 添加 limit_process 工具
  - 需要进程通信 → 添加 send_signal_to_process 工具

---

## 参考文档

- 📖 完整接入说明: `docs/OPEN_APP_TOOL_INTEGRATION.md`
- 📝 快速开始: `docs/QUICK_START_OPEN_APP.md`
- 🔐 安全门控: `modules/security_system/tool_security_gate.py`
- 🛠 工具注册: `infra/tool_manager/tool_registry.py`

---

**更新日期**: 2026-06-09  
**状态**: 三个工具已完整接入，可投入使用
