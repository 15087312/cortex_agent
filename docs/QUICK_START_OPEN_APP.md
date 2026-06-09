# 快速入门：open_app 和 close_app 工具

## 一句话总结

**一对跨平台应用管理工具，自动适配 macOS/Windows/Linux，接口统一。**

---

## 快速接入（已完成）

### 接入步骤（供参考）

如果要为其他项目接入类似工具，遵循这 3 步：

#### Step 1: 创建工具文件
```
infra/tool_manager/tools/open_application.py
```
- 用 `@ToolRegistry.register()` 装饰主函数
- 定义 `name`, `description`, `params`, `risk_level`, `category`, `tags`

#### Step 2: 更新权限配置
```
modules/security_system/tool_security_gate.py
```
在以下集合中添加工具名：
- `MEDIUM_RISK_TOOLS` (如果是修改系统的操作)
- `_MUTATION_TOOLS` (同上)

#### Step 3: 完成！
系统自动扫描加载，无需手动 import。

---

## 工具如何使用

### 导入并调用

```python
from infra.tool_manager.tools.open_application import open_app, close_app

# 打开应用
result = open_app("Chrome")
print(result)
# {"status": "success", "message": "已打开应用: Google Chrome"}

# 打开网址
result = open_app("https://example.com")
# {"status": "success", "message": "已在浏览器打开: https://example.com"}

# 打开文件
result = open_app("/Users/user/document.pdf")
# {"status": "success", "message": "已用默认应用打开: ..."}

# ═══════════════════════════════════════════════════════════

# 关闭应用（温和关闭，允许应用保存）
result = close_app("Chrome", force=False)
# {"status": "success", "message": "已关闭应用: Chrome"}

# 强制关闭应用
result = close_app("Firefox")
# {"status": "success", "message": "已关闭应用: Firefox"}
```

### 通过 Agent 调用

Agent 自动发现此工具，可以像这样调用：

```
user: 用浏览器打开 https://www.example.com 然后过30秒关闭它

agent:
  [1] 调用工具: open_app
      参数: app_identifier="https://www.example.com"
      结果: {"status": "success", "message": "已在浏览器打开..."}
  
  [2] 等待 30 秒...
  
  [3] 调用工具: close_app
      参数: app_identifier="Chrome"
      结果: {"status": "success", "message": "已关闭应用: Chrome"}
```

---

## 权限与审批

| 场景 | 行为 |
|------|------|
| **Planning 模式** | ❌ 工具不在列表中（禁用写操作） |
| **Editing 模式** | ⚠️ 快速路径检查 + 日志记录 |
| **Free mode** | ✓ 直接执行 |

### 为什么是 MEDIUM 风险？

- **LOW**: 只读工具（无副作用）
- **MEDIUM**: 修改系统状态（启动应用）✓ ← open_app 属于此类
- **HIGH**: 需要用户显式审批（如 exec_command）
- **CRITICAL**: 绝对禁止

---

## 接入清单

- [x] **工具代码**: `/infra/tool_manager/tools/open_application.py` 
  - open_app: 跨平台打开应用（macOS/Windows/Linux）
  - close_app: 跨平台关闭应用（macOS/Windows/Linux）
  - 应用别名映射（自动平台转换）
  - 统一的返回格式

- [x] **权限配置**: `modules/security_system/tool_security_gate.py`
  - 在 `MEDIUM_RISK_TOOLS` 中添加 `"open_app"`, `"close_app"`
  - 在 `_MUTATION_TOOLS` 中添加 `"open_app"`, `"close_app"`

- [x] **自动加载**: `/infra/tool_manager/tools/__init__.py`
  - 无需修改（系统自动扫描）

- [x] **文档**: 
  - `/docs/OPEN_APP_TOOL_INTEGRATION.md` — 完整接入说明
  - `/docs/QUICK_START_OPEN_APP.md` — 快速开始（本文件）

---

## 常见问题

**Q: 工具怎么注册的？**  
A: 通过 `@ToolRegistry.register()` 装饰器，系统自动扫描加载。

**Q: 权限怎么控制？**  
A: 在 `tool_security_gate.py` 中配置风险等级和工具集合。

**Q: 如何禁止打开某个应用？**  
A: 在 `open_application.py` 中添加黑名单检查：
```python
BLOCKED_APPS = {"malware", "dangerous"}
if app_identifier.lower() in BLOCKED_APPS:
    return {"status": "error", "message": "禁止打开该应用"}
```

**Q: Windows 路径格式怎么写？**  
A: 支持正常 Windows 路径，如 `C:\\Program Files\\App\\app.exe`。

**Q: close_app 和 kill_process 有什么区别？**  
A: close_app 更友好（只需应用名，无需 PID），但权限更低（MEDIUM vs HIGH）。

**Q: 支持关闭特定进程实例吗？**  
A: close_app 按名称模糊匹配，如果有多个同名进程会全部关闭。需要精确控制请用 kill_process。

---

## 文件修改汇总

```diff
# 1. 新增文件
+ infra/tool_manager/tools/open_application.py

# 2. 修改：权限配置
~ modules/security_system/tool_security_gate.py
  + "open_app", "close_app" 添加到 MEDIUM_RISK_TOOLS
  + "open_app", "close_app" 添加到 _MUTATION_TOOLS

# 3. 新增文档
+ docs/OPEN_APP_TOOL_INTEGRATION.md
+ docs/QUICK_START_OPEN_APP.md (本文件)
```

---

## 验证工具是否正确接入

```bash
python3 << 'EOF'
from infra.tool_manager.tool_registry import ToolRegistry
from infra.tool_manager.tools import open_application

tools = ToolRegistry.list_tools()
assert 'open_app' in tools
print("✓ open_app 工具已成功接入")
EOF
```

---

## 相关文档

- 📖 完整接入说明: `docs/OPEN_APP_TOOL_INTEGRATION.md`
- 📝 工具实现代码: `infra/tool_manager/tools/open_application.py`
- 🔐 权限系统: `modules/security_system/tool_security_gate.py`
- 🛠 工具注册: `infra/tool_manager/tool_registry.py`

---

**状态**: ✅ 接入完成，已验证（open_app + close_app）  
**日期**: 2026-06-09（更新，添加 close_app）
