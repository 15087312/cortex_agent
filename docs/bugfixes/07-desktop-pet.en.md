# Error Log & Fixes · Desktop Pet

[← Back to index](./README.en.md) | [简体中文](./07-desktop-pet.md)

> Split from `docs/ERRORS_AND_FIXES.en.md` by module; section numbers are unchanged (§104–§107 fix duplicated numbers in the original—see the index). 2 sections: §103, §109.

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

---
## 109. Pet voice completely broken while every test passed: fake audio + fake routes + wrong VAD assumption (desktop pet + frontend)

**Symptoms (four user-visible failures):**

1. Turning on "realtime listening" in settings does nothing; the pet page's microphone chip stays "Idle" while nothing is actually listening.
2. The pet never speaks (silent TTS), yet the status line reports a working TTS backend.
3. The master switch no longer matches the last saved value after a refresh (sometimes auto-launching the window).
4. The persona page cannot rename, a new persona inherits the current content, delete acts on the "selected" persona, and switching silently drops edits.

**Root causes (five independent defects):**

1. **Frontend called a route that does not exist.** The settings page read the master switch with `GET /config/{key}`; the backend only has `GET /config` (whole config) and `PUT /config/{key}`. The 404 was swallowed by `catch { /* silent */ }`, so the switch stayed at its default.
2. **The microphone belonged to the wrong process.** Realtime voice was captured with `getUserMedia` in the pet window (QWebEngine)/browser, which QtWebEngine denies for local pages and which only exists while the page is open — the backend (the side that must interrupt TTS and merge transcripts) never got audio.
3. **Wrong VAD assumption.** The fixed energy threshold was 300 while this machine's ambient RMS median is ~375 → every frame counted as speech, so the state was permanently "listening" and fired constantly.
4. **Only one, already-dead TTS path.** `output_system.tts` used edge-tts, whose service returns `403 Invalid response status`; `TTSEngine.available` only probes whether the package imports, so the status always claimed availability while synthesis returned `None`.
5. (Found while testing) **Stopping the microphone crashed the process.** `stop()` called `stop_stream()`/`terminate()` first while the capture thread was blocked in `read()`, touching freed PortAudio objects → `Bus error` took down the backend.

**Fixes:**

- **Resident backend listener** `modules/desktop_pet/voice/mic_listener.py`: pyaudio 16 kHz capture → energy VAD segmentation → single ASR worker; the capture thread never blocks. VAD is adaptive: sample 700 ms of ambient audio at start, take the median as the noise floor, threshold `max(configured, floor × 2.2)` (capped at 8000), plus 90 ms onset debounce and a 240 ms minimum voiced length; the EMA floor is clamped at 1.0 to avoid denormal decay. Detected speech immediately interrupts TTS. `stop()` now sets `_running=False` → joins the capture thread → closes the stream → terminates.
- **Local TTS chain** `voice/tts.py`: Piper (`data/models/piper/*.onnx`, cached per process) → macOS `say` (Chinese voice) → edge-tts fallback; new `wav_is_audible()` validates the artifact (≥0.15 s and 16-bit peak ≥8), so silence/empty shells fail over to the next backend; `_EDGE_HINT` remembers a real failure so the status stops claiming edge is usable.
- **Speech semantics** `modules/desktop_pet/speech.py`: the model wraps what should be spoken in `<say>…</say>`; everything else is inner monologue (persisted, displayed, used for thinking, never spoken). Without tags: user just spoke → speak everything; own thinking → stay silent.
- **Transcript injection + cooldown wake-up**: `submit_utterance()` buffers transcripts and restarts the timer on each new utterance; after `min_think_interval` of silence they are merged into one user message that wakes a round. New read-only `GET /stream/pet/voice/status` (polling never creates an engine singleton).
- **Frontend**: the master switch reads the whole `GET /config`; listening state comes from the backend (the pet window only displays it and no longer grabs the mic); when not running the chip says "Not listening" instead of "Idle"; the persona page is rebuilt as a list (with an in-use marker) plus an independent editor (rename + save/cancel/activate/delete with confirmation; creation starts from an empty form and activates immediately).
- **Empty-tick backoff → user-no-reply backoff**: `CustomLoop._no_reply_streak` increments only when the user spoke and the whole turn produced no reply, and resets on any reply; the multiplier is 2^n capped by `idle_backoff_max`, and the model's own empty ticks no longer affect it.
- **Three more artifact/lifecycle defects fixed during the wrap-up**: a silent 8-bit WAV counted as audible, the TTS output directory resolved against cwd (so `/audio` could point elsewhere — synthesis succeeds, playback 404s), and the `PetTaskQueue` worker never shut down with `stop()` (a pending task errors after the loop closes). All three have regressions; see “Real defects the new tests caught” in §110.

**Why 647 frontend + 166 backend tests all passed anyway:** see §110 (fake audio doubles, fake frontend routes, synthetic data sharing the implementation's assumption, and real ASR being disabled by conftest).

**Verification (measured, not mocked):**

- `say` produced a real 3.92 s / 22050 Hz / RMS 4006 WAV; after resampling to 16 kHz Whisper returned `你好,我是你的捉蟲,今天也要开心哦`.
- The real microphone opened successfully: `energy threshold 766.7 / noise floor 348.5 / state idle` (no more false triggers when quiet).
- `pytest tests/unit/test_pet_voice.py tests/unit/test_pet_voice_tts_chain.py tests/unit/test_frontend_api_contract.py tests/unit/test_pet_store_personas.py` → 46 passed; real-device `-m external` → 3 passed; `vitest src/pages/Pet.spec.js` → 38 passed.

**Lesson:** for features that only "work" when you can see/hear them, assert on the **artifact** (a WAV's duration and waveform, a real microphone's noise floor), not on return values and logs. Cross-boundary calls need a route contract test, otherwise frontend mocks turn 404s into `{}`.
