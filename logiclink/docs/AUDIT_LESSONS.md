# Logiclink 语义审计经验 — 误写模式、修复纪律与证据引擎边界

> 状态：经验沉淀（来自 2026-09 三轮全仓审计，修复 ~75 处误写）
> 适用范围：为 ai_backend 及任何 logiclink 仓库写 `.link` / `# @link:block` 的模型与人工审阅者
> 姊妹文档：本文讲"标注写错"；checker 自己失效的模式（空真守卫、渲染/判定分叉、
> 配置不接线）与修复计划见 [`CHECKER_AUDIT.md`](./CHECKER_AUDIT.md)

---

## 一、为什么需要本文档

`logiclink` 绿 ≠ 语义正确。它只验证"声明的抽象拼图与标注/证据一致"——如果标注
本身说错了（把 `Write` 写成 `Emit`、把 `Think` 写成 `Retrieve`、声明了代码里不存在的
行为），checker 照样绿。全仓审计挖出的 ~75 处误写里，绝大多数 checker 都是绿的。

本文档记录：误写长什么样（模式）、为什么发生、怎么修、证据引擎的硬边界在哪。

---

## 二、误写模式（按出现频率排序）

### M1. 幻影原子 — 声明了代码里不存在的行为 【最高频】

**症状**：eq / block 项里有代码中根本没有的原子。
**例子**：
- `PerceiveLoop = Loop(Think)` — 但 perception/ 没有任何 LLM 推理，全是规则+推送
- `App_Emit = Emit · Retrieve` — 实际先构造 envelope（Retrieve）再发送（Emit），顺序反了
- `TimeRange = Loop(Emit)` — 实际是 `result.append()`（Write），零个 Emit 调用
- `Async_Gather = Emit` — 纯 `Semaphore + gather`，无任何发送

**根因**：写标注的人"想当然"（感知=想、返回=输出），没读函数体。
**修复**：读函数体，按真实调用/分支/返回对原子。

### M2. 错原子 — 原子选错了类别 【高频】

**症状**：原子存在但类别不对。
**例子**：
- embedding 的 `model(**inputs)` 神经推理标成 `Retrieve`（应是 Think）
- `_rank_and_filter` 的 `store.touch_event()`（Write 副作用）藏在 Retrieve 里
- PyAutoGUI 的 `click/typewrite` 标成 `Emit`（应是 Tool——外部硬件动作）
- `Logger_Setup` 标成 `Emit`（应是 Write——makedirs/addHandler）

**判定表**（关键！）：

| 代码行为 | 原子 | 反例 |
|---------|------|------|
| LLM / 神经网络推理 | Think | embedding 的 model() |
| 从存储/网络读 | Retrieve | post/from_pretrained/query |
| 落盘/持久化/副作用写 | Write | touch_event/makedirs |
| 消息/事件/推送/返回 | Emit | 纯 return 构造 |
| 分支/校验/幂等检查 | Guard | if 条件 |
| 外部硬件/子进程动作 | Tool | subprocess/click/typewrite |
| 循环 | Loop() | while/for |

### M3. 顺序倒置 — 声明顺序与执行顺序相反 【中频】

**例子**：
- `PermVisible = Guard · Retrieve` — 实际先读白名单再过滤（Retrieve·Guard）
- `SMALL_Chat_Emit = Emit · Throttle` — 实际是 重试循环内 sleep，最后才返回

**修复**：按代码从上到下的执行顺序排列原子。

### M4. 跨函数组合被压扁 【中频】

**症状**：父 block 覆盖整个函数，但函数内 delegate 给其它方法，真实行为不在本函数。
**例子**：
- `PetChat = Think · Write` 挂在 `chat()` 上 — 实际 `chat()` 只是 `submit(queue, loop.run)`，
  Think/Write 都在被委托的 `loop.run`/`save_pair` 里
- `ChatOnly_Turn = Think` — 实际函数是 `while True` 接收+落库+推送，Think 在 thinker 里

**修复**：跨函数组合的**外层 block 只标注本函数直接执行的行为**（委托 = 不标注），
或拆到被委托函数上；v1 不跨函数验证。

### M5. 分支/循环被线性化 【低频但危险】

**例子**：
- `Distrib = Guard · Write · Emit` — 实际 console/file/api/voice 是互斥 `if/elif`，
  Write 只在 file 分支。应为 `Guard · (Write | Emit)`
- `MockWs = Loop(Guard · Emit)` — 循环内还有 `receive_text/json.loads`（Retrieve）和
  `log()`（Write），应为 `Loop(Retrieve · Guard · Write · Emit)`

**修复**：互斥分支用 `|`，循环内每个行为都进 Loop。

### M6. 文件级/类级错位 【低频】

**例子**：
- `VersionCheck_Guard = Guard · Think` 挂在 `class VersionChecker:` — 类定义行既无
  分支也无推理，实际在方法 `:34,:56` 里
- `CausalTree_Retrieve = Retrieve` 挂在文件顶部 — 文件 620 行含 Loop/Guard/Write，
  单个 Retrieve 掩盖一切

