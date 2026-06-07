# 插件系统 (Plugin System)

> 安全沙箱 · 权限网关 · 多 Provider 导出 · 审计链 · 治理管控 · 生产级打包

---

## 概述

插件系统为 Humanoid AGI 提供**安全、可控、可审计**的工具扩展能力。每个插件以独立目录运行在沙箱中，通过 Gateway 访问宿主资源（记忆、网络、文件系统、输出通道），所有调用经过权限校验、Schema 验证、治理管控和审计记录。

**核心安全原则**：插件代码不可信，所有资源访问必须经过 Gateway 中间层，绝不直接暴露宿主 API。

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Model / Expert 调用层                         │
│  Provider 适配 (OpenAI / Anthropic / Generic)                       │
│  PluginToolService.list_tools()  ·  invoke_tool_call()              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                       治理层 (Governance)                            │
│  预算控制 · 确认令牌 · 幂等去重 · 限流 · 循环检测 · 风暴防护           │
│  ToolGovernanceController                                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                        引擎层 (Engine)                               │
│  插件生命周期 · 沙箱管理 · 工具调用 · 事件分发 · 断路器               │
│  PluginEngine                                                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                        网关层 (Gateway)                              │
│  权限校验 · 记忆读写 · 网络请求 · 文件系统 · 输出发送 · 事件发布      │
│  PluginGateway → GatewayClient (注入到插件的 api 参数)               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                        插件沙箱 (Sandbox)                            │
│  sub_process 隔离 · bubblewrap · 超时控制 · 并发限制                 │
│  plugin.yaml + src/main.py + config_schema.json                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
modules/plugin_system/              # 插件系统核心代码
├── engine.py                       # PluginEngine — 核心引擎
├── gateway.py                      # PluginGateway — 权限网关
├── loader.py                       # 插件发现、安装、权限管理
├── models.py                       # 数据模型 (PluginMetadata, InstalledPlugin 等)
├── config.py                       # 插件配置管理器
├── tool_service.py                 # PluginToolService — 统一工具入口
├── tool_governance.py              # 治理：预算、确认、幂等、限流
├── tool_security.py                # 权限注册表
├── tool_errors.py                  # 错误码与安全消息
├── tool_result.py                  # 结果脱敏
├── tool_adapter.py                 # ToolRegistry 桥接
├── tool_manager_adapter.py         # 旧版 ToolManager 适配器
├── model_loop_adapter.py           # 模型循环适配器
├── llm_tools.py                    # LLM 工具目录
├── provider_tools.py               # Provider 专属 Schema 导出
├── schema_validation.py            # JSON Schema 校验
├── audit.py                        # 审计日志 (JSONL + SHA-256 哈希链)
├── policy.py                       # 生产策略引擎
├── sandbox.py                      # 沙箱管理器
├── signing.py                      # Ed25519 / HMAC 签名
├── scaffold.py                     # 脚手架生成器
├── manifest_lint.py                # 清单 Lint 检查
├── status.py                       # 平台状态快照
├── templates/                      # 模板目录 (8 个模板)
│   ├── hello_world/
│   ├── read_only_memory_tool/
│   ├── readonly_retrieval_plugin/
│   ├── file_summary_plugin/
│   ├── network_lookup_tool/
│   ├── controlled_network_plugin/
│   ├── admin_only_output_tool/
│   └── production_plugin_package/
├── PLUGIN_AUTHOR_GUIDE.md          # 插件作者指南
├── PLUGIN_MANIFEST.md              # 清单格式参考
├── TOOL_SERVICE_CONTRACT.md        # 工具服务契约
├── INTEGRATION_CONTRACT.md         # 集成契约
├── PRODUCTION_EVIDENCE.md          # 生产证据要求
├── EXTERNAL_GOVERNANCE.md          # 外部治理说明
└── WINDOWS_SANDBOX_LIMITS.md       # Windows 沙箱限制

data/plugins/                       # 已安装插件目录
├── my_plugin/
│   ├── plugin.yaml                 # 清单（必须）
│   ├── config_schema.json          # 配置 Schema（必须）
│   ├── README.md
│   ├── src/
│   │   ├── __init__.py
│   │   └── main.py                 # 工具函数实现（必须）
│   └── tests/
│       └── test_my_tool.py
```

---

## 快速开始

### 1. 生成插件脚手架

```bash
# 从零生成
python -m modules.plugin_system.scaffold data/plugins my_plugin --tool-name my_tool

