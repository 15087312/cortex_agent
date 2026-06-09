# 打开/关闭应用工具 (open_app/close_app) — 接入说明

## 工具概览

**工具名**: `open_app` 和 `close_app`  
**文件位置**: `/infra/tool_manager/tools/open_application.py`  
**接入日期**: 2026-06-08  
**最后更新**: 2026-06-09（添加 close_app）

打开/关闭应用程序的跨平台工具，自动适配 macOS、Windows、Linux 三端，接口统一。

---

## 一、功能清单

### 核心能力 - open_app

1. **应用启动** — 按应用名、可执行文件路径启动程序
2. **文件打开** — 用默认应用打开文件
3. **URL 打开** — 用默认浏览器打开网址
4. **跨平台自适配** — 同一接口，自动检测系统平台并调用对应实现

### 核心能力 - close_app

1. **应用关闭** — 按应用名自动查找进程并关闭
2. **温和关闭** — 支持 SIGTERM（允许应用保存）或强制关闭 SIGKILL
3. **进程查找** — 自动搜索匹配的进程（无需知道 PID）
4. **跨平台自适配** — 同一接口，自动检测系统平台并调用对应实现

### 平台差异处理

#### open_app

| 场景 | macOS | Windows | Linux |
|------|-------|---------|-------|
| **应用名** | `open -a "AppName"` | `start AppName` | 直接执行应用名 |
| **可执行路径** | `open /path/to/app` | `start C:\path\app.exe` | `xdg-open /path/app` |
| **URL** | `open https://...` | `os.startfile()` | `xdg-open https://...` |
| **文件路径** | `open /path/file` | `os.startfile()` | `xdg-open /path/file` |

#### close_app

| 场景 | macOS | Windows | Linux |
|------|-------|---------|-------|
| **应用关闭** | `pkill -f "应用名"` | `taskkill /IM app.exe /F` | `pkill -f "应用名"` |
| **强制关闭** | SIGKILL（pkill 内置） | `/F` 标志 | SIGKILL（pkill 内置） |
| **温和关闭** | SIGTERM（无 -9）| `/T` 标志 | SIGTERM（无 -9）|

---

## 二、接入工具系统流程

### 2.1 自动加载机制

**无需手动配置**。工具系统已实现自动扫描：

```python
# /infra/tool_manager/tools/__init__.py
# 自动扫描 tools/ 目录，导入所有模块并触发 @ToolRegistry.register 装饰器
for _module_info in pkgutil.iter_modules([str(_package_dir)]):
    if not _module_info.name.startswith("_"):
        importlib.import_module(f".{_module_info.name}", package=__name__)
```

**接入成功验证**：

```bash
$ python3 -c "
from infra.tool_manager.tool_registry import ToolRegistry
from infra.tool_manager.tools import open_application
tools = ToolRegistry.list_tools()
assert 'open_app' in tools
print('✓ open_app 工具已注册')
"
```

### 2.2 注册元数据

工具通过装饰器注册到中心工具库：

```python
@ToolRegistry.register(
    name="open_app",
    description="打开应用程序或文件（跨平台自动适配）",
    params={
        "app_identifier": "应用名称、可执行文件路径、或 URL",
        "wait": "是否等待应用关闭（默认后台运行）",
    },
    source="builtin",           # 内置工具（非插件）
    risk_level="MEDIUM",        # 中等风险（见下文权限控制）
    category="mutation",        # 修改系统状态
    tags=["system", "app_launch"],
    priority=1,                 # 常用工具
    core=True,                  # 在工具 API 中展示完整 schema
)
```

---

## 三、权限控制机制

### 3.1 风险等级划分

```
LOW:      查询类工具，直接放行 → open_app 不属于此类
MEDIUM:   文件修改/应用启动 → ✓ open_app 属于此等级
HIGH:     exec_command/git_push 等，需用户审批
CRITICAL: 系统级危害，硬阻断
```

### 3.2 权限策略

#### 位置 1: `modules/security_system/tool_security_gate.py`

