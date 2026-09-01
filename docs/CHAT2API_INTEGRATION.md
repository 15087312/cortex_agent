# Chat2API 多模型自动切换接入指南（零代码方案）

**Language / 语言**: [简体中文](./CHAT2API_INTEGRATION.md)

> 本文档描述如何**不改一行代码**，让 Cortex Agent 通过同一目录下的
> [Chat2API 管理器](../../Chat2API) 的「模型映射 + 多账号负载均衡」，用**固定逻辑模型名**
> 自动切换底层真实模型，Cortex 侧只配置一次。

---

## 1. 方案原理

Cortex 的模型层（`LARGE_MODEL_NAME` / `MEDIUM_MODEL_NAME` / `SMALL_MODEL_NAME`）填写
**固定逻辑模型名**（建议 `cortex-large` / `cortex-medium` / `cortex-small`），
API 地址统一指向 Chat2API 的 OpenAI 兼容端点 `http://127.0.0.1:8080/v1`。

Chat2API 收到请求后按以下两级机制决定真正的上游模型与账号：

```
Cortex 请求 c  "model": "cortex-large"
        │
        ▼
Chat2API  /v1/chat/completions
        │
        ├─ ① 模型映射 modelMappings
        │    将 "cortex-large" → { actualModel: "deepseek-v4-flash",
        │                          preferredProviderId: "deepseek" }
        │
        ├─ ② 负载均衡 loadBalancer
        │    在支持该 actualModel 的 provider / 账号之间按
        │    round-robin / fill-first / failover 策略选一个
        │
        ▼
某个实际模型 API（DeepSeek / Kimi / Qwen / GLM / Perplexity ...）
```

**关键效果**：切换模型 = 在 Chat2API 里改一条模型映射（界面下拉或一条 API 请求），
Cortex 端永远不用改任何配置。

---

## 2. Chat2API 侧准备（一次性）

### 2.1 启动代理

Chat2API（Electron 应用）默认代理端口 `8080`、监听 `127.0.0.1`（`proxyPort`）。
启动后右上角确认代理状态为 **Running**。

> 注意：若本机 8080 已被其他进程占用（例如 Cortex 开发者模式占用），
> 请在 Chat2API「设置 → 代理端口」改成空闲端口，并把后文 Cortex 侧 URL 同步修改。

### 2.2 添加多家供应商账号

在 Chat2API 的「Providers 供应商」页为要自动切换的每家厂商添加账号并启用：

| 厂商 | 内置供应商 ID | 说明 |
|------|--------------|------|
| DeepSeek | `deepseek` | 支持 deepseek-v4-flash / deepseek-v4-pro 等 |
| Kimi | `kimi` | |
| 通义 Qwen | `qwen` | |
| 智谱 GLM | `glm` | |
| MiniMax | `minimax` | |
| Z.ai | `zai` | |
| Perplexity | `perplexity` | |

每添加一个账号都会生成独立的 `Account`，可单独设置每日限额。

### 2.3 确认/调整负载均衡策略

「Settings → 负载均衡策略」：

| 策略 | 行为 | 适用 |
|------|------|------|
| `round-robin`（默认） | 在候选账号间轮询 | 多账号分摊用量 |
| `fill-first` | 先填满用量最小的账号 | 配额用尽自动切下一个 |
| `failover` | 优先健康账号，失败超阈值自动降级 | 高可用 |

---

## 3. Cortex 侧配置（一次性）

在 Cortex 设置页「对话 → 主模型配置」填入：

| 字段 | 值 | 说明 |
|------|-----|------|
| 大模型 API URL | `http://127.0.0.1:8080/v1` | Chat2API OpenAI 兼容端点 |
| 大模型 API Key | 留空 | Chat2API 未开启 `enableApiKey` 时无需 |
| 大模型模型名 | `cortex-large` | **逻辑名，固定不换** |
| 中模型 API URL | `http://127.0.0.1:8080/v1` | |
| 中模型模型名 | `cortex-medium` | |
| 小模型 API URL | `http://127.0.0.1:8080/v1` | |
| 小模型模型名 | `cortex-small` | |

等价于直接修改 `~/.cortex/settings.json`：

```json
{
  "LARGE_MODEL_API_URL": "http://127.0.0.1:8080/v1",
  "LARGE_MODEL_API_KEY": "",
  "LARGE_MODEL_NAME": "cortex-large",
  "MEDIUM_MODEL_API_URL": "http://127.0.0.1:8080/v1",
  "MEDIUM_MODEL_NAME": "cortex-medium",
  "SMALL_MODEL_API_URL": "http://127.0.0.1:8080/v1",
  "SMALL_MODEL_NAME": "cortex-small"
}
```

保存后智能体模式立即生效，纯对话模式新会话生效，无需重启。

---

## 4. 建立模型映射（切换的入口）

Chat2API 的「Models 模型映射」页（或管理 API）中，为每个逻辑名建立一条映射：