# 基于模板生成（推荐）
python -m modules.plugin_system.scaffold data/plugins my_plugin --template hello_world
```

插件名规则：`^[a-z][a-z0-9_]{1,63}$`（小写字母开头，仅含小写字母、数字、下划线，2-64 字符）。

### 2. 编辑 plugin.yaml

```yaml
name: my_plugin
version: "0.1.0"
description: "插件功能描述"
author: "作者名"
license: MIT

extensions:
  - type: tool
    name: my_tool                          # 工具名
    entry: src.main:my_tool                # 模块:函数
    description: "工具描述，会被模型看到"
    params:                                # 入参 JSON Schema
      type: object
      properties:
        query:
          type: string
          maxLength: 100
      required: [query]
      additionalProperties: false
    returns:                               # 返回值 JSON Schema
      type: object
      properties:
        result:
          type: string
          maxLength: 500
      required: [result]
      additionalProperties: false
    permissions:
      - compute: true

permissions:
  - compute: true

runtime:
  mode: sub_process
  trust: third_party
  timeout_seconds: 5
  max_concurrency: 1
```

### 3. 实现工具函数

```python
# src/main.py

def my_tool(args, api=None):
    """
    参数:
      args: dict — 模型传入的参数（已通过 Schema 校验）
      api:  GatewayClient — 宿主注入的网关客户端，可为 None（dry run）
    返回:
      JSON-serializable 的 dict
    """
    query = str(args["query"]).strip()

    # 访问宿主资源必须通过 api
    # api.read_memory(key)
    # api.write_memory(key, value)
    # api.read_config(key)
    # api.network_request(url, method="GET")
    # api.read_file(path)
    # api.write_file(path, content)
    # api.publish_event(event, data)
    # api.send_output(content, channel, content_type)

    return {"result": f"处理结果: {query}"}
```

### 4. 本地检查

```bash
# 清单 Lint（检查权限、Schema、安全风险、Provider 导出兼容性）
python -m modules.plugin_system.manifest_lint data/plugins/my_plugin

# 查看平台状态
python -m modules.plugin_system.status --json

# 生产策略检查
python -m modules.plugin_system.production_policy_check

# 工具自测
python -m modules.plugin_system.tool_selftest

# 模块自测
python -m modules.plugin_system.selftest
```

---

## 清单格式 (plugin.yaml) 详解

### 顶层字段

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `name` | string | ✓ | 插件名，匹配 `^[a-z][a-z0-9_]{1,63}$` |
| `version` | string | ✓ | 语义化版本 |
| `description` | string | ✓ | 插件描述 |
| `author` | string | ✓ | 作者 |
| `license` | string | | 许可证 |
| `extensions` | list | ✓ | 扩展定义（工具、中间件等） |
| `permissions` | list | ✓ | 插件级权限上限 |
| `runtime` | object | | 运行时配置 |

### extensions 字段

```yaml
extensions:
  - type: tool                          # 扩展类型
    name: tool_name                     # 工具名
    entry: src.main:function_name       # 入口：模块路径:函数名
    description: "工具描述"              # 会暴露给模型
    params: { ... }                     # 入参 JSON Schema
    returns: { ... }                    # 返回值 JSON Schema
    permissions:                        # 工具级权限（必须是插件级权限的子集）
      - permission_type: scope