```python
# MEDIUM 风险工具 — 快速路径检查
MEDIUM_RISK_TOOLS = {
    "write_file", "file_edit", "append_file",
    "run_command", "run_python",
    "open_app",  # ← 已添加
    ...
}

# 写操作工具 — plan 模式禁止，edit 模式需用户确认
_MUTATION_TOOLS = {
    "write_file", "file_edit", "append_file",
    "open_app",  # ← 已添加（修改系统状态）
    ...
}
```

#### 权限行为

| 模式 | 行为 |
|------|------|
| **Plan 模式** | ❌ 禁止使用（不在工具列表中） |
| **Edit 模式** | ⚠️ 快速路径检查 + 日志记录（无需显式审批） |
| **自由模式** | ✓ 直接执行 |

### 3.3 安全检查链

工具执行前的检查流程：

```
工具调用请求
    ↓
[1] 风险等级检查 → MEDIUM → 进入 MEDIUM_RISK_TOOLS 路径
    ↓
[2] 参数验证 → app_identifier 必须非空字符串
    ↓
[3] 路径/应用合法性检查
    - 检查路径是否存在（如果是路径）
    - 检查应用是否在 PATH 中（如果是应用名）
    - ✓ 无绝对禁止列表（合法应用都可以打开）
    ↓
[4] 执行 + 审计日志记录
    ↓
返回结果
```

### 3.4 绝对禁止列表

**open_app 没有绝对禁止列表**，因为：

- 应用启动权限由操作系统控制（用户权限、文件权限）
- 恶意软件防护由 antivirus + OS 负责
- agent 无法跨权限边界执行

---

## 四、使用示例

### 使用示例

```python
from infra.tool_manager.tools.open_application import open_app, close_app

# 打开浏览器
result = open_app("Chrome")
# → {"status": "success", "message": "已打开应用: Chrome"}

# 打开网址
result = open_app("https://example.com")
# → {"status": "success", "message": "已在浏览器打开: https://example.com"}

# 打开文件
result = open_app("/path/to/document.pdf")
# → {"status": "success", "message": "已用默认应用打开: /path/to/document.pdf"}

# Windows 路径
result = open_app("C:\\\\Program Files\\\\MyApp\\\\app.exe")
# → {"status": "success", "message": "已启动: C:\\Program Files\\MyApp\\app.exe"}

# ═══════════════════════════════════════════════════════════

# 关闭应用（温和关闭，允许应用保存）
result = close_app("Chrome", force=False)
# → {"status": "success", "message": "已关闭应用: Chrome"}

# 强制关闭应用
result = close_app("Firefox")  # force=True 为默认值
# → {"status": "success", "message": "已关闭应用: Firefox"}

# 关闭进程
result = close_app("python")
# → {"status": "success", "message": "已关闭应用: python"}
```

### 应用别名映射

内置别名（跨平台自动转换）：

```python
APP_NAME_MAP = {
    "chrome": {"darwin": "Google Chrome", "windows": "chrome.exe", "linux": "google-chrome"},
    "firefox": {"darwin": "Firefox", "windows": "firefox.exe", "linux": "firefox"},
    "vscode": {"darwin": "Visual Studio Code", "windows": "code.exe", "linux": "code"},
    "finder": {"darwin": "/System/...", "windows": "explorer.exe", "linux": "nautilus"},
    ...
}

# 无需关心平台，直接用别名
open_app("firefox")  # 自动转换为该平台的名称
```

---

## 与 kill_process 的对比

| 特性 | open_app/close_app | kill_process |
|------|-------------------|--------------|
| **输入** | 应用名（如 "Chrome"） | PID（如 1234） |
| **易用性** | 高（无需知道 PID） | 低（需手动查找 PID） |
| **跨平台** | ✓ 完全自适配 | ✓（但调用方式一致） |
| **风险等级** | MEDIUM | HIGH |
| **审批** | 快速路径 | 需用户审批 |
| **适用场景** | 日常打开/关闭应用 | 异常进程清理 |
| **精度** | 按应用名模糊匹配 | 精确 PID 指定 |

