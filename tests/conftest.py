"""
共享测试 fixtures
"""
import os

# macOS 双 libomp 兜底（OMP: Error #15）：根因修复见 scripts/fix_macos_libomp.py，
# 此变量仅兜底未跑脚本的环境。必须在任何重库导入前设置。
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 测试关闭后台向量化 worker：EventStore 保存事件后会在后台线程延迟加载 embedding
# 模型并推理，与主线程并发触发双 libomp 段错误（见 docs/ERRORS_AND_FIXES.md §27）。
# 测试用按需加载即可，不需要后台异步向量化。
os.environ.setdefault("EMBEDDING_BACKGROUND_WORKER", "false")

import pytest
import sys

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# 内存上限保护：超预期内存占用自动终止测试进程（默认关闭，显式启用）
#
# 原理: 看门狗线程周期采样当前进程 RSS（psutil），超过上限立即 os._exit(1)。
#   - 进程内无法用 RLIMIT_AS（macOS 不允许设低于当前已用地址空间）
#   - 仅在本文件（tests/conftest.py）内由 pytest 加载时生效，生产/后端进程
#     不加载本文件，绝不会触发
#
# 环境变量: CORTEX_TEST_MEM_LIMIT_MB  上限 MB；**必须显式设置才启用**
#   （默认 0=关闭；CI 显式设 4096；设为 0 或未设一律不启动看门狗）
# ---------------------------------------------------------------------------
_MEM_LIMIT_MB = int(os.environ.get("CORTEX_TEST_MEM_LIMIT_MB", "0") or "0")


def _mem_watchdog() -> None:
    """看门狗线程：RSS 超上限立即终止进程（仅测试进程，见上方说明）。"""
    import time
    try:
        import psutil
        _get_rss = lambda: psutil.Process().memory_info().rss  # bytes，跨平台统一
    except ImportError:
        import resource
        # 退化：ru_maxrss 为峰值（Linux=KB，macOS=bytes），只能做峰值兜底
        def _get_rss():
            v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return v * 1024 if sys.platform != "darwin" else v
    limit = _MEM_LIMIT_MB * 1024 * 1024
    while True:
        try:
            if _get_rss() > limit:
                print(f"\n[TEST-MEM-WATCHDOG] 测试进程内存超限 {_MEM_LIMIT_MB}MB，自动终止",
                      file=sys.stderr, flush=True)
                os._exit(1)
        except Exception:
            pass
        time.sleep(0.5)


@pytest.fixture(scope="session", autouse=True)
def _memory_limit():
    if _MEM_LIMIT_MB <= 0:
        yield
        return
    import threading
    threading.Thread(target=_mem_watchdog, daemon=True).start()
    print(f"\n[TEST-MEM-WATCHDOG] 测试进程内存上限: {_MEM_LIMIT_MB} MB（超限自动终止）",
          file=sys.stderr, flush=True)
    yield


@pytest.fixture(scope="session", autouse=True)
def block_real_native_libs():
    """unit 测试不真实加载重量级原生库。

    torch / transformers / onnxruntime / mlx-vlm 初始化会创建 OpenMP/线程池线程，
    与已加载的 faiss 等原生库双 OpenMP 冲突（见 docs/ERRORS_AND_FIXES.md §27），
    表现为测试进程随机 GIL 死锁（sample 确认：主线程卡 take_gil，libtorch_cpu
    线程持 GIL 死锁）。unit 测试这些库一律用假模块（sys.modules 注入）测分支。
    """
    for mod in (
        "torch",
        "transformers",
        "sentence_transformers",
        "mlx_vlm",
        "paddleocr",
        "rapidocr_onnxruntime",
    ):
        sys.modules[mod] = None
    yield
    for mod in (
        "torch",
        "transformers",
        "sentence_transformers",
        "mlx_vlm",
        "paddleocr",
        "rapidocr_onnxruntime",
    ):
        sys.modules.pop(mod, None)


