# 待确认的高危行为变更

> 补测过程中发现的**可能影响生产行为**的问题。修复前需人工确认。
> 非高危/非预期的普通错误 bug 已在测试补全过程中直接修复（见 docs/ERRORS_AND_FIXES.md）。

| # | 模块 | 问题 | 影响 | 建议修复 |
|---|------|------|------|----------|
| 85 | chat.js 专家气泡 | 同一 AI 多次发言合并为一个气泡（同身份复用已有气泡并覆盖 content） | 对话历史丢失：思考过程、工具调用、前次输出被覆盖，仅保留最后一次 | `addExpertMessage` 改为每次输出新建气泡；思考/工具缓冲在输出时消耗而非清空；重放路径对齐 |
| 86 | personas.yaml 持久化 | personas.yaml 内容被整体覆盖/丢失（agent_active、custom agents 全部消失） | 所有编排配置（启停、人设、覆盖、工具权限、模型参数）重启后失效 | 后端写入路径已验证正确；需排查早期数据丢失的触发场景（并发写竞态？打包 app HOME 不同？）；建议增加写入日志或文件变更监控 |
| 87 | config/settings.py | personas.yaml 并发写丢失更新（14+ 个 set_* 无锁） | 两个并发请求修改同一文件，后者覆盖前者；极端导致 §86 数据丢失 | 加文件级锁或合并单次 read-modify-write |
| 88 | config/settings.py | settings.json 并发写丢失更新（save_user_config 无锁） | Settings 页面快速操作或双 tab 并发修改，后者覆盖前者 | 加文件级锁或乐观锁 |
| 89 | api/main.py | delete_custom_agent 五次独立 read-modify-write 级联 | 删除 agent 后配置不一致（残留 persona/override/tools/params） | 合并为单次 read-modify-write |
| 90 | config/settings.py | _save_personas_yaml 吞异常，调用方假设成功 | 写入失败时 API 返回 success toast，用户以为保存成功但实际没落盘 | 返回 success/failure，调用方据此告知用户 |
| 91 | chat.js switchToSession | 重放路径合并同一身份的多次输出 | 切换会话后只显示最后一次输出，中间版本丢失 | 重放路径每次输出新建气泡 |
| 92 | multi_model_orchestrator | _session_dialog_history 并发覆盖 | 同 session 快速两条消息，展开面板显示错误历史 | per-session 锁或独立存储 |
| 93 | context/pool.py | TurnContext fragments 按 source 覆盖 | 同源不同模块的上下文被后者覆盖丢失 | 改为 list 或复合 key |
| 94 | chat.js thinking | 子串去重误杀（includes 匹配） | "分析需求" 被 "分析需求并制定方案" 包含，后者跳过 | 改为精确匹配（Set 或 trim 比较） |

详见 `docs/ERRORS_AND_FIXES.md` §85-§94。