---

## 五、系统集成点

### 5.1 工具发现

agent 通过 `ToolRegistry.get_tools_for_api()` 获取完整工具列表：

```python
tools = ToolRegistry.get_tools_for_api()
# 返回包含 open_app 和 close_app 的完整工具 schema
```

### 5.2 安全审计

所有调用经过 `tool_security_gate.py` 的审计路径：

```
ToolSecurityGate.verify_tool_access(
    tool_name="open_app" | "close_app",
    caller_model_id="claude-xxx",
    args={"app_identifier": "Chrome"}
)
→ 记录审计日志 + 执行权限检查
```

### 5.3 错误恢复

工具返回统一格式：

```python
{
    "status": "success" | "error",
    "message": "详细描述"
}
```

模型可根据 status 判断是否重试。

---

## 六、风险评估与缓解

### 潜在风险

| 风险 | 缓解措施 |
|------|--------|
| 启动恶意软件 | 依赖 OS 防护 + 用户权限限制 |
| 资源耗尽 | 超时控制（5s），进程后台运行 |
| 多窗口打开 | 正常行为，无法禁止 |

### 审批流程

**MEDIUM 风险工具** (`open_app`) 的审批：

1. **Plan 模式**: 完全禁止（工具不在列表中）
2. **Edit 模式**: 允许 + 日志记录（无弹窗）
3. **自由模式**: 直接执行

---

## 七、文件结构

```
infra/tool_manager/tools/
├── __init__.py                    # 自动扫描加载器
├── open_application.py            # ← 新增文件（此工具实现）
├── exec_command.py                # 命令执行工具
├── file_manager.py                # 文件管理工具
└── ...

modules/security_system/
├── tool_security_gate.py          # ← 已修改（添加权限配置）
└── centralized_policy.py          # 集中安全政策
```

---

## 八、验证清单

- [x] 工具文件已创建: `/infra/tool_manager/tools/open_application.py`
- [x] 工具已注册到 `ToolRegistry`
- [x] 权限配置已更新: `tool_security_gate.py` (MEDIUM_RISK_TOOLS 和 _MUTATION_TOOLS)
- [x] 跨平台支持: macOS/Windows/Linux
- [x] 应用别名映射: 常见应用自动转换
- [x] 错误处理: 统一的返回格式和日志记录
- [x] open_app 和 close_app 两个工具已实现
- [x] 文档完整: 本说明

---

## 九、如何修改权限

如果需要调整权限策略，修改以下文件：

### 提升为 HIGH 风险（需用户显式审批）

```python
# modules/security_system/tool_security_gate.py
HIGH_RISK_TOOLS = {
    ...
    "open_app",  # 改为 HIGH 时添加
}
# 从 MEDIUM_RISK_TOOLS 中删除
```

### 添加黑名单应用（禁止打开）

```python
# infra/tool_manager/tools/open_application.py
BLOCKED_APPS = {
    "malware", "dangerous_app",
}

# 在 open_app() 开头添加检查
if app_identifier.lower() in BLOCKED_APPS:
    return {"status": "error", "message": "禁止打开该应用"}
```

---

## 十、FAQ

**Q: 为什么是 MEDIUM 而不是 LOW？**  
A: LOW 级别是纯查询（无副作用），但打开应用会修改系统状态，需要追踪。

**Q: Plan 模式为什么禁止？**  
A: Plan 是制定方案模式，不应执行任何修改系统的操作。

**Q: 可以限制只能打开特定应用吗？**  
A: 可以，在 `open_app()` 中添加白名单检查。

**Q: 支持等待应用关闭吗？**  
A: 支持，通过 `wait=True` 参数（当前实现为后台运行，可扩展）。

---

## 更新历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-08 | 1.0 | 初始实现，接入权限系统 |
| 2026-06-09 | 1.1 | 添加 close_app 工具（关闭应用） |