**修复**：标注挂在**真实行为所在的最小作用域**（方法/语句），不挂在类/文件顶部。

### M7. 状态机/数据流过度建模 【低频】

**例子**：
- `state task queued→running→done/cancelled` — 实际 task_queue 是 `asyncio.Queue` +
  `future.cancel()`，没有状态字段，是幻影状态机
- `PetChatFlow/PetMsgFlow` 的 `Emit(msgs)·Write(msgs)` — build_messages 是纯构造，
  无 Emit/Write；L2 要求"先生產後消費"，逼标注撒谎

**修复**：没有真实状态字段/转换就不写 state；简单委托/构造不加 L2 flow。

---

## 三、证据引擎的硬边界（不可证 vs 语义）

修复过程中反复遇到：**语义上对，但证据引擎证明不了**。三类：

### B1. 属性赋值不是调用
```python
self._connected = False   # 语义是 Write，但 ast.Call 看不到
self.modules[name] = ...  # 同上
```
证据引擎只识别 `ast.Call`（`foo()`）。属性/下标赋值永远不产生 evidence。

### B2. 方法名不在调用名黑名单
```python
await session.__aexit__(...)   # Retrieve 语义，但 "__aexit__" 不在 link.toml
```
link.toml 的 `[atoms.X].calls` 是"白名单"——没列的调用名不产生证据。

### B3. 函数整体委托
```python
def chat(self, text):          # 全函数只是 submit(queue, loop.run)
    fut = await self.task_queue.submit(...)   # Think 在 loop.run 里，不在这里
```
调用名匹配 + 区间匹配都无法跨函数。

### 处理纪律

1. **能补白名单就补**：`__aexit__`/`run_coroutine_threadsafe`/`get_url`/`exists` 等
   是真实调用，加进 `[atoms.Retrieve].calls` 即可证。
2. **不可证就诚实降级**：`StdioClose` 的状态重置是属性赋值 → 回 `Guard · Retrieve`
   （可证部分）+ 注释说明"状态重置为属性赋值，非持久 Write 不进项"。
3. **绝不硬塞**：把不可证行为塞进 eq 会破 E002，且让 eq 与代码脱节——两头都错。

### 判决表（"写"类行为的最终裁定）

| 行为 | 判定 | 原因 |
|------|------|------|
| `self._connected = False` | 不进项 | 属性赋值，不可证 |
| `self.modules[name] = ...` | 不进项 | 下标赋值，不可证；且是内存登记非持久 |
| `store.touch_event(id)` | Write（进项） | 方法调用，可证 |
| `makedirs`/`addHandler` | Write（进项） | 方法调用，可证 |
| `click`/`typewrite` | Tool（进项） | 方法调用，可证 |

---

## 四、修复工作流（三遍法）

1. **先跑 checker 找机械错误**：E001 解析 / E002 缺证据 / E003 等式不一致 /
   E004 组合不匹配 / E005 重名 —— 这些是"拼图对不上"，先修。
2. **再读代码做语义审计**：对每个 eq/block，读对应函数体，核对
   （a）原子顺序 vs 执行顺序 （b）原子类别 vs 行为判定表 （c）有无幻影/遗漏。
3. **最后回跑 checker 确认绿**：语义修复常引入 E002/E003/E004（证据或 eq 没联动），
   按"能补白名单→补；不能→诚实降级+注释"收敛。

## 五、给审阅者的检查清单

- [ ] 每个原子在函数体里能找到真实证据（调用名/分支/返回）
- [ ] 原子顺序与代码执行顺序一致
- [ ] 没有 `Think` 标给非推理（perception 是重灾区）
- [ ] 没有 `Emit` 标给纯 return / 纯构造
- [ ] 没有 `Retrieve` 标给神经网络推理（embedding 是重灾区）
- [ ] 互斥分支用了 `|` 而不是线性 `·`
- [ ] `Loop()` 只包真实 while/for
- [ ] block 挂在真实行为的作用域，不挂在类/文件顶部
- [ ] 跨函数委托不冒充直接行为
- [ ] 无状态字段的状态机 / 纯构造的 L2 flow —— 删掉或留注释
- [ ] 属性赋值类"写"未硬塞进 eq（诚实降级+注释）

---

## 六、本轮审计成果（2026-09）

| 批次 | 修复数 | 典型 |
|------|--------|------|
| HIGH | 12 | PerceiveLoop 幻影 Think / App_Emit 倒序 / TimeRange 错原子 / GateChain 压扁 |
| MEDIUM ×5 | ~35 | thinking 全系 / memory 数据流 / output 分支 / infra 白名单补全 |
| R1-R4 | ~25 | 状态机幻影 / L2 幻影 flow / 文件级错位 / Db/DbInit/FileVer 修正 |
| **累计** | **~75** | 全仓语义错误基本清零 |

**最终验证**：`logiclink . --allow-arch` 仅剩 5 个已文档化 E302（基础设施耦合），
`--strict` 0 警告。