```

### runtime 字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `sub_process` | 沙箱模式（生产必须为 `sub_process`） |
| `trust` | `third_party` | 信任等级 |
| `timeout_seconds` | `5` | 单次调用超时（网络工具建议 8s） |
| `max_concurrency` | `1` | 最大并发调用数 |
| `memory_mb` | | 内存限制 |
| `cpu_seconds` | | CPU 时间限制 |

---

## 权限系统

### 权限类型与风险等级

| 权限 | 风险等级 | 说明 | 是否需要 scope |
|------|----------|------|---------------|
| `compute` | 🟢 低 | 纯计算，无副作用 | 否 |
| `memory.read` | 🟡 中 | 读取记忆存储 | 否 |
| `config.read` | 🟡 中 | 读取配置 | 否 |
| `fs.read` | 🟠 高 | 读取文件系统 | 是（路径模式） |
| `fs.write` | 🟠 高 | 写入文件系统 | 是（路径模式） |
| `network.outbound` | 🔴 高 | 出站网络请求 | 是（URL 模式） |
| `memory.write` | 🔴 危险 | 写入记忆存储 | 否 |
| `output.send` | 🔴 危险 | 发送输出消息 | 否 |

### 权限声明方式

```yaml
# 插件级权限（上限）
permissions:
  - compute: true
  - fs.read:
      - path: data/input/*              # 路径通配符
  - network.outbound:
      - url: https://api.example.com/*  # URL 通配符
        methods: [GET]                   # 限制 HTTP 方法

# 工具级权限（必须是插件级的子集）
extensions:
  - type: tool
    name: my_tool
    permissions:
      - fs.read:
          - path: data/input/*
```

### ⚠️ 网络权限陷阱

```yaml
# ❌ 错误：这不是"允许所有网络"
permissions:
  - network.outbound: true

# ✓ 正确：必须声明具体的 URL 模式
permissions:
  - network.outbound:
      - url: https://api.example.com/*
        methods: [GET]
```

`network.outbound: true` 的语义是"声明了权限但未指定目标"，实际会**阻断**所有请求。

### 角色可见性

| 工具风险 | model（普通模型） | expert（专家） | admin（管理员） |
|----------|------------------|---------------|----------------|
| 低 (compute) | ✓ 可见 | ✓ 可见 | ✓ 可见 |
| 中 (memory/fs.read) | 视配置 | ✓ 可见 | ✓ 可见 |
| 高 (network/fs.write) | ✗ 隐藏 | ✓ 需确认 | ✓ 需确认 |
| 危险 (output/memory.write) | ✗ 隐藏 | ✗ 隐藏 | ✓ 需确认 |

---

## Gateway API（宿主资源访问）

插件通过函数的 `api` 参数访问宿主资源，**禁止直接使用** `requests`、`httpx`、`open()`、`socket` 等。

### 可用方法

| 方法 | 权限 | 说明 |
|------|------|------|
| `api.read_memory(key)` | `memory.read` | 读取记忆记录 |
| `api.write_memory(key, value)` | `memory.write` | 写入记忆记录 |
| `api.read_config(key)` | `config.read` | 读取配置项 |
| `api.network_request(url, method, **kwargs)` | `network.outbound` | HTTP 请求（GET/POST，5s 超时，64KB body 限制） |
| `api.read_file(path)` | `fs.read` | 读取插件数据目录内文件 |
| `api.write_file(path, content)` | `fs.write` | 写入插件数据目录内文件 |
| `api.publish_event(event, data)` | — | 发布事件到事件总线 |
| `api.send_output(content, channel, content_type)` | `output.send` | 发送输出消息 |

### 网络安全特性

Gateway 的网络请求具有以下安全防护：

- **协议限制**：仅允许 HTTPS
- **方法限制**：仅允许 GET、POST
- **URL 长度限制**：最长 2048 字符
- **请求体限制**：POST 最大 64 KB
- **响应限制**：最大 1 MB
- **超时范围**：0.1s ~ 10.0s
- **禁止自定义请求头**
- **禁止 HTTP 重定向**
- **DNS 防护**：阻止重绑定攻击，阻止访问元数据服务（169.254.169.254）
- **IP 防护**：阻止回环、私有、链路本地、多播、保留地址

### 文件系统安全特性

- 路径穿越防护（拒绝 `..`、绝对路径）
- 符号链接防护（`O_NOFOLLOW`）
- 限制在插件数据目录内

---

## 治理管控 (Governance)

### 预算控制

| 角色 | 单次会话调用上限 | 高风险调用上限 | 每分钟调用上限 | 结果字节上限 |
|------|-----------------|---------------|---------------|-------------|
| model | 8 | 1 | 10 | 256 KB |
| expert | 24 | 8 | 30 | 1 MB |
| admin | 64 | 32 | 60 | 4 MB |

### 确认令牌

高风险工具（`network.outbound`、`fs.write`、`memory.write`、`output.send`）需要人工确认：

1. 模型首次调用 → 返回 `CONFIRMATION_REQUIRED` + 令牌
2. 模型携带令牌再次调用 → 验证通过后执行
3. 令牌绑定：角色、会话、工具名、参数哈希、权限
4. 令牌有效期：300 秒
5. 参数变更 → 令牌失效

### 幂等保护

副作用工具自动启用幂等去重：

- 相同参数重复调用 → 返回缓存结果（`DUPLICATE_TOOL_CALL`）
- 执行中的重复调用 → 返回 `DUPLICATE_IN_PROGRESS`
- 参数不一致的重复调用 → 返回 `IDEMPOTENCY_CONFLICT`

### 风暴防护

- 连续相同参数调用 ≥ 4 次 → 拒绝（`TOOL_LOOP_DETECTED`）
- 被拒调用 ≥ 4 次 → 限流（`TOOL_STORM_RATE_LIMITED`）

---

## Provider 适配

插件工具可导出为三种 Provider 格式，供不同模型 API 调用：

### OpenAI (Chat Completions / Responses)

```json
{
  "type": "function",
  "function": {
    "name": "my_plugin__my_tool",
    "description": "工具描述",
    "parameters": { "type": "object", "properties": { ... } }
  }
}
```

### Anthropic (tool_use)

```json
{
  "name": "my_plugin__my_tool",
  "description": "工具描述",
  "input_schema": { "type": "object", "properties": { ... } }
}
```

### Generic（内部格式）

```json
{
  "name": "my_plugin__my_tool",
  "description": "工具描述",
  "parameters": { ... },
  "_metadata": {
    "plugin_id": "my_plugin",
    "tool_name": "my_tool",
    "contract_version": "2026-05-rc1"
  }
}
```

### Provider 工具名映射

服务响应中的 `_metadata` 字段包含 `provider_tool_name → plugin_id:tool_name` 映射，外部调用方必须使用此映射回溯到内部标识。

---

## 审计系统

### 哈希链

所有操作记录为 JSONL 格式，每条记录包含：

- `previous_hash`：前一条记录的哈希
- `current_hash`：当前记录的 SHA-256 哈希
- 形成不可篡改的链式结构

### 审计内容

- 插件安装/卸载/启用/禁用/隔离/吊销
- 权限授予/拒绝
- 工具调用（成功/失败/确认/预算超限/限流）
- 审计记录**不包含**原始参数、返回值、密钥

### 检查点锚定

支持 Ed25519 签名的检查点，可用于外部不可变审计锚定验证。

```bash
# 验证审计日志完整性
python -m modules.plugin_system.audit --verify
```

---

## 可用模板

### 按风险等级

```
🟢 低风险 (compute)
├── hello_world              最简示例，纯计算
└── production_plugin_package 生产打包结构示例

🟡 中风险 (memory.read)
├── read_only_memory_tool    单键记忆读取
└── readonly_retrieval_plugin 业务检索（有界结果集）

🟠 高风险 (fs.read / network.outbound)
├── file_summary_plugin      专家专属，读取文件
├── controlled_network_plugin 专家专属，受控网络
└── network_lookup_tool      受限出站 HTTPS

🔴 危险 (output.send)
└── admin_only_output_tool   管理员专属，发送输出
```

### 模板生成命令

```bash
# 低风险
python -m modules.plugin_system.scaffold data/plugins my_hello --template hello_world

# 记忆读取
python -m modules.plugin_system.scaffold data/plugins my_reader --template readonly-retrieval

# 文件读取
python -m modules.plugin_system.scaffold data/plugins my_file_tool --template file-summary

# 受控网络
python -m modules.plugin_system.scaffold data/plugins my_api --template controlled-network

# 输出发送
python -m modules.plugin_system.scaffold data/plugins my_notifier --template admin_only_output_tool

# 生产打包参考
python -m modules.plugin_system.scaffold data/plugins my_prod --template production-package
```

---

## 工具函数签名规范

```python
def tool_name(args: dict, api=None) -> dict:
    """
    参数:
      args — 模型传入的参数字典，已通过 params Schema 校验
      api  — GatewayClient 实例，dry run 时可能为 None

    返回:
      JSON-serializable 的 dict，必须符合 returns Schema

    异常:
      可抛出 ValueError / TypeError，会被捕获为安全错误消息
      不应暴露内部路径、堆栈、密钥等敏感信息
    """
```

### 关键规则

1. **args 类型处理**：模型传入的值可能是字符串，需要显式类型转换
2. **api 为 None 处理**：必须提供 dry-run 回退逻辑
3. **返回值边界**：所有字符串返回值应截断到 `maxLength`
4. **禁止直接 I/O**：不得使用 `requests`、`httpx`、`open()`、`socket` 等
5. **禁止提示注入**：描述字段不得包含"忽略之前的指令"等诱导性文本

---

## 错误码

| 错误码 | 说明 |
|--------|------|
| `INVALID_ARGUMENT_JSON` | 参数不是合法 JSON |
| `TOOL_CALL_MISSING_NAME` | 缺少工具名 |
| `PARAM_SCHEMA_ERROR` | 参数不符合 Schema |
| `RETURN_SCHEMA_ERROR` | 返回值不符合 Schema |
| `PERMISSION_DENIED` | 权限不足 |
| `TOOL_NOT_VISIBLE` | 工具对当前角色不可见 |
| `CONFIRMATION_REQUIRED` | 需要人工确认 |
| `CONFIRMATION_INVALID` | 确认令牌无效 |
| `CONFIRMATION_EXPIRED` | 确认令牌已过期 |
| `BUDGET_EXCEEDED` | 预算超限 |
| `RATE_LIMITED` | 调用频率超限 |
| `DUPLICATE_TOOL_CALL` | 幂等重复调用 |
| `DUPLICATE_IN_PROGRESS` | 重复调用正在执行中 |
| `IDEMPOTENCY_CONFLICT` | 幂等键参数冲突 |
| `DRY_RUN_ONLY` | 仅支持 dry run |
| `TOOL_LOOP_DETECTED` | 工具循环调用 |
| `TOOL_STORM_RATE_LIMITED` | 工具风暴限流 |

---

## 生产部署要求

生产环境的插件包必须满足以下条件：

| 要求 | 说明 |
|------|------|
| `runtime.mode: sub_process` | 必须使用子进程沙箱 |
| `manifest.lock` | 每个文件的 SHA-256 哈希锁文件 |
| `sbom.cdx.json` | CycloneDX 格式的软件物料清单 |
| Ed25519 签名 | 包签名（HMAC 仅限开发环境） |
| 扫描报告 | pip-audit / OSV / Grype 等扫描器输出 |
| 外部治理存储 | 多实例安全的事务型存储 |
| 外部审批提供者 | 人工确认的外部服务 |
| 外部审计锚定 | 不可变的审计检查点 |

> ⚠️ 模板中的 `.example` 文件（`SIGNATURE.example`、`scanner_report.example.json` 等）仅为结构参考，**不是**有效的生产证据。

---

## 集成契约

### 正确的调用链路

```
模型请求 → Provider 解析 → PluginToolService → 治理检查 → PluginEngine
  → PluginGateway → 沙箱执行 → 结果脱敏 → Provider 格式化 → 模型响应
```

### 禁止的模式

- ❌ 从模型循环直接调用插件函数
- ❌ 绕过 PluginToolService 直接调用 PluginEngine
- ❌ 将插件结果转为 `system` / `developer` 消息
- ❌ 向 `model` 角色暴露隐藏工具（`include_hidden=True`）
- ❌ 将原始目录视为已安装插件
- ❌ 记录原始参数、返回值、密钥

### 插件结果是不可信数据

插件返回的内容必须作为 `tool` 角色消息放回模型上下文，**绝不能**被合并到系统指令中。

---

## 诊断命令速查

```bash
# 脚手架生成
python -m modules.plugin_system.scaffold <target_dir> <name> [--template <tpl>]

# 清单 Lint
python -m modules.plugin_system.manifest_lint <plugin_dir> [--production] [--json]

# 平台状态
python -m modules.plugin_system.status [--json]

# 生产策略检查
python -m modules.plugin_system.production_policy_check [--json]

# 工具自测
python -m modules.plugin_system.tool_selftest

# 模块自测
python -m modules.plugin_system.selftest

# 治理自测
python -m modules.plugin_system.tool_governance --selftest --json

# 集成契约检查
python -m modules.plugin_system.integration_contract [--json]

# 推荐测试计划
python -m modules.plugin_system.test_plan --json

# 综合诊断
python -m modules.plugin_system.doctor [--json]
```

---

## 相关文档

| 文档 | 路径 | 内容 |
|------|------|------|
| 插件作者指南 | `modules/plugin_system/PLUGIN_AUTHOR_GUIDE.md` | 完整开发流程 |
| 清单格式参考 | `modules/plugin_system/PLUGIN_MANIFEST.md` | plugin.yaml 详细规范 |
| 工具服务契约 | `modules/plugin_system/TOOL_SERVICE_CONTRACT.md` | PluginToolService 接口规范 |
| 集成契约 | `modules/plugin_system/INTEGRATION_CONTRACT.md` | 外部系统集成规范 |
| 生产证据要求 | `modules/plugin_system/PRODUCTION_EVIDENCE.md` | 生产包证据清单 |
| 外部治理说明 | `modules/plugin_system/EXTERNAL_GOVERNANCE.md` | 治理存储与审批提供者 |
| Windows 沙箱限制 | `modules/plugin_system/WINDOWS_SANDBOX_LIMITS.md` | Windows 环境限制 |
