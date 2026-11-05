# Error Log & Fixes · Desktop Pet

[← Back to index](./README.en.md) | [简体中文](./07-desktop-pet.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 1 sections: §103.

---
## 103. Desktop pet "frontend receives no messages": proxy leaks the upstream SSE + native tool_calls missing the assistant echo (frontend proxy + pet loop)

**Symptom:** In the desktop app (frontend on 8765, proxying `/api` to the backend) the pet page's reply bubble stays empty; after refreshing or reopening the page, new messages still receive no tokens. The backend log shows the pet still producing a self-talk round every ~30s and writing it to history. The Qt pet window that talks to 8080 directly is unaffected.

**Root cause (two compounding bugs):**

1. **The proxy never closes the upstream SSE when the browser disconnects.** `frontend/server.py::_proxy_sse` used `requests.iter_content` to block on the upstream; it only noticed the client was gone when a write failed. Between two pet thinking rounds there can be 20–30s of silence with no writes at all. The proxy therefore kept the upstream connection alive, uvicorn never saw a disconnect, and `PetEngine.stream_chat`'s `finally` (which cancels the queued job) never ran: the `max_loop_rounds=0` continuous thinking kept spinning and occupied the serial `PetTaskQueue`, so requests from a freshly opened page queued behind it and received no tokens.
2. **Native tool_calls rounds never echoed the assistant message back into history.** When `_think` got `response.message.tool_calls` it only returned `{tool, params, tool_call_id}`, and `_react_cycle` then appended the `role="tool"` observation. On the next request that tool message had no preceding `assistant(tool_calls)`, so OpenAI-compatible providers returned 400: `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`. The SSE stream only carried `event: error` + `done`, so the frontend showed no content.

**Fix:**

1. `_proxy_sse` now uses `http.client` + a dedicated reader thread + 0.5s polling: `select` + `recv(MSG_PEEK)` returning EOF means the browser is gone, so the loop exits immediately; `finally` closes both `resp` and `conn` to tear down the upstream (for EOF-delimited responses such as `Connection: close`, `conn.sock` is `None` and the real fd lives on `resp.fp`). After connecting, the read timeout is widened to `SSE_IDLE_TIMEOUT` so the 5s connect timeout does not kill normal thinking gaps.
2. Before returning a native tool call, `_think` appends `ChatMessage(role="assistant", content=raw or None, tool_calls=[tc])` to the real `messages` list (`msgs` is only this round's request copy), so every `role="tool"` message is immediately preceded by the matching `assistant(tool_calls)`.

**Tests:**
- `tests/unit/test_frontend_server.py::test_proxy_sse_closes_upstream_when_client_disconnects`: a raw socket simulates a browser disconnect and asserts the upstream notices within 5s (previously never).
- `tests/unit/test_pet_react_custom_loop.py::test_native_tool_call_roundtrip` gains an assertion that the message before `role="tool"` is an `assistant(tool_calls)` carrying the same `tool_call_id`.
- Live check: a pet request through the 8765 proxy with the client aborted after 20s produced no new history rows within 80s (previously it kept spinning for 3+ minutes); tool-call rounds now produce normal content.

### 103.1 Deadlock with the reader thread when closing upstream on disconnect (follow-up)

**Symptom:** After the 103 fix, "refresh stops the loop" worked in most cases, but while the
upstream stayed **silent** (the usual state between two thinking rounds) the proxy thread hung
in `resp.close()` until the next frame or the 300s idle timeout. Every refresh therefore leaked
one upstream connection and one proxy thread — the same class of problem 103 addressed.

**Root cause:** The upstream reader thread blocks in `resp.read1()` holding the
`HTTPResponse.fp` (`BufferedReader`) lock; the main thread's `resp.close()` needs that same
lock, so they wait on each other. `conn.close()`, queued after `resp.close()`, never ran.

**Fix:** `_proxy_sse` keeps `up_sock = conn.sock` after `conn.connect()` (a
`Connection: close` response clears `conn.sock`), and the `finally` block calls
`up_sock.shutdown(SHUT_RDWR)` first so the blocking read returns EOF immediately, then closes
`resp` and `conn`.

**Tests:**
- `tests/unit/test_frontend_server.py::test_proxy_sse_client_disconnect_stops_immediately`
  asserts the disconnect path shuts down before closing and closes both response and connection.
- `tests/leak/test_leak_frontend_proxy.py`: thread count does not grow after 60 proxies;
  with a silent upstream and 5 client disconnects, 5/5 upstream connections must close within
  10s (0/5 before the fix).
- `tests/integration/test_frontend_proxy_stream.py`: a real uvicorn backend asserts the SSE
  generator receives `CancelledError` after the client disconnects.
- `tests/integration/test_frontend_proxy_stream.py::test_browser_disconnect_stops_pet_engine`:
  real uvicorn + real pet routes + real `PetEngine`/`PetTaskQueue`/`CustomLoop`/SSE with only the
  external model replaced by a local fake client. It first reads two `round_complete` frames to
  prove continuous thinking is running, then lets the model enter a **silent period** (the only
  case the old proxy could not detect), closes the browser connection, and asserts the thinking
  task receives `CancelledError`. Negative control: disabling the disconnect check (`if False`)
  makes this test fail.