@pytest.fixture(scope="session", autouse=True)
def mock_pyqt6_if_missing():
    """PyQt6 不可用（CI/无桌面依赖环境）时注入假模块，使 frontend.pet_widget 可导入。

    pet_widget 是 Qt 桌宠窗口，顶层 import PyQt6；测试只读其 BACKEND_URL（端口发现逻辑），
    不实例化 Qt 控件——因此环境缺 PyQt6 时用 MagicMock 模块树顶替即可。
    """
    try:
        import PyQt6  # noqa: F401
        yield
        return
    except ImportError:
        pass

    from unittest.mock import MagicMock

    root = MagicMock()
    sys.modules["PyQt6"] = root
    _SUBS = ("QtCore", "QtGui", "QtWebChannel", "QtWebEngineCore",
             "QtWebEngineWidgets", "QtWidgets")
    for sub in _SUBS:
        sys.modules[f"PyQt6.{sub}"] = MagicMock()
    yield
    sys.modules.pop("PyQt6", None)
    for sub in _SUBS:
        sys.modules.pop(f"PyQt6.{sub}", None)


@pytest.fixture(scope="session", autouse=True)
def register_capabilities():
    """注册业务能力到 infra 端口。

    生产环境由 api/main → bootstrap 注册；测试会话开始时统一注册，
    否则工具层 get_capability() 返回 None 会走降级分支。
    测试需要 mock 具体能力时：unregister_capability(name) + register_capability(name, fake)。
    """
    from bootstrap import register_business_capabilities
    register_business_capabilities()
    yield
    from infra.tool_manager.service_registry import _capabilities
    _capabilities.clear()


@pytest.fixture
def settings():
    """提供测试用 Settings 实例"""
    from config.settings import Settings
    return Settings(_env_file=None)


@pytest.fixture
def mock_model_runner():
    """模拟 ModelRunner"""
    from unittest.mock import MagicMock, AsyncMock
    runner = MagicMock()
    runner.is_running = False
    runner.model_id = "test-model"
    runner.config = MagicMock()
    runner.config.model_name = "test"
    runner.config.api_key = "test-key"
    runner.process_input = AsyncMock(return_value="test response")
    return runner


@pytest.fixture
def blackboard():
    """提供测试用 CognitiveBlackboard 实例"""
    from modules.thinking.cognition.blackboard import CognitiveBlackboard
    return CognitiveBlackboard(max_entries=100)


@pytest.fixture
def app_state():
    """提供测试用 AppState 实例"""
    from cli_tui.state import AppState
    return AppState(api_url="http://localhost:8080")


@pytest.fixture
def memory_manager():
    """提供测试用 MemoryManager 实例（新系统暂存根）"""
    return None


@pytest.fixture(autouse=True)
def _stop_background_sources():
    """每个测试后 stop 所有遗留的屏幕监控/差异源实例。

    修复：ScreenMonitorSource / ScreenDiffSource 的 daemon 后台线程在测试未显式
    stop 时会继续运行（无限调用被 mock 的 reader），干扰 pytest capture 导致偶发挂起。
    通过类级 weakref 注册表统一清理。
    """
    yield
    for mod, cls_name, stop_method in (
        ("modules.perception.difference.sources.screen_monitor_source", "ScreenMonitorSource", "stop"),
        ("modules.perception.difference.sources.mcp_screen_source", "ScreenDiffSource", "stop"),
        ("modules.perception.setup", "PerceptionSystem", "stop"),
        ("modules.perception.events.bus", "PerceptionEventBus", "shutdown"),
        ("modules.perception.detectors.voice_detector", "VoiceDetector", "stop"),
        ("modules.perception.detectors.hotkey_voice_detector", "HotkeyVoiceDetector", "stop"),
        ("modules.perception.detectors.ocr_detector", "OCRDetector", "stop"),
    ):
        try:
            module = __import__(mod, fromlist=["x"])
            cls = getattr(module, cls_name)
            for inst in list(getattr(cls, "_all_instances", ())):
                try:
                    getattr(inst, stop_method)()
                except Exception:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 内存泄漏检测 —— 默认开启，覆盖全项目（unit + integration，经 tests/conftest.py）
