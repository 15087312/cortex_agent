# 待确认的高危行为变更

**Language / 语言**: [简体中文](./BUGS_REQUIRING_CONFIRMATION.md) | [English](./BUGS_REQUIRING_CONFIRMATION.en.md)

> 补测过程中发现的**可能影响生产行为**的问题。修复前需人工确认。
> 非高危/非预期的普通错误 bug 已在测试补全过程中直接修复（见 docs/ERRORS_AND_FIXES.md）。

**当前状态（2026-11 复核）**：§85、§87、§88、§89、§90、§91、§92、§93、§94 已全部

落实修复并有回归测试；§86 只剩"历史数据是在哪丢的"这一待观察项，后端写入/读取路径与
跨进程锁均已就位。仍建议保留本表作为行为变更的追溯记录。

| # | 模块 | 问题 | 影响 | 建议修复 | 状态 |
|---|------|------|------|----------|------|
| 85 | chat.js 专家气泡 | 同一 AI 多次发言合并为一个气泡（同身份复用已有气泡并覆盖 content） | 对话历史丢失：思考过程、工具调用、前次输出被覆盖，仅保留最后一次 | `addExpertMessage` 改为每次输出新建气泡；重放路径对齐 | ✅ 已修（`addExpertMessage` 每次新建气泡；专家推理/工具统一进过程流，气泡内缓冲已移除，见 §101/§101.1） |
| 86 | personas.yaml 持久化 | personas.yaml 内容被整体覆盖/丢失（agent_active、custom agents 全部消失） | 所有编排配置（启停、人设、覆盖、工具权限、模型参数）重启后失效 | 后端写入路径已验证正确；需排查早期数据丢失的触发场景（并发写竞态？打包 app HOME 不同？）；建议增加写入日志或文件变更监控 | 🔍 写入路径已加固（§96 跨进程锁 + §102 拒绝基于空字典写入），历史触发场景仍待观察 |
| 87 | config/settings.py | personas.yaml 并发写丢失更新（14+ 个 set_* 无锁） | 两个并发请求修改同一文件，后者覆盖前者；极端导致 §86 数据丢失 | 加文件级锁或合并单次 read-modify-write | ✅ 已修（§96：线程锁 + `faiss_file_lock` 跨进程锁包住整个 read-modify-write；全部 `set_*` 都走 `_modify_personas_yaml`） |
| 88 | config/settings.py | settings.json 并发写丢失更新（save_user_config 无锁） | Settings 页面快速操作或双 tab 并发修改，后者覆盖前者 | 加文件级锁或乐观锁 | ✅ 已修（§96 同款双锁；另修「解析失败当空文件写回」的整文件清空路径，见 §102） |
| 89 | api/main.py | delete_custom_agent 五次独立 read-modify-write 级联 | 删除 agent 后配置不一致（残留 persona/override/tools/params） | 合并为单次 read-modify-write | ✅ 已修（`Settings.delete_custom_agent` 单次 `_modify_personas_yaml` 清理全部键） |
| 90 | config/settings.py | _save_personas_yaml 吞异常，调用方假设成功 | 写入失败时 API 返回 success toast，用户以为保存成功但实际没落盘 | 返回 success/failure，调用方据此告知用户 | ✅ 已修（失败清理 tmp 后 `raise`，API 层转错误响应） |
| 91 | chat.js switchToSession | 重放路径合并同一身份的多次输出 | 切换会话后只显示最后一次输出，中间版本丢失 | 重放路径每次输出新建气泡 | ✅ 已修（§101：恢复 `entry_type` 后逐条新建气泡、缓冲随输出消耗） |
| 92 | multi_model_orchestrator | _session_dialog_history 并发覆盖 | 同 session 快速两条消息，展开面板显示错误历史 | per-session 锁或独立存储 | ✅ 已修（`_session_dialog_history_lock` 保护写入） |
| 93 | context/pool.py | TurnContext fragments 按 source 覆盖 | 同源不同模块的上下文被后者覆盖丢失 | 改为 list 或复合 key | ✅ 已修（`source` / `source#2`… 复合 key + 内容哈希去重） |
| 94 | chat.js thinking | 子串去重误杀（includes 匹配） | "分析需求" 被 "分析需求并制定方案" 包含，后者跳过 | 改为精确匹配（Set 或 trim 比较） | ✅ 已修（`_thinkingSeen` Set 精确匹配） |

详见 `docs/ERRORS_AND_FIXES.md` §85-§94、§96、§101-§102。
