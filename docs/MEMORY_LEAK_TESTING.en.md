# Memory Leak Testing and Detection System

**Language**: [English](./MEMORY_LEAK_TESTING.en.md) | [简体中文](./MEMORY_LEAK_TESTING.md)

> This document records the project's memory leak **detection mechanisms**, **leak test suite**, and **how to run and configure them**.
> For related bug fixes see `docs/ERRORS_AND_FIXES.md` §30/32/35/36/37.

## 1. System Overview (Three Layers + Coverage Proof)

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ 1. Detection (default on)  conftest global, full coverage of unit+integration │
│    · muppy byte sampling + trend verdict (every 100 tests)                    │
│    · pympler type diff before/after session                                   │
│ 2. Verification           tests/leak/ 10-type leak suite                      │
│    · scripts/verify_leak_detection.py asserts each is identifiable            │
│ 3. Termination            memory watchdog: os._exit(1) if over limit          │
│    · keeps runaway memory from dragging down the local machine / CI runner    │
│ 4. Coverage proof         [MODULE-COVERAGE] module execution checklist        │
│    · 195/195 production modules executed by tests                             │
└───────────────────────────────────────────────────────────────────────────────┘
```

## 2. Detection Mechanism (Enabled by Default in conftest)

Runs automatically on every pytest session (unit / integration), **zero configuration**:

| Layer | Implementation | What Is Detected |
|---|---|---|
| Byte trend | `pympler.muppy.get_size` samples live bytes every `LEAK_INTERVAL`(100) tests | Sustained growth across tests = leak (includes bytes/numpy raw memory) |
| Trend verdict | Fits a slope over the latter half of sample points at session end | `> LEAK_RATE_THRESHOLD`(256 KiB/test) reports `⚠ suspected memory leak` |
| Type identification | pympler `SummaryTracker` diff before/after session | Top unreleased object types |
| Sample identification | Sample points record nodeid | Can locate the tests corresponding to jump intervals |

Output entry point: `pytest_sessionfinish` (displays reliably after capture release), tagged `[LEAK-DETECT]`.

### Example Verdict Output

```
[LEAK-DETECT] Memory Leak Detection Report
  [Trend] Sample points (test count, live KiB, node):
     100  94860 KiB  ...
     200  97236 KiB  ...
  [Trend] Last 27 sample points: 187021 → 500315 KiB (120.5 KiB added per test)
  ✓ Memory stable (120.5 KiB per test, threshold 256 KiB/test)
```

### Configuration Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LEAK_INTERVAL` | 100 | Sampling interval (number of tests) |
| `LEAK_RATE_THRESHOLD` | 256 | Added KiB per test above which a leak is flagged |
| `LEAK_REPORT` | 1 | 0 disables the report |
| `CORTEX_TEST_MEM_LIMIT_MB` | 4096 | Memory watchdog upper limit (0 disables) |
| `CORTEX_TEST_MODULE_UNCOVERED_MAX` | 10 | Max allowed number of uncovered production modules |

## 3. Leak Test Suite (tests/leak/)

10 independent files, each **deliberately constructing** a leak of a specific type/module to verify the detection system can identify it.

| # | File | Leak Type | Module Domain |
|---|---|---|---|
| A | `test_leak_object_growth.py` | Object reference accumulation (unbounded list/dict) | General |
| B | `test_leak_bytes_raw.py` | Raw memory (bytes; object count unchanged while bytes grow) | General |
| C | `test_leak_reference_cycle.py` | Reference cycle (with `__del__`; GC cannot reclaim) | General |
| D | `test_leak_threads.py` | Thread object accumulation | General |
| E | `test_leak_global_cache.py` | Unbounded cache (dict keys keep growing) | General |
| F | `test_leak_module_memory.py` | Unbounded append of memory events/blackboard observations | modules/memory |
| G | `test_leak_module_perception.py` | Event subscription table + perception event queue | modules/perception |
| H | `test_leak_module_model.py` | Model client instance accumulation | infra/model |
| I | `test_leak_module_database.py` | Unclosed DB session objects | modules/database |
| J | `test_leak_files_resources.py` | File handles/content not released | utils/output |

- All marked with `pytestmark = pytest.mark.leak`, **deselected by default** (not part of the normal suite)
- Run verification: `python scripts/verify_leak_detection.py` (each file in an independent process, asserting the output contains `⚠ suspected memory leak`)

## 4. Investigation Tools (scripts/leak_check.py)

| Mode | Command | Purpose |
|---|---|---|
| RSS monitoring | `python scripts/leak_check.py tests/unit/test_xxx.py` | Externally samples the subprocess's real physical memory to judge linear growth |
| tracemalloc pinpointing | `python scripts/leak_check.py --tracemalloc ...` | Outputs top allocation sites of live memory (slow; suited to small scopes) |
| Full run | `python scripts/leak_check.py` | Full suite + RSS + report |

## 5. Module Coverage List (Guarantees "All Modules Are Within Detection Scope")

Leak detection is a session-level aggregate and **only applies to modules actually "executed by tests"**. At session end conftest outputs:

```
[MODULE-COVERAGE] Production Module Execution Coverage List
  Total production modules: 195  Executed by tests: 195  Not executed: 0
  ✓ All production modules were executed by tests and are within leak detection coverage
```

- Unexecuted modules are listed individually (⚠); exceeding `CORTEX_TEST_MODULE_UNCOVERED_MAX` raises an alarm
- Currently verified: **195/195 production modules fully covered**

## 6. Memory Watchdog (Automatic Termination)

conftest starts a daemon watchdog thread that samples process RSS every 0.5s (psutil); once it exceeds `CORTEX_TEST_MEM_LIMIT_MB` (default 4096MB) it immediately calls `os._exit(1)`.

- Verification: deliberately accumulate 60MB with a 200MB limit → immediate termination printing `[MEM-LIMIT]`
- Purpose: any runaway memory (real leak/runaway test) is killed within 2s, so it never drags down the local machine/CI runner

## 7. Run Entry Points Summary

```bash
# Normal full run (detection enabled by default)
python3 -m pytest tests/unit

# Verify leak detection capability (10 types)
python3 scripts/verify_leak_detection.py

# Pinpoint leaks precisely
python3 scripts/leak_check.py tests/unit/test_xxx.py

# Memory watchdog low-limit test (should terminate automatically)
CORTEX_TEST_MEM_LIMIT_MB=200 python3 -m pytest tests/unit/test_leak_bytes_raw.py
```

## 8. Known Detection Limitations

- pympler is blind to pure C buffers (BytesIO etc.) → leak tests should also accumulate content bytes (see bug §32)
- The full 5353-test process shows ~500MB live (muppy estimate) / RSS ~1.1GB; this is multi-file module accumulation (staircase convergence), not a leak
- `tracemalloc` full-run tracking slows execution 20-30x; use only for small-scope precise pinpointing
- `resource.setrlimit(RLIMIT_AS)` cannot be set low on macOS (see bug §35); the watchdog is used instead