#
# 两层检测：
#   1. 增长趋势（轻量）：pytest_runtest_teardown 每 LEAK_INTERVAL 个测试采样
#      sys.getallocatedobjects()，会话结束对后半段采样点拟合斜率；
#      若存活对象数随测试数持续线性增长 → 跨测试泄漏。
#   2. 类型定位（pympler）：会话前后 SummaryTracker 快照 diff，
#      列出会话内创建但未释放的对象类型，供人工确认泄漏点。
#
# 开销：getallocatedobjects 为 O(1)；pympler 仅在会话结束遍历一次对象表。
# 环境变量可调：LEAK_INTERVAL（采样间隔，默认 50）、LEAK_REPORT（0 关闭报告）。
# ---------------------------------------------------------------------------

LEAK_INTERVAL = int(os.environ.get("LEAK_INTERVAL", "100"))
LEAK_REPORT = os.environ.get("LEAK_REPORT", "1") == "1"

# 每测试新增存活字节数超过此阈值判定为泄漏嫌疑（保守，避免误报；单位 KiB/测试）
LEAK_RATE_THRESHOLD = int(os.environ.get("LEAK_RATE_THRESHOLD", "256"))

_LEAK_CTX = {"count": 0, "samples": [], "tracker": None}


def _leak_sample_mem_kib() -> int:
    """采样当前存活对象总字节数（pympler.muppy，含 bytes/bytearray 等原始内存）。"""
    from pympler import muppy
    return muppy.get_size(muppy.get_objects()) // 1024


def pytest_runtest_teardown(item, nextitem):
    """每 LEAK_INTERVAL 个测试采样存活内存 KiB（真实字节，含 bytes/numpy 内容）。

    开销：muppy 遍历存活对象约 0.1-1s/次（随进程对象数），默认每 100 测试一次。
    """
    _LEAK_CTX["count"] += 1
    if _LEAK_CTX["count"] % LEAK_INTERVAL == 0:
        try:
            _LEAK_CTX["samples"].append(
                (_LEAK_CTX["count"], _leak_sample_mem_kib(), getattr(item, "nodeid", "")),
            )
        except ImportError:
            pass  # pympler 未安装：跳过采样（报告会提示）
        except Exception:
            pass


def pytest_sessionstart(session):
    """会话开始：构造 pympler baseline（用于会话结束的类型 diff）。"""
    try:
        import gc
        from pympler import tracker
        gc.collect()
        _LEAK_CTX["tracker"] = tracker.SummaryTracker()
    except ImportError:
        _LEAK_CTX["tracker"] = None  # pympler 未安装：无法检测，报告会提示


def pytest_sessionfinish(session, exitstatus):
    """会话结束（capture 已释放）：输出泄漏检测报告 + 模块覆盖清单。"""
    try:
        import gc as _gc
        _gc.collect()
        _report_leak_detector(_LEAK_CTX["tracker"])
    except Exception:
        pass
    try:
        _report_module_coverage()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 生产模块执行覆盖清单 —— 保证"所有模块都在泄漏检测覆盖范围内"
#
# 泄漏检测是 session 级聚合趋势，只对"被测试执行过"的模块有效。
# 若某生产模块从未被任何测试 import/执行，它就不在检测范围。
# 本清单在会话结束对比"全部生产模块"与"实际被执行(import)的模块"，
# 明确报告哪些模块无测试触及 —— 这些模块需要补测试后才能纳入检测覆盖。
#
# 环境变量:
#   CORTEX_TEST_MODULE_UNCOVERED_MAX  允许未覆盖的生产模块数（默认 10，
#      超限在报告中标 ⚠；设 -1 关闭检查）
# ---------------------------------------------------------------------------
_PRODUCTION_DIRS = ("modules", "infra", "utils", "config", "api", "cortex", "frontend")
_MODULE_UNCOVERED_MAX = int(os.environ.get("CORTEX_TEST_MODULE_UNCOVERED_MAX", "10"))

