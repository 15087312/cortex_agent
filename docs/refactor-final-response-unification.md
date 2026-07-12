# 重构计划：统一最终回复写入路径

> 日期: 2026-07-11
> 文件: `modules/thinking/core/model_runner.py`
> 目标: 将 6 个 `set_final_response` 调用点合并为 `_write_final_result` 单一出口

---

## 现状

### 6 个 `set_final_response` 调用点

| # | 行号 | 方法 | 触发条件 | 路径类型 |
|---|------|------|----------|----------|
| 1 | 547 | `_save_partial_result` | 用户取消 / 异常中断 | 取消 |
| 2 | 628 | `_write_final_result` | `continuous_think` 正常结束 | 正常(唯一出口) |
| 3 | 1419 | `_generate` | `no_tools` 纯聊天模式 | 功能分支 |
| 4 | 1540 | `_generate` 工具循环 | large 模型直接输出文本(无工具调用) | 正常(短期支路) |
| 5 | 1574 | `_generate` 工具循环 | 模型多次拒绝调用工具，强制结束 | fallback |
| 6 | 1651 | `_generate` 工具循环 | 模型调用 `respond_to_user` 工具 | 正常(控制工具) |

### 冗余分析

路径 #4/#5/#6 在 `_generate` 内调用 `set_final_response(content)` 后 `return content`。但返回后：
1. `content` → `think_once` → `continuous_think` → 检测到 `continue=false` → break → 回到 `_think_loop`
2. `_think_loop` 调用 `_write_final_result()` → 从 snapshot 读 `result_summary` → **再次** `set_final_response`

**结论: `set_final_response` 在路径 #4/#5/#6 中是完全冗余的。** `record_control_decision` 已经足够——`_write_final_result` 会从那里读取结果。

---

## 重构方案

### 核心思路

路径 #4/#5/#6 只做 `record_control_decision`（已在做），**去掉 `set_final_response`**。
让 `_write_final_result` 成为写入 `final_response` 的唯一地方。

```
改动前:
  _generate() → set_final_response + record_control_decision + return content
  _think_loop → _write_final_result() → set_final_response (又写一次，覆盖)

改动后:
  _generate() → record_control_decision(continue:false, result_summary:content) + return
  _think_loop → _write_final_result() → set_final_response (唯一写入点)
```

### 具体改动

#### 文件 1: `model_runner.py` — 4 处删减

**Path #4** (L~1538-1544):
```python
# 改动前
if self.blackboard:
    prefix = self._personality_prefix()
    if prefix and not content.startswith(prefix):
        content = prefix + content
    self.blackboard.set_final_response(content)  # ← 删
if self._thinker:
    self._thinker.record_control_decision({"continue": False, "result_summary": content})
return content  # 保留
```
→ 删除 `set_final_response` 及前缀逻辑，只保留 `record_control_decision` + `return`

**Path #5** (L~1570-1578):
```python
# 改动前
response_text = content or f"[{self.identity.role}] 已处理：{self._task_description}"
if self.blackboard:
    prefix = self._personality_prefix()
    if prefix and not response_text.startswith(prefix):
        response_text = prefix + response_text
    self.blackboard.set_final_response(response_text)  # ← 删
if self._thinker:
    self._thinker.record_control_decision({"continue": False, "result_summary": response_text})
return response_text  # 保留
```
→ 同路径 #4

**Path #6: `respond_to_user`** (L~1648-1654):
```python
# 改动前
if self.blackboard:
    prefix = self._personality_prefix()
    if prefix and not content.startswith(prefix):
        content = prefix + content
    self.blackboard.set_final_response(content)  # ← 删
if self._thinker:
    self._thinker.record_control_decision({"continue": False, "result_summary": content})
```
→ 只删 `set_final_response`，其余不变

**Path #3: `no_tools`** (L~1417-1421) — **保持现状**
- 这个路径不走 `_write_final_result`，`no_tools` 是一个完全独立的模式
- 如果以后统一，需要重构 `_generate` 的调用结构，影响较大

**Path #1: `_save_partial_result`** (L~547) — **保持现状**
- 取消/异常路径，不应经过正常输出逻辑

#### `_write_final_result` 的兼容性确认

`_write_final_result` 读取 `control_decision.result_summary`（L~623-624）：
```python
has_final_result = bool(
    control_decision and getattr(control_decision, "result_summary", None)
)
```

路径 #4/#5/#6 都设置了 `result_summary`，所以 `_write_final_result` 能正确读取。

⚠️ 路径 #6 当前将内容设为 `content + prefix`，而 `_write_final_result` 的逻辑是：
```python
response = control_decision.result_summary[:8000]
if prefix and not response.startswith(prefix):
    response = prefix + response
self.blackboard.set_final_response(response)
```

由于路径 #6 的 `result_summary` 是**原始 content（无前缀）**，`_write_final_result` 会正确叠加前缀。✅

---

## 影响评估

### 收益

| 维度 | 收益 |
|------|------|
| 减少重复代码 | `set_final_response` 从 6 处减为 3 处（Path #1/取消、Path #2/正常、Path #3/no_tools） |
| 人格前缀统一 | 前缀逻辑只需在 `_write_final_result` 写一次 |
| 后续扩展 | 加输出格式/过滤/审核只需改一个点 |
| 消除冗余写入 | 不再有"先写一次，又被覆盖一次"的情况 |

### 风险

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| `_write_final_result` 读不到 `result_summary` | 低 | 写入空值，用户看不到回复 | `_write_final_result` 有 fallback（从 `history_thoughts` 恢复） |
| 路径 #4/#5 返回的内容不进 snapshot | 低 | `_write_final_result` 读不到内容 | `record_control_decision` 已设置 `result_summary`，`get_process_snapshot` 会包含 |
| expert/supervisor 的 return 路径被误伤 | 低 | 专家/主管输出异常 | 这些路径不调用 `set_final_response`（只 `record_control_decision`），不改它们 |
| `respond_to_user` 先处理其他 control_calls 再 return 的延时 | 低 | `_write_final_result` 可能读旧数据 | `record_control_decision` 覆盖旧值，take the last |

### 测试策略

1. 测试 large 模型直接文本回复（路径 #4）：发送简单问题，确认完整回复
2. 测试 `respond_to_user`（路径 #6）：通过对话触发模型调用该工具
3. 测试 `no_tools` 模式（路径 #3）：保持不变
4. 测试取消/中断（路径 #1）：保持不变
5. 测试人格前缀：切换到非默认人格，确认前缀 `[人格名] ` 出现且不重复
6. 测试 supervisor/expert 委托流程：确保不影响原有流程

---

## 执行计划

```
Step 1: 删除路径 #4/#5/#6 中的 set_final_response 行     [~5 行, 低风险]
Step 2: 删除路径 #4/#5/#6 中的人格前缀逻辑                [~3 行, 已在 _write_final_result 中]
Step 3: 验证 _write_final_result 的读取路径                [检查, 无需改代码]
Step 4: 编译验证                                          [py_compile]
Step 5: 功能测试                                          [6 个测试场景]
---
总改动量: 删除约 8 行，无新增行
预计时间: 15-20 分钟
```
