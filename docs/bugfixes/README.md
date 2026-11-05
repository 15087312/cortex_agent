# 错误原因与修复记录 · 总索引

**Language / 语言**: [简体中文](./README.md) | [English](./README.en.md)

> 本目录集中存放项目的 bug 修复记录，由原 `docs/ERRORS_AND_FIXES.md`（107 节）按模块拆分而成。
> 章节编号与原文一致，历史引用（`ERRORS_AND_FIXES.md §N`）仍然可用——旧路径现在是一个跳转页。
> 待确认的高危行为变更见 [`pending.md`](./pending.md)。

## 目录结构

| 模块 | 节数 |
|------|------|
| [后端 · 思考 / 模型 / 工具 / 委托](./01-backend-thinking.md) | 27 |
| [后端 · 记忆 / 因果 / 心理活动 / 感知](./02-backend-memory.md) | 15 |
| [后端 · 配置持久化与安全](./03-backend-config-security.md) | 14 |
| [前端 · 聊天 / 气泡 / 重放 / 多会话](./04-frontend-chat.md) | 15 |
| [前端 · 页面 / 样式 / 构建 / 按钮](./05-frontend-ui.md) | 6 |
| [实时链路 · WS / SSE / 推送 / 代理 / TUI](./06-realtime-streaming.md) | 8 |
| [桌宠](./07-desktop-pet.md) | 1 |
| [测试 / CI / 环境 / 依赖](./08-testing-ci.md) | 19 |
| [流程 · 审计方法论 / 文档治理](./09-process-docs.md) | 2 |

> 测试基建/内存安全测试框架另见 [`../MEMORY_LEAK_TESTING.md`](../MEMORY_LEAK_TESTING.md)。

## 编号修订

原文有 4 处重复编号（§40/§41/§42/§43 各出现两次，后一次是后续补记）。为便于检索，
后出现的 4 节改为 §104–§107，并在各自标题下标注原编号；其余编号一律不变。
代码注释与其它文档中的 `§1–§103` 引用因此全部保持有效。

| 新编号 | 原标题 | 主题 |
|--------|--------|------|
| §104 | §40（第二次） | 前端连不上后端：IPv6 localhost 陷阱 + 代理层测试盲区 |
| §105 | §41（第二次） | 切换会话后心理活动框消失 —— mental 事件不持久化 |
| §106 | §42（第二次） | 环境感知未注入大模型上下文 |
| §107 | §43（第二次） | 文件感知功能实际无效 —— 采集管道断裂 |

## 新增一条记录

1. 在对应模块文件末尾追加 `## <下一个编号>. <标题>（后端/前端/…）`；节内建议固定写
   **现象 → 根因 → 修复 → 测试**（可选 **回归** / **同类排查**）。
2. 在本文「全量索引」表末尾补一行：编号 / 模块 / 标题 / 文件。
3. 同步英文文件（`<stem>.en.md`）与 `README.en.md`。
4. 若改动触及生产行为契约、需要人工确认，同时在 [`pending.md`](./pending.md) 登记。

## 全量索引