# frontend 下依赖 GUI 的启动器：无显示环境（CI）无法测试，清单豁免（前端代理层 server.py
# 与 pet_widget.py 纳入覆盖，Qt 启动器 main/pet_launch 豁免）
_FRONTEND_EXCLUDE = ("frontend/main.py", "frontend/pet_launch.py")


def _all_production_modules() -> set:
    """扫描生产目录下的全部 .py 模块路径（不含 __init__）。"""
    found = set()
    for base in _PRODUCTION_DIRS:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".py") and f != "__init__.py":
                    rel = os.path.relpath(os.path.join(root, f))
                    if rel in _FRONTEND_EXCLUDE:
                        continue
                    found.add(rel[:-3].replace(os.sep, "."))
    return found


def _report_module_coverage() -> None:
    """报告未被测试执行（import）的生产模块 —— 这些不在泄漏检测覆盖范围。"""
    if _MODULE_UNCOVERED_MAX < 0:
        return
    all_mods = _all_production_modules()
    executed = {m for m in sys.modules if m in all_mods}
    uncovered = sorted(all_mods - executed)

    print("\n" + "=" * 60)
    print("[MODULE-COVERAGE] 生产模块执行覆盖清单")
    print(f"  生产模块总数: {len(all_mods)}  测试已执行: {len(executed)}  "
          f"未执行: {len(uncovered)}")
    if uncovered:
        print(f"  —— 以下 {len(uncovered)} 个生产模块无测试触及，不在泄漏检测覆盖范围：")
        for m in uncovered[:20]:
            print(f"    ⚠ {m}")
        if len(uncovered) > 20:
            print(f"    … 等共 {len(uncovered)} 个")
        if len(uncovered) > _MODULE_UNCOVERED_MAX:
            print(f"  ⚠ 未覆盖模块数 {len(uncovered)} 超过上限 "
                  f"{_MODULE_UNCOVERED_MAX}，请为这些模块补测试以纳入泄漏检测")
    else:
        print("  ✓ 所有生产模块均被测试执行，全部在泄漏检测覆盖范围内")
    print("=" * 60)


def _report_leak_detector(pympler_tracker) -> None:
    if not LEAK_REPORT:
        return
    print("\n" + "=" * 60)
    print("[LEAK-DETECT] 内存泄漏检测报告")
    print("=" * 60)

    # 1) 增长趋势判定（基于 pympler.muppy 真实字节数）
    samples = _LEAK_CTX["samples"]
    if len(samples) >= 3:
        half = samples[len(samples) // 2:]
        delta_kib = half[-1][1] - half[0][1]
        delta_tests = max(1, half[-1][0] - half[0][0])
        rate = delta_kib / delta_tests  # KiB/测试
        print(f"  [趋势] 采样点(测试数, 存活KiB, 节点):")
        for n, kib, nid in samples:
            print(f"    {n:5d}  {kib:8d} KiB  {nid}")
        print(f"  [趋势] 后 {len(half)} 个采样点: {half[0][1]} → {half[-1][1]} KiB "
              f"(每测试新增 {rate:.1f} KiB)")
        if rate > LEAK_RATE_THRESHOLD:
            print(f"  ⚠ 疑似内存泄漏: 每测试持续新增 {rate:.1f} KiB，"
                  f"超过阈值 {LEAK_RATE_THRESHOLD} KiB/测试")
            print(f"    → 用 scripts/leak_check.py 缩小范围定位泄漏点")
        else:
            print(f"  ✓ 内存稳定（每测试 {rate:.1f} KiB，阈值 {LEAK_RATE_THRESHOLD} KiB/测试）")
    else:
        print(f"  [趋势] 测试样本不足（{len(samples)} 个采样点 < 3，跳过增长判定）")

    # 2) pympler 类型定位
    if pympler_tracker is None:
        print("  [警告] pympler 未安装，无法做类型 diff；请 pip install pympler")
    else:
        print("  [pympler] 会话内创建但未释放的对象类型 top（随测试数增长的 = 泄漏候选）:")
        try:
            pympler_tracker.print_diff()
        except Exception as e:
            print(f"  pympler 分析失败: {e}")
    print("=" * 60)



