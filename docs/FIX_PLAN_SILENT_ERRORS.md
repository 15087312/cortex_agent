# 静默报错 & 不当降级 修复计划

> 创建日期: 2026-06-27
> 状态: 📋 部分修复进行中

---

## 🔴 核心原则（用户确认）

**丢失记忆和系统提示词应当直接报错，不降级。**

原因：
- system prompt 构建失败 = PromptComposer / 配置加载出问题，是基础设施故障
- 静默降级让模型以错误指令运行，比直接报错更危险
- 调用方（think_once / health_check 等）**已有异常处理**，raise 会被正确捕获

---

## 修复分类

| 类别 | 行为 | 示例 |
|------|------|------|
| ✅ **有意义降级 — 接受** | 降级链本身就是设计 | screen_capture 三级降级、后端探测链、finally 清理 |
| 🟡 **接受但加日志** | 降级合理但缺日志，出问题不可排查 | tools/__init__.py 加载失败、composer.py skill 加载 |
| 🔴 **不改 raise 但加日志** | 降级可接受但必须至少 visible | api_stream.py set_mode() 失败、非核心感知丢失 |
| 🔴 **直接 raise** | 降级导致数据/语义错误 | system prompt 丢失、对话历史丢失、embedding 维度错 |

---

## ✅ 甲类：有意义的降级（不动）

| 文件 | 逻辑 | 理由 |
|------|------|------|
| `screen_capture.py` — mss → PIL → screencapture | 跨平台方案链 | 降级是设计本身 |
| `image_analyzer._detect_available_model()` | 各后端探测 | 探测行为，不是错误 |
| `combined_provider.py:48-59` — finally 清理 | 不应覆盖原始异常 | ✅ 已算合理 |
| `transport.py:134-140` — session close | finally 清理 | ✅ |
| 各处 ImportError（有明确 fallback） | 可选依赖 | ✅ 只要 fallback 明确 |

---

## 🟡 乙类：加日志（~30 处）

| 文件 | 行 | 当前 | 改为 |
|------|----|------|------|
| `tools/__init__.py:24` | `except: pass` | 无日志 | `logger.warning(f"分类记忆工具加载失败: {e}")` |
| `tools/__init__.py:31` | `except: pass` | 无日志 | `logger.warning(f"AI工具恢复失败: {e}")` |
| `composer.py:207` | `except: pass` | 返回 "" | `logger.warning(f"Skill加载失败: {e}")` |
| `api_stream.py:960` | `except: pass` | set_mode 不生效 | `logger.warning(f"set_mode失败: {e}")` |
| `orchestrator.py:248` | `except: pass` | 同上 | 同上 |
| `continuous_thinker.py:619,628,641,659` | 感知/记忆/价值观 | `logger.debug` | 已合理，但可提升到 info |

---

## 🔴 丙类：直接 raise（必须修）

### 🔴 P1: 核心模型客户端 — system prompt / 对话历史丢失 → raise

| 文件 | 行 | 当前行为 | 改为 |
|------|----|---------|------|
| `large_model_client.py:76` | `except Exception: system_prompt = "中文硬编码"` | 模型以错误指令运行 | **raise** |
| `medium_model_client.py:166` | `except Exception: messages = [user_prompt]` | 全部历史丢失 | **raise** |
| `small_model_client.py:163` | `except Exception: messages = [user_prompt]` | 全部历史丢失 | **raise** |

**理由：**
- PromptComposer 失败 = 配置/基础设施故障，不是运行时波动
- 调用方已有异常处理（think_once 用 try/except 返回 error dict）
- health_check 失败返回 False，不会崩溃
- 静默降级导致模型在错误配置下运行，比 visible error 更坏

### 🔴 P2: embedding 维度静默降级 → raise

| 文件 | 行 | 当前行为 | 改为 |
|------|----|---------|------|
| `event_store.py:259` | `self._embedding_dim = 384` | FAISS 结果可能完全错误 | **从模型读取；失败则 raise** |

### 🔴 P3: 资源泄漏风险 → 加 `logger.debug`

| 文件 | 行 | 改为 |
|------|----|------|
| `combined_provider.py:50-59` | `logger.debug(f"清理失败: {e}")` |
| `transport.py:134,239` | `logger.debug(f"session关闭失败: {e}")` |
| `cortex/main.py:167` | `logger.warning(f"端口清理失败: {e}")` |

---

## 修复执行状态

- [x] P1 #1: `large_model_client.py:76` — system prompt 构建失败 → raise
- [x] P1 #2: `medium_model_client.py:166` — 消息构建失败 → raise
- [x] P1 #3: `small_model_client.py:163` — 消息构建失败 → raise
- [ ] P2 #1: `event_store.py:259` — embedding 维度 → raise
- [ ] P3 #1: `combined_provider.py:50-59` — 加日志
- [ ] P3 #2: `transport.py:134,239` — 加日志
- [ ] P3 #3: `cortex/main.py:167` — 加日志
- [ ] 乙类: ~30 处加日志
