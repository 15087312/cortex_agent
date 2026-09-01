# Chat2API Multi-Model Auto-Switching Integration Guide (Zero-Code)

**Language / 语言**: [简体中文](./CHAT2API_INTEGRATION.md) | [English](./CHAT2API_INTEGRATION.en.md)

> This guide explains how to let Cortex Agent auto-switch between multiple real models
> through the [Chat2API Manager](../../Chat2API) using **fixed logical model names** —
> with **zero code changes**. Cortex config is set once; switching happens entirely in Chat2API.

---

## 1. How It Works

Cortex model tiers (`LARGE_MODEL_NAME` / `MEDIUM_MODEL_NAME` / `SMALL_MODEL_NAME`) use
**fixed logical names** (recommended: `cortex-large` / `cortex-medium` / `cortex-small`),
all pointing at Chat2API's OpenAI-compatible endpoint `http://127.0.0.1:8080/v1`.

Chat2API then decides the real upstream model & account via two mechanisms:

```
Cortex sends "model": "cortex-large"
        │
        ▼
Chat2API  /v1/chat/completions
        │
        ├─ ① Model mappings (modelMappings)
        │    "cortex-large" → { actualModel: "deepseek-v4-flash",
        │                       preferredProviderId: "deepseek" }
        │
        ├─ ② Load balancer (loadBalancer)
        │    picks one account among providers supporting that actualModel
        │    using round-robin / fill-first / failover
        │
        ▼
Real model API (DeepSeek / Kimi / Qwen / GLM / Perplexity ...)
```

**Key benefit**: to switch models you edit *one mapping* in Chat2API (UI dropdown or one API call).
Cortex never changes.

---

## 2. Chat2API Setup (one-time)

### 2.1 Start the proxy

Chat2API (Electron) listens on `127.0.0.1:8080` by default (`proxyPort`).
Make sure the proxy status shows **Running**.

> If 8080 is already occupied (e.g. Cortex dev server), change the proxy port in
> Chat2API "Settings → Proxy Port" and update the Cortex URLs below accordingly.

### 2.2 Add multiple provider accounts

Add and enable accounts for each vendor you want to auto-switch among:

| Vendor | Builtin provider ID | Notes |
|--------|--------------------|-------|
| DeepSeek | `deepseek` | deepseek-v4-flash / deepseek-v4-pro ... |
| Kimi | `kimi` | |
| Qwen | `qwen` | |
| GLM | `glm` | |
| MiniMax | `minimax` | |
| Z.ai | `zai` | |
| Perplexity | `perplexity` | |

### 2.3 Load balance strategy

"Settings → Load Balance Strategy":

| Strategy | Behavior | Use case |
|----------|----------|----------|
| `round-robin` (default) | rotate across candidate accounts | spread usage |
| `fill-first` | use the least-used account first | quota exhaustion fallback |
| `failover` | prefer healthy accounts; cool down failed ones | high availability |

---

## 3. Cortex Config (one-time)

In Cortex Settings → Chat → Main Model Config:

| Field | Value | Note |
|-------|-------|------|
| Large API URL | `http://127.0.0.1:8080/v1` | Chat2API OpenAI endpoint |
| Large API Key | leave empty | only needed if Chat2API `enableApiKey` is on |
| Large Model Name | `cortex-large` | **logical name, never changed** |
| Medium API URL | `http://127.0.0.1:8080/v1` | |
| Medium Model Name | `cortex-medium` | |
| Small API URL | `http://127.0.0.1:8080/v1` | |
| Small Model Name | `cortex-small` | |

Equivalent to editing `~/.cortex/settings.json`:

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

Takes effect immediately in Agent mode; new sessions in Chat-Only mode. No restart.

---

## 4. Create Model Mappings (the switch point)

In Chat2API "Models" page (or via Management API), map each logical name:

| requestModel | actualModel | preferredProviderId | preferredAccountId |
|--------------|-------------|---------------------|--------------------|
| `cortex-large` | `deepseek-v4-flash` | `deepseek` | optional |
| `cortex-medium` | some medium model | optional | |
| `cortex-small` | some light model | optional | |

Option A — Chat2API UI: Models → add mapping → save.

Option B — Management API (enable "Management API" first and note the secret):

```bash
curl -X POST http://127.0.0.1:8080/v0/management/model-mappings \
  -H "X-Management-Secret: <your secret>" \
  -H "Content-Type: application/json" \
  -d '{
    "requestModel": "cortex-large",
    "actualModel": "deepseek-v4-flash",
    "preferredProviderId": "deepseek"
  }'
```

---

## 5. Switching Models (daily operation; Cortex untouched)

**Scenario 1 — swap real model** (e.g. deepseek → Kimi): edit the mapping's
actualModel to the target (e.g. `kimi-k2-0711-preview`) and set preferredProviderId
to `kimi` if desired. No Cortex changes.

**Scenario 2 — auto rotation / failover**: if multiple providers support the same
actualModel and the mapping leaves preferredProviderId unset, Chat2API load-balances
across accounts; an account failing 3× cools down 1 min and traffic moves elsewhere.

**Scenario 3 — switch an entire preset**: treat the three mappings as one "plan";
batch-update all three (UI or API) in seconds.

Management API update example:

```bash
curl -X PUT http://127.0.0.1:8080/v0/management/model-mappings/cortex-large \
  -H "X-Management-Secret: <your secret>" \
  -H "Content-Type: application/json" \
  -d '{"actualModel": "kimi-k2-0711-preview", "preferredProviderId": "kimi"}'
```

---

## 6. Wildcard Mappings (optional advanced)

Chat2API supports wildcards in request model names:

| Pattern | Matches | Example |
|---------|---------|---------|
| `*` | any model | route everything to one vendor |
| `cortex-*` | prefix | cover all logical names |
| `*large` | suffix | only the large tier |

Example: one mapping `cortex-* → deepseek-v4-flash (preferred: deepseek)` covers all tiers.

---

## 7. Verification

1. **Proxy reachable**:
   ```bash
   curl http://127.0.0.1:8080/health   # expect status: healthy
   ```
   > If `UNAUTHORIZED`, `/v1` API-key auth is on — set the Chat2API API key in Cortex.

2. **List models** (should include `cortex-large` etc. plus real models):
   ```bash
   curl http://127.0.0.1:8080/v1/models
   ```

3. **One Cortex conversation**: check `/stream/ws/{sid}` logs to confirm the large
   model actually hits Chat2API and matches the mapping.

4. **Switch test**: edit one mapping and chat again — should take effect without restart.

---

## 8. Boundaries & Fallback

- **Keep the three logical names**; if a mapping is missing the request is passed through
  with the original name to the first provider that accepts it.
- **Mapping precedence**: exact mapping `> *` wildcard `>` passthrough (`modelMapper.mapModel`).
- **Provider support check**: `loadBalancer` only includes accounts whose effective model list
  contains the actualModel — use real model IDs supported by the target account.
- **Context length**: align `LARGE_MODEL_CONTEXT_LENGTH` etc. with the actual switched model
  to avoid truncation.

---

## Appendix: Code Map

| Concept | Chat2API location |
|---------|-------------------|
| Model mappings | `src/main/proxy/modelMapper.ts`, `src/main/store/config.ts`, `routes/management/modelMappings.ts` |
| Load balancer | `src/main/proxy/loadbalancer.ts` |
| Account models (displayName→actualModelId) | `src/main/store/store.ts::getEffectiveModels`, `store/types.ts::CustomModel` |
| OpenAI endpoint | `src/main/proxy/routes/chat.ts` (`/v1/chat/completions`) |
| Management auth | `src/main/proxy/middleware/managementAuth.ts` |