| 编号 | 模块 | 标题 | 文件 |
|------|------|------|------|
| §1 | 思考/模型 | OpenAI tool 消息孤儿引用 → 工具调用死循环（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §2 | 记忆/因果 | 分类记忆工具加载失败 — 悬空引用（后端） | [02-backend-memory](./02-backend-memory.md) |
| §3 | 聊天/气泡 | WebSocket 发送无重试 → 消息静默丢失（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §4 | 聊天/气泡 | 虚拟滚动 → 新消息不渲染（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §5 | 聊天/气泡 | 停止按钮只改 UI 不通知后端（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §6 | 页面/样式 | 原生 confirm/prompt 阻塞 Qt WebEngine（前端） | [05-frontend-ui](./05-frontend-ui.md) |
| §7 | 页面/样式 | 界面使用 emoji 违背 CSS 图标约定 | [05-frontend-ui](./05-frontend-ui.md) |
| §8 | 聊天/气泡 | 新建会话无效（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §9 | 记忆/因果 | 无关历史记忆注入污染上下文（后端） | [02-backend-memory](./02-backend-memory.md) |
| §10 | 思考/模型 | 交互等待超时（ask_user_intent / 审批） | [01-backend-thinking](./01-backend-thinking.md) |
| §11 | 记忆/因果 | 审批结果未回传模型 + 黑板生命周期问题 | [02-backend-memory](./02-backend-memory.md) |
| §12 | 实时链路 | 前端裸 fetch 路径在 8765 静态代理下全部 404（前端） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §13 | 配置/安全 | settings 双重 @property → 多 Agent 思考崩溃（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §14 | 页面/样式 | 会话图谱 return_to 节点 tier 为空 → 节点/边被布局丢弃（前端/后端） | [05-frontend-ui](./05-frontend-ui.md) |
| §15 | 流程/文档 | 补充历史经验（此前未记录） | [09-process-docs](./09-process-docs.md) |
| §16 | 记忆/因果 | 黑板共享记忆：角色别名不匹配（后端） | [02-backend-memory](./02-backend-memory.md) |
| §17 | 聊天/气泡 | 前端重构期已修复 Bug 汇总（旧 JS 版 → Vue 迁移经验） | [04-frontend-chat](./04-frontend-chat.md) |
| §18 | 配置/安全 | 设置全局开关是摆设（后端/前端） | [03-backend-config-security](./03-backend-config-security.md) |
| §19 | 思考/模型 | 自动化替换留下的运行时 NameError + 同名索引重复（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §20 | 测试/CI | 测试假对象与真实模型字段脱节（后端） | [08-testing-ci](./08-testing-ci.md) |
| §21 | 思考/模型 | 主动搭话旁路绕过全局/会话开关（后端）+ 测试污染生产库 | [01-backend-thinking](./01-backend-thinking.md) |
| §22 | 页面/样式 | 全按钮审计方法论 + 启动快捷键 shortcut_keys 是摆设（前端/后端） | [05-frontend-ui](./05-frontend-ui.md) |
| §23 | 页面/样式 | "点详情没反应"的两种根因：DOM 位置 + 构建失败（前端） | [05-frontend-ui](./05-frontend-ui.md) |
| §24 | 实时链路 | send_json_from_thread 事件循环线程内自锁 → 对话报错（后端） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §25 | 思考/模型 | 工具型单次 LLM 调用被统一套上 agent 人设（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §26 | 聊天/气泡 | 聊天附件"发不出去"：三个契约断裂连环 + 测试基建缺失（前端/后端） | [04-frontend-chat](./04-frontend-chat.md) |
| §27 | 测试/CI | macOS 双 libomp 段错误/abort + 测试与生产环境审查（后端/环境） | [08-testing-ci](./08-testing-ci.md) |
| §28 | 配置/安全 | 无鉴权 WebSocket + 0.0.0.0 绑定 + execution_mode 注入 + 审批 future 断开泄漏（安全审计高危） | [03-backend-config-security](./03-backend-config-security.md) |
| §29 | 配置/安全 | config/settings.py 缺失 `import sys` → NameError 隐患（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §30 | 测试/CI | ScreenMonitor 后台线程遗留 → pytest 全量随机挂起（后端/测试） | [08-testing-ci](./08-testing-ci.md) |
| §31 | 测试/CI | test_detect_text OCR 顺序污染 —— sys.modules 模块级置 None（后端/测试） | [08-testing-ci](./08-testing-ci.md) |
| §32 | 测试/CI | BytesIO 内部 buffer 不被 pympler 计算 —— 泄漏检测盲区（测试基建） | [08-testing-ci](./08-testing-ci.md) |
| §33 | 测试/CI | 本地代理 fake-ip 解析 example.com 为内网 IP → SSRF 防护误判（测试环境） | [08-testing-ci](./08-testing-ci.md) |
| §34 | 测试/CI | test_api 版本断言过期（2.0.0 vs 实际版本）（测试） | [08-testing-ci](./08-testing-ci.md) |
| §35 | 测试/CI | macOS 不允许 resource.setrlimit(RLIMIT_AS) 低于当前用量（测试基建） | [08-testing-ci](./08-testing-ci.md) |
| §36 | 测试/CI | sys.getallocatedobjects() 在 Python 3.13 移除（测试基建） | [08-testing-ci](./08-testing-ci.md) |
| §37 | 测试/CI | EventStore.__del__ 对假 faiss 索引触发 GC 死循环（后端/测试） | [08-testing-ci](./08-testing-ci.md) |
| §38 | 流程/文档 | bug 文档系统性审计 —— 发现 2 处同类问题并修复 | [09-process-docs](./09-process-docs.md) |
| §39 | 思考/模型 | mypy 修复引入回归：input_controller 幂等被破坏（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §40 | 记忆/因果 | 深度回忆无噪声守卫 → 无关查询臆造因果链（后端） | [02-backend-memory](./02-backend-memory.md) |
| §41 | 记忆/因果 | 跨场景事件污染佐证 + 增量挂链自我强化（后端） | [02-backend-memory](./02-backend-memory.md) |
| §42 | 记忆/因果 | 显式归属守卫过度杀伤同链事件（后端） | [02-backend-memory](./02-backend-memory.md) |
| §43 | 记忆/因果 | 因果链单节点噪点与重复刷屏（后端/展示） | [02-backend-memory](./02-backend-memory.md) |
| §44 | 测试/CI | CI 全量随机 flaky —— `set_event_loop(None)` 残留污染（测试） | [08-testing-ci](./08-testing-ci.md) |
| §45 | 测试/CI | CI 全量随机 flaky —— `/tools` 路由断言（测试，根因未复现） | [08-testing-ci](./08-testing-ci.md) |
| §104（改号） | 实时链路 | 前端连不上后端：IPv6 localhost 陷阱 + 前端代理层测试盲区（前端/后端） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §46 | 思考/模型 | 模型 client 缓存旧配置 → 改 API URL/Key 不生效（后端/实时生效缺失） | [01-backend-thinking](./01-backend-thinking.md) |
| §47 | 思考/模型 | 纯对话人设无视编排 active 状态 → 强制套用已停用 agent 的人设（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §48 | 思考/模型 | 同类问题排查：人设选择硬编码 / 配置缓存的系统性审计 | [01-backend-thinking](./01-backend-thinking.md) |
| §49 | 记忆/因果 | 心理活动（conscience）对话缓存跨会话累计（后端） | [02-backend-memory](./02-backend-memory.md) |
| §50 | 记忆/因果 | 因果图不跟记忆库走 → 多记忆库因果知识跨库污染（后端） | [02-backend-memory](./02-backend-memory.md) |
| §51 | 记忆/因果 | 心理活动显示思维链而非内心独白（后端） | [02-backend-memory](./02-backend-memory.md) |
| §105（改号） | 记忆/因果 | 切换会话后心理活动框消失 —— mental 事件不持久化（前端/后端） | [02-backend-memory](./02-backend-memory.md) |
| §52 | 测试/CI | CI 测试失败排查：PyQt6 环境缺失 + 测试依赖真实用户配置（测试/环境） | [08-testing-ci](./08-testing-ci.md) |
| §53 | 测试/CI | 配置指纹重建在测试环境触发真实 client 构造 → `API key 不能为空`（后端/测试） | [08-testing-ci](./08-testing-ci.md) |
| §54 | 测试/CI | CI `/tools` 路由断言失败——fastapi 0.141 惰性 `_IncludedRouter`（测试） | [08-testing-ci](./08-testing-ci.md) |
| §55 | 测试/CI | 依赖版本漂移——CI 装最新、本地停旧版（`>=` 不锁版本） | [08-testing-ci](./08-testing-ci.md) |
| §106（改号） | 记忆/因果 | 环境感知未注入大模型上下文 —— dd1ee8b 重构回归（后端） | [02-backend-memory](./02-backend-memory.md) |
| §56 | 配置/安全 | 人设存储两套来源 + 反馈闭环节点集合未按会话隔离（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §107（改号） | 记忆/因果 | 文件感知功能实际无效 —— 采集端与消费端管道断（后端） | [02-backend-memory](./02-backend-memory.md) |
| §57 | 思考/模型 | thinking 模式 tool loop 报 400：assistant 消息未回传 reasoning_content（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §58 | 测试/CI | CI 全量下 socket/子进程测试 20s 超时误杀（测试/CI） | [08-testing-ci](./08-testing-ci.md) |
| §59 | 思考/模型 | delegate_task 的 wait_seconds 解析后未传递 —— 上级设置的下级超时恒走 300s 兜底（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §60 | 思考/模型 | think_once 外层 120s 嵌套覆盖 runner 的 THINK_TIMEOUT —— 上级设置的超时被内层吞掉（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §61 | 思考/模型 | 委托链不可重建 + 超时后思考上下文丢失（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §62 | 思考/模型 | thinking_result 两处生产路径 delegation_id 语义不一致 —— RuntimeExpert 路径用 task_id，黑板委托链查不到（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §63 | 思考/模型 | start_runner 委托节点记录覆盖 delegate 分发记录 —— 委托角色名被 identity.role 替换（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §64 | 思考/模型 | 断点续思考的"从中断处继续"指令在多次超时重试下累积（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §65 | 记忆/因果 | 黑板 final_response 设置后未落库 —— 重启后最终回复丢失（后端） | [02-backend-memory](./02-backend-memory.md) |
| §66 | 测试/CI | 测试直接篡改全局单例 ToolRegistry._tools —— 全量 CI 69 个失败（测试污染） | [08-testing-ci](./08-testing-ci.md) |
| §67 | 思考/模型 | 新增控制工具 read_context 误加入所有 tier —— expert 权限越权（后端/测试） | [01-backend-thinking](./01-backend-thinking.md) |
| §68 | 实时链路 | todo 面板 3 秒轮询 —— 改 WS 事件推送 + 按需拉取（前端/后端） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §69 | 聊天/气泡 | 前端上下文占用显示偏低 —— context_tokens 未计入工具调用历史（后端） | [04-frontend-chat](./04-frontend-chat.md) |
| §70 | 思考/模型 | 模型调用失败：'NoneType' object is not iterable —— SSE 流 delta.tool_calls 为 null（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §71 | 聊天/气泡 | 切换会话后出现多余的独立"思考"气泡（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §72 | 聊天/气泡 | 运行时与恢复路径不一致（同类排查）—— 专家气泡数量/思考区属性名（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §73 | 聊天/气泡 | 恢复路径工具 trace 双份 + 审批文本污染思考区（前端/后端，§71 同类） | [04-frontend-chat](./04-frontend-chat.md) |
| §74 | 聊天/气泡 | 架构根治：运行时与恢复路径共用同一分类规则（前端重构） | [04-frontend-chat](./04-frontend-chat.md) |
| §75 | 页面/样式 | AI 输出序号（有序列表）超出气泡边界（前端样式） | [05-frontend-ui](./05-frontend-ui.md) |
| §76 | 思考/模型 | 长时间挂机后："Task was destroyed but it is pending!"（asyncio Event.wait 残留，后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §77 | 配置/安全 | 授权设置页冗余的 API Key 手动输入（前端，安全暴露反模式） | [03-backend-config-security](./03-backend-config-security.md) |
| §78 | 配置/安全 | 授权设置 tab 运行时配置表明文展示模型 API Key（前端，§77 同类） | [03-backend-config-security](./03-backend-config-security.md) |
| §79 | 思考/模型 | 委托角色名解析硬编码（delegation_port ROLE_TO_IDENTITY，后端，§77 同类隐患） | [01-backend-thinking](./01-backend-thinking.md) |
| §80 | 思考/模型 | "正在思考 Ns" 计时从会话连接起算，而非当前任务（后端 api_stream） | [01-backend-thinking](./01-backend-thinking.md) |
| §81 | 测试/CI | Vercel 部署失败：pyaudio 编译需要 portaudio.h（服务器/无头环境依赖治理） | [08-testing-ci](./08-testing-ci.md) |
| §82 | 实时链路 | 多会话并行处理：WS 单连接 + 全局处理状态导致会话互相干扰（前端 ws client / chat store / Chat.vue） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §83 | 思考/模型 | 编排页关闭主管/专家后，System Prompt 仍拼接其上下文（能力表未过滤启用状态） | [01-backend-thinking](./01-backend-thinking.md) |
| §84 | 思考/模型 | 编排页切换总指挥后启动仍走 orchestrator，被启停开关拒绝（编排器硬编码身份） | [01-backend-thinking](./01-backend-thinking.md) |
| §85 | 聊天/气泡 | 同一 AI 多次发言被合并到一个气泡（前端专家气泡机制） | [04-frontend-chat](./04-frontend-chat.md) |
| §86 | 配置/安全 | personas.yaml 内容丢失 / 系统提示词覆盖重启后消失（配置持久化风险） | [03-backend-config-security](./03-backend-config-security.md) |
| §87 | 配置/安全 | personas.yaml 并发写丢失更新 —— 无进程级锁（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §88 | 配置/安全 | settings.json 并发写丢失更新（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §89 | 配置/安全 | delete_custom_agent 四次独立 read-modify-write 级联丢数据（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §90 | 配置/安全 | _save_personas_yaml 吞掉异常 —— 调用方假设成功（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §91 | 聊天/气泡 | switchToSession 重放路径合并同一身份的多次输出（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §92 | 思考/模型 | _session_dialog_history 并发覆盖 —— 同 session 两次请求互相覆盖（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §93 | 思考/模型 | TurnContext fragments 按 source 覆盖 —— 同源不同内容丢失（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §94 | 聊天/气泡 | thinking 内容子串去重误杀 —— 短文本被长文本包含导致丢失（前端） | [04-frontend-chat](./04-frontend-chat.md) |
| §95 | 思考/模型 | 思考超时按“整轮墙钟”计时 —— 工具执行吃掉预算、界面长时间无回复（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §96 | 配置/安全 | 配置文件跨进程写竞态 —— 仅线程锁挡不住多进程（后端） | [03-backend-config-security](./03-backend-config-security.md) |
| §97 | 思考/模型 | plan 模式把只读委派也拦掉 —— 关键词为空仍返回拦截（后端） | [01-backend-thinking](./01-backend-thinking.md) |
| §98 | 实时链路 | 新事件只在浏览器里生效 —— TUI 看不到工具、SSE 整段憋到结束、回放同轮错位（跨端） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §99 | 实时链路 | SSE 出口看不到工具/流式帧；TUI 不认可应答帧（后端 + TUI） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §100 | 实时链路 | SSE 会话被当成“前端已断开”：握手拦下本轮 LLM、推送只认 WS、断开不清理（后端） | [06-realtime-streaming](./06-realtime-streaming.md) |
| §101 | 聊天/气泡 | 重放把主管/专家多次输出合并成一个气泡 —— entry_type 没有落库（前端 + 后端，§85/§91） | [04-frontend-chat](./04-frontend-chat.md) |
| §102 | 配置/安全 | settings.json 解析失败时静默当作 `{}` 写回 —— 一次保存抹掉全部配置（后端，§88 残留） | [03-backend-config-security](./03-backend-config-security.md) |
| §103 | 桌宠 | 桌宠「前端收不到消息」：代理泄漏 SSE 上游 + 原生 tool_calls 缺 assistant 回填（前端代理 + 桌宠循环） | [07-desktop-pet](./07-desktop-pet.md) |