| 请求模型（requestModel） | 实际模型（actualModel） | 首选供应商 | 首选账号 |
|--------------------------|------------------------|-----------|----------|
| `cortex-large` | `deepseek-v4-flash` | `deepseek` | 可选 |
| `cortex-medium` | 某中等级实际模型 | 可选 | |
| `cortex-small` | 某轻量实际模型 | 可选 | |

创建方式二选一：

**方式 A — Chat2API 界面**：Models → 添加映射 → 填上表 → 保存。

**方式 B — 管理 API**（需先在设置开启 Management API 并记下 secret）：

```bash
curl -X POST http://127.0.0.1:8080/v0/management/model-mappings \
  -H "X-Management-Secret: <你的 secret>" \
  -H "Content-Type: application/json" \
  -d '{
    "requestModel": "cortex-large",
    "actualModel": "deepseek-v4-flash",
    "preferredProviderId": "deepseek"
  }'
```

---

## 5. 切换模型（日常操作，Cortex 不再动）

**场景一：更换真实模型**（例如从 deepseek 切到 Kimi）——Chat2API 里把
`cortex-large` 的 actualModel 改成目标模型（如 `kimi-k2-0711-preview`），
并把 preferredProviderId 改成 `kimi`（可选）。Cortex 无需任何改动。

**场景二：多账号自动轮换/故障回退**——若多个 provider 都支持同一 actualModel，
且映射不写死 preferredProviderId，Chat2API 会按负载均衡策略在账号间自动分配；
某账号连续失败 3 次自动冷却 1 分钟并切入其他账号。

**场景三：一键切换整套方案**——把三层的映射组织成「方案」，切换时批量更新三条映射，
或用界面直改三个下拉，十几秒完成，Cortex 全程无感。

管理 API 更新示例：

```bash
curl -X PUT http://127.0.0.1:8080/v0/management/model-mappings/cortex-large \
  -H "X-Management-Secret: <你的 secret>" \
  -H "Content-Type: application/json" \
  -d '{"actualModel": "kimi-k2-0711-preview", "preferredProviderId": "kimi"}'
```

---

## 6. 通配符映射（可选高级用法）

Chat2API 支持通配符匹配请求模型名：

| 模式 | 匹配规则 | 示例用法 |
|------|----------|----------|
| `*` | 匹配任意模型 | 把所有模型路由到某家 |
| `cortex-*` | 前缀匹配 | 覆盖全部 cortex 逻辑名 |
| `*large` | 后缀匹配 | 只匹配大模型逻辑名 |

示例：一条 `cortex-* → deepseek-v4-flash（preferredProvider: deepseek）`
即可覆盖三层逻辑名，无需逐条配置。

---

## 7. 验证

1. **确认代理可达**：
   ```bash
   curl http://127.0.0.1:8080/health   # 期望 status: healthy
   ```
   > 若返回 `UNAUTHORIZED`，说明 `/v1` 开启了 API Key 认证，
   > 需先到 Cortex 设置填入 Chat2API 的 API Key。

2. **查看暴露模型**（应包含 `cortex-large` 等逻辑名 + 各 provider 实际模型）：
   ```bash
   curl http://127.0.0.1:8080/v1/models
   ```

3. **Cortex 一次对话**：在 Cortex 发起对话，观察 `/stream/ws/{sid}` 日志中
   大模型实际打到哪个上游 URL，确认走了 Chat2API 并命中映射。

4. **切换验证**：改一条映射后再次对话，确认无需重启 Cortex 即生效。

---

## 8. 边界与回退

- **三层模型名必须保持逻辑名**；若映射缺失，请求会「原样传名」给遇到的第一家
  能接受该名的 provider（Chat2API 不报错但可能匹配不到预期模型）。
- **映射优先级**：精确模型映射 `> *` 通配映射 `>` 原样透传（`modelMapper.mapModel`）。
- **Provider 支持性**：`loadBalancer` 只把「实际模型在某 provider 有效模型列表内」
  的账号纳入候选池；映射的 actualModel 请填目标账号真实支持的模型 ID。
- **Token/上下文**：三层各自的上下文长度按实际模型的参数配置
  （`LARGE_MODEL_CONTEXT_LENGTH` 等），切换不同模型时注意对齐，避免截断。

---

## 附：本文档与代码的对应关系

| 概念 | Chat2API 位置 |
|------|--------------|
| 模型映射 | `src/main/proxy/modelMapper.ts`、`src/main/store/config.ts`、`routes/management/modelMappings.ts` |
| 负载均衡 | `src/main/proxy/loadbalancer.ts` |
| 账号模型（displayName→actualModelId） | `src/main/store/store.ts::getEffectiveModels`、`store/types.ts::CustomModel` |
| OpenAI 兼容端点 | `src/main/proxy/routes/chat.ts`（`/v1/chat/completions`） |
| 管理 API 认证 | `src/main/proxy/middleware/managementAuth.ts` |