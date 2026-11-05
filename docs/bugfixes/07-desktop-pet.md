# 错误原因与修复记录 · 桌宠

[← 返回总索引](./README.md) | [English](./07-desktop-pet.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 1 节：§103。

---
## 103. 桌宠「前端收不到消息」：代理泄漏 SSE 上游 + 原生 tool_calls 缺 assistant 回填（前端代理 + 桌宠循环）

**现象：** 桌面端（前端跑在 8765，经代理访问后端）打开桌宠页面聊天，回复气泡一直空白；
刷新或重新打开页面后再发消息，也收不到任何 token。后台日志里桌宠仍在每 ~30s 产出一轮
「自言自语」并写入历史。直连 8080 的 Qt 桌宠小窗不受影响。

**根因（两处叠加）：**

1. **代理在浏览器断开后不关闭上游 SSE。** `frontend/server.py::_proxy_sse` 用
   `requests.iter_content` 阻塞读上游，只有「写客户端失败」时才会发现浏览器已经走了；
   桌宠两轮思考之间可以静默 20–30s，期间根本没有写操作。于是代理一直挂着上游连接，
   uvicorn 感知不到断连，`PetEngine.stream_chat` 的 `finally`（取消队列任务）永远不执行：
   `max_loop_rounds=0` 的持续思考继续空转，并占住 `PetTaskQueue` 的串行队列，新打开的
   页面提交的请求排在后面拿不到 token。
2. **原生 tool_calls 轮次没有把 assistant 消息回填进历史。** `_think` 收到
   `response.message.tool_calls` 时只返回 `{tool, params, tool_call_id}`，而
   `_react_cycle` 紧接着 append `role="tool"` 的观察消息。下一轮请求里 `tool` 消息
   没有前置的 `assistant(tool_calls)`，OpenAI 兼容接口直接 400：
   `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`，
   SSE 只剩 `event: error` + `done`，前端自然看不到正文。

**修复：**

1. `_proxy_sse` 改为 `http.client` + 独立读线程 + 0.5s 轮询：用
   `select` + `recv(MSG_PEEK)` 读到 EOF 判断浏览器已断开，立刻退出循环；`finally`
   同时 `resp.close()` 与 `conn.close()` 断开上游（`Connection: close` 这类 EOF 分隔响应下
   `conn.sock` 会被置空，真正的 fd 挂在 `resp.fp` 上）。连接建立后把读超时放宽到
   `SSE_IDLE_TIMEOUT`，避免 5s 连接超时误杀正常的思考间隔。
2. `_think` 返回原生工具调用前，把
   `ChatMessage(role="assistant", content=raw or None, tool_calls=[tc])` 回填进真实
   `messages`（`msgs` 只是本轮请求副本），保证 `role="tool"` 永远紧跟对应的
   `assistant(tool_calls)`。

**测试：**
- `tests/unit/test_frontend_server.py::test_proxy_sse_closes_upstream_when_client_disconnects`：
  裸 socket 模拟浏览器断开，断言上游在 5s 内感知断连（修复前永不感知）。
- `tests/unit/test_pet_react_custom_loop.py::test_native_tool_call_roundtrip` 新增断言：
  `role="tool"` 的前一条必须是带同 `tool_call_id` 的 `assistant(tool_calls)`。
- 实机验证：经 8765 代理发起桌宠请求、20s 客户端中断，80s 内无新增历史行
  （修复前会继续空转 3 分钟以上）；工具调用轮次可正常产出正文。

### 103.1 断连关闭上游时与读线程抢锁（补）

**现象：** 103 的修复上线后，「刷新即停止」在多数情况生效，但当上游**暂时没有任何字节**
（两轮思考之间的静默期，正是桌宠最常见的状态）时，代理线程会卡在
`resp.close()` 上直到上游下一帧或 300s 空闲超时——期间上游连接与代理线程都还在，
每刷新一次泄漏一条连接 + 一个线程，与 103 要解决的问题同源。

**根因：** 上游在独立读线程里阻塞于 `resp.read1()`，持着 `HTTPResponse.fp`
（`BufferedReader`）的内部锁；主线程 `resp.close()` 需要同一把锁，于是互等。
`conn.close()` 排在 `resp.close()` 之后，也执行不到。

**修复：** `_proxy_sse` 在 `conn.connect()` 后保留 `up_sock = conn.sock`
（`Connection: close` 响应会把 `conn.sock` 置空），`finally` 中先
`up_sock.shutdown(SHUT_RDWR)` 让阻塞读立即返回 EOF（唤醒读线程、释放缓冲锁），
再依次 `resp.close()` / `conn.close()`。

**测试：**
- `tests/unit/test_frontend_server.py::test_proxy_sse_client_disconnect_stops_immediately`
  断言断连路径确实先 `shutdown(SHUT_RDWR)` 再关闭，且上游响应/连接都被关闭。
- `tests/leak/test_leak_frontend_proxy.py`：60 次转发后线程数不增长；
  静默上游 + 客户端断开 5 次，上游连接必须 5/5 在 10s 内关闭（修复前 0/5）。
- `tests/integration/test_frontend_proxy_stream.py`：真实 uvicorn 后端断言
  客户端断开后 SSE 生成器收到 `CancelledError`（桌宠队列任务随之取消）。
- `tests/integration/test_frontend_proxy_stream.py::test_browser_disconnect_stops_pet_engine`：
  真实 uvicorn + 真实桌宠路由 + 真实 `PetEngine`/`PetTaskQueue`/`CustomLoop`/SSE，只有外部
  模型被换成本地假 client。先读到两轮 `round_complete` 证明持续思考在跑，再让模型进入
  **静默期**（旧代理唯一发现不了断开的场景）后关闭浏览器连接，断言思考任务被 `CancelledError`
  取消。负向验证：把断连检测改成 `if False` 后该用例必失败。
