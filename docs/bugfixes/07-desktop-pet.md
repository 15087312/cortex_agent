# 错误原因与修复记录 · 桌宠

[← 返回总索引](./README.md) | [English](./07-desktop-pet.en.md)

> 由 `docs/ERRORS_AND_FIXES.md` 按模块拆分；章节编号沿用原文（§104–§107 为原文重复编号的修订，见总索引）。本文件共 2 节：§103、§109。

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

---
## 109. 桌宠语音「全都跑不通，测试却全绿」：假音频 + 假路由 + 错误 VAD 假设（桌宠 + 前端）

**现象（用户侧四件事都不工作）：**

1. 设置页打开「实时监听」没反应，桌宠页的麦克风状态永远显示「空闲」，实际没在听；
2. 桌宠从不说话（TTS 无声音），但状态里 TTS 后端显示可用；
3. 桌宠总开关刷新后与上次保存的不一致（有时自动拉起窗口）；
4. 人设页无法改名、新建会继承当前人设内容、删除作用于「当前选中」、切换会静默丢编辑。

**根因（五处独立缺陷叠加）：**

1. **前端调了不存在的路由。** 设置页用 `GET /config/{key}` 读总开关，后端只有
   `GET /config`（整份）与 `PUT /config/{key}`。404 被 `catch { /* 静默 */ }` 吞掉，
   开关永远停在默认值。
2. **麦克风归属错了。** 实时语音由桌宠窗（QWebEngine）/浏览器 `getUserMedia` 采集，
   QtWebEngine 对本地页面直接拒权限，且只在页面打开时才有流；后端进程（真正要打断
   TTS、合并转写的那一端）拿不到音频。
3. **VAD 阈值假设错了。** 固定能量阈值 300，而本机实测环境噪声 RMS 中位数 ≈ 375
   → 每一帧都「有声」，监听状态长期 listening、不断误触发。
4. **TTS 只剩一条已死的链路。** `output_system.tts` 走 edge-tts，微软服务端稳定
   返回 `403 Invalid response status`；`TTSEngine.available` 只探测「包能不能
   import」→ 状态永远为可用，合成返回 `None`，桌宠静默。
5. （实测顺带发现）**麦克风停止顺序会崩进程。** `stop()` 先 `stop_stream()`/`terminate()`
   时，采集线程还阻塞在 `read()`，踩到已释放的 PortAudio 对象 → `Bus error` 直接带走
   整个后端进程。

**修复：**

- **后端常驻监听** `modules/desktop_pet/voice/mic_listener.py`：pyaudio 16kHz 采集 →
  能量 VAD 断句 → 单线程 ASR；采集线程永不阻塞。VAD 自适应：启动先采 700ms 环境音
  取中位数作噪声底，阈值 `max(配置值, 噪声底×2.2)`（上限 8000），再加 90ms 起音去抖、
  240ms 最短语音长度；噪声底 EMA 下限 1.0 防次正规数衰减。人声一起立刻回调打断 TTS。
  `stop()` 顺序改为：置 `_running=False` → join 采集线程（等它自然退出）→ 关流 → terminate。
- **本地 TTS 链** `voice/tts.py`：Piper（`data/models/piper/*.onnx`，进程内缓存）→
  macOS `say`（中文音色）→ edge-tts 兜底；新增 `wav_is_audible()` 校验产物
  （时长 ≥0.15s + 16bit 峰值 ≥8），静音/空壳一律判失败并落到下一个后端；
  `_EDGE_HINT` 记住真实请求失败，状态里不再声称 edge 可用。
- **发声语义** `modules/desktop_pet/speech.py`：模型用 `<say>…</say>` 标记「说出口」，
  其余是内心独白（入库/显示/参与思考，不朗读）；无标签时「用户刚说话 → 整段朗读，
  自己思考 → 不朗读」。
- **转写注入 + 冷却唤醒**：`submit_utterance()` 先进缓冲，每来一句重新计时，静默满
  `min_think_interval` 合并成一条用户消息唤醒一轮；新增只读的
  `GET /stream/pet/voice/status`（不因轮询创建引擎单例）。
- **前端**：总开关改读整份 `GET /config`；监听状态由后端提供（桌宠窗只显示，不再抢麦）；
  未运行时标签为「未监听」而不是「空闲」；人设页重做为「左列表（使用中标记）/右独立
  编辑器（改名 + 保存/取消/启用/删除二次确认，新建空表单并创建即启用）」。
- **空拍退避 → 用户连续无回复退避**：`CustomLoop._no_reply_streak` 只在「用户说了话、
  整轮却没产出回复」时 +1，回复即复位；倍率 2^n 封顶 `idle_backoff_max`，模型自己
  空拍不再影响倍率。
- **收尾又修掉三处产物/生命周期缺陷**：8bit 静音 WAV 被判「有声」、TTS 输出目录按 cwd
  解析（`/audio` 挂载可能指向别处 → 合成成功却 404）、`PetTaskQueue` worker 不随
  `stop()` 收尾（循环关闭后 pending task 报错）。三处均补了回归用例，细节见 §110
  「顺带修掉的真实缺陷」。

**为什么这么多缺陷能同时通过 647 个前端 + 166 个后端测试：** 见 §110（假音频替身、
前端假路由、合成数据与实现共享假设、真机 ASR 被 conftest 禁用）。

**验证（本机实测，不是 mock）：**

- `say` 合成 3.92s / 22050Hz / RMS 4006 的真实 WAV；重采样到 16k 后 Whisper 转回
  `你好,我是你的捉蟲,今天也要开心哦`；
- 真实麦克风打开成功：`能量阈值 766.7 / 噪声底 348.5 / 状态 idle`（安静时不再假触发）；
- `pytest tests/unit/test_pet_voice.py tests/unit/test_pet_voice_tts_chain.py
  tests/unit/test_frontend_api_contract.py tests/unit/test_pet_store_personas.py`
  46 项通过；真机 `-m external` 3 项通过；`vitest src/pages/Pet.spec.js` 38 项通过。

**教训：** 语音这类「必须看见/听见才算成功」的功能，验证对象必须是**产物**（WAV 的
时长与波形、真实麦克风的噪声底），不是函数返回值和日志；跨端调用必须有路由契约测试，
否则前端 mock 会把 404 变成 `{}`。
