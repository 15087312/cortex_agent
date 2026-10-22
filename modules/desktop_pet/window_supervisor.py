"""桌宠窗口进程监管 — 后端统一拉起/停止/看护 Qt 桌宠窗口进程。

背景：桌宠窗口（frontend/pet_launch.py）此前由 Qt 主窗口直接 Popen 拉起，
存在三个问题：崩溃后无人重启、Web 页看不到进程状态、多处拉起会出双窗口。
本监管器收编所有权，Qt 主窗口改为调后端接口（见 frontend/main.py）。

- 桌宠进程本身是纯展示客户端（透明 Qt 窗口 + WebEngine 渲染后端下发的
  live2d 页面）：无模型、无记忆、无状态。杀掉重开不丢任何东西，
  后端 PetEngine 照常思考（气泡经轮询/SSE 照常送达）。
- 后端退出：登记进 cortex.process_registry 随 lifespan 收编；桌宠自身的
  backend 绑定 watchdog（pid 文件）兜底。
- 后端重启：桌宠凭 pid 文件绑定跟随新后端（cortex/watchdog.py），
  新后端启动后经 PID 文件收编（adopt），窗口不闪断。

线程安全：start/stop/status/monitor 共用一把锁。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from utils.logger import setup_logger

logger = setup_logger("pet_window_supervisor")

_PET_SCRIPT = "pet_launch.py"
_MONITOR_INTERVAL = 5.0
_STOP_TIMEOUT = 3.0
# 连续异常死亡超过上限则放弃自动重启（防无显示器等必死环境空转拉起）
_MAX_DEATH_STREAK = 5
_DEATH_WINDOW = 600.0


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pid_file() -> Path:
    return Path.home() / ".cortex" / "pet_window_pid.json"


def _log_file() -> Path:
    return Path.home() / ".cortex" / "pet_window.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, OverflowError):
        return False


def _pid_is_pet(pid: int) -> bool:
    """校验 PID 确为桌宠进程（防 PID 复用误杀），失败时保守返回 False。"""
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        return _PET_SCRIPT in (r.stdout or "")
    except Exception:
        return False


def _terminate_pid(pid: int, timeout: float = _STOP_TIMEOUT) -> bool:
    """SIGTERM → 等待 → SIGKILL，返回是否已死。"""
    import signal as _signal
    try:
        os.kill(pid, _signal.SIGTERM)
    except OSError:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, _signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.3)
    return not _pid_alive(pid)


class PetWindowSupervisor:
    """桌宠窗口进程监管器（单例，懒启动监控线程）。"""

    _instance: Optional["PetWindowSupervisor"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None  # 本监管器拉起的进程
        self._adopted_pid: Optional[int] = None         # 收编的外部进程（无 Popen 句柄）
        self._expected = False      # 用户期望运行中（stop() 置 False，崩溃不改）
        self._started_at: float = 0.0
        self._restarts = 0
        self._death_streak = 0
        self._last_death: float = 0.0
        self._last_exit_code: Optional[int] = None
        self._gave_up = False       # 连续崩溃超限，停止自动重启
        self._monitor_started = False
        self._monitor_stop = threading.Event()

    @classmethod
    def get_instance(cls) -> "PetWindowSupervisor":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 查询 ──

    def _current_pid(self) -> Optional[int]:
        if self._proc is not None:
            return self._proc.pid
        return self._adopted_pid

    def _alive_locked(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        if self._adopted_pid is not None:
            return _pid_alive(self._adopted_pid)
        return False

    def status(self) -> Dict[str, Any]:
        """进程状态（每次调用顺带收编/清理，保持自洽）。"""
        with self._lock:
            self._adopt_if_needed_locked()
            pid = self._current_pid()
            running = self._alive_locked()
            if not running:
                # 进程没了：清理句柄，退出码留档（下次 start 重置）
                if self._proc is not None:
                    try:
                        self._last_exit_code = self._proc.poll()
                    except Exception:
                        pass
                    self._proc = None
                self._adopted_pid = None
                pid = None
            return {
                "running": running,
                "pid": pid,
                "expected": self._expected,
                "restarts": self._restarts,
                "exit_code": self._last_exit_code,
                "started_at": self._started_at or None,
                "gave_up_auto_restart": self._gave_up,
                "log_path": str(_log_file()),
            }

    # ── 启动 / 停止 ──

    def start(self) -> Dict[str, Any]:
        """确保桌宠窗口进程运行（已在跑则直接返回）。"""
        if os.environ.get("CORTEX_DISABLE_PET", "0") == "1":
            return {"ok": False, "reason": "CORTEX_DISABLE_PET=1，桌宠已禁用"}
        self._ensure_monitor()
        with self._lock:
            self._expected = True
            self._gave_up = False
            self._adopt_if_needed_locked()
            if self._alive_locked():
                return {"ok": True, "already_running": True, "pid": self._current_pid()}
            proc = self._spawn_locked()
            if proc is None:
                self._expected = False
                return {"ok": False, "reason": "拉起桌宠进程失败，见 pet_window.log"}
            self._proc = proc
            self._adopted_pid = None
            self._started_at = time.time()
            self._last_exit_code = None
            self._write_pid_file_locked(proc.pid)
            try:
                from cortex.process_registry import register
                register("pet_window", proc)
            except Exception:
                pass
            logger.info(f"[PetWindow] 已拉起桌宠窗口进程 pid={proc.pid}")
            return {"ok": True, "already_running": False, "pid": proc.pid}

    def stop(self) -> Dict[str, Any]:
        """停止桌宠窗口进程（用户主动关闭，不再自动重启）。"""
        with self._lock:
            self._expected = False
            pid = self._current_pid()
            proc, self._proc = self._proc, None
            self._adopted_pid = None
            try:
                from cortex.process_registry import unregister
                unregister("pet_window")
            except Exception:
                pass
            self._clear_pid_file_locked(pid)
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=_STOP_TIMEOUT)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                    self._last_exit_code = proc.poll()
                except Exception as e:
                    logger.warning(f"[PetWindow] 停止进程异常: {e}")
            elif pid is not None:
                _terminate_pid(pid)
                self._last_exit_code = None
            else:
                return {"ok": True, "already_stopped": True}
            logger.info(f"[PetWindow] 已停止桌宠窗口进程 pid={pid}")
            return {"ok": True, "pid": pid}

    # ── 内部 ──

    def _adopt_if_needed_locked(self) -> None:
        """无句柄时尝试经 PID 文件收编存活的老窗口（后端重启/外部拉起）。"""
        if self._proc is not None or self._adopted_pid is not None:
            return
        try:
            data = json.loads(_pid_file().read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except Exception:
            return
        if pid > 1 and _pid_alive(pid) and _pid_is_pet(pid):
            self._adopted_pid = pid
            if not self._started_at:
                try:
                    self._started_at = float(data.get("started_at", 0) or 0)
                except Exception:
                    self._started_at = 0.0
            logger.info(f"[PetWindow] 收编存活桌宠窗口 pid={pid}")
        else:
            #  stale 文件：进程已死或 PID 复用成别的进程，只清文件不动进程
            try:
                _pid_file().unlink()
            except Exception:
                pass

    def _spawn_locked(self) -> Optional[subprocess.Popen]:
        script = _project_root() / "frontend" / _PET_SCRIPT
        if not script.is_file():
            logger.error(f"[PetWindow] 桌宠脚本不存在: {script}")
            return None
        env = dict(os.environ)
        # 父进程看护交给监管器（本进程），禁用 pet 自身的 getppid 看护，
        # 避免后端重启（父进程更换）时桌宠被误杀；后端绑定 watchdog 保留，
        # 后端彻底退出时桌宠仍会跟随退出。
        env["CORTEX_PET_NO_WATCHDOG"] = "1"
        log_path = _log_file()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            logf = open(log_path, "w", encoding="utf-8")
        except Exception as e:
            logger.error(f"[PetWindow] 日志文件打不开: {e}")
            return None
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(_project_root()),
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
            )
            return proc
        except Exception as e:
            logger.error(f"[PetWindow] Popen 失败: {e}")
            try:
                logf.close()
            except Exception:
                pass
            return None
        finally:
            # Popen 成功后父进程侧关闭文件句柄（子进程持有自己的 dup）
            try:
                logf.close()
            except Exception:
                pass

    def _write_pid_file_locked(self, pid: int) -> None:
        try:
            p = _pid_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps({"pid": pid, "started_at": self._started_at}),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[PetWindow] PID 文件写入失败: {e}")

    def _clear_pid_file_locked(self, pid: Optional[int]) -> None:
        try:
            p = _pid_file()
            if not p.exists():
                return
            if pid is not None:
                try:
                    if int(json.loads(p.read_text(encoding="utf-8")).get("pid", 0)) != pid:
                        return  # 不是我们管理的 PID，不动
                except Exception:
                    pass
            p.unlink()
        except Exception:
            pass

    # ── 崩溃看护 ──

    def _ensure_monitor(self) -> None:
        with self._lock:
            if self._monitor_started:
                return
            self._monitor_started = True
        t = threading.Thread(target=self._monitor_loop, daemon=True, name="pet-window-monitor")
        t.start()

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.is_set():
            time.sleep(_MONITOR_INTERVAL)
            try:
                self._monitor_tick()
            except Exception as e:
                logger.debug(f"[PetWindow] 监控 tick 异常 (非致命): {e}")

    def _monitor_tick(self) -> None:
        with self._lock:
            if not self._expected or self._gave_up:
                return
            self._adopt_if_needed_locked()
            if self._alive_locked():
                return
            # 意外死亡：记录并重启（退避 + 上限）
            now = time.time()
            if now - self._last_death > _DEATH_WINDOW:
                self._death_streak = 0
            self._death_streak += 1
            self._last_death = now
            if self._proc is not None:
                try:
                    self._last_exit_code = self._proc.poll()
                except Exception:
                    pass
                self._proc = None
            self._adopted_pid = None
            if self._death_streak > _MAX_DEATH_STREAK:
                self._gave_up = True
                logger.error(
                    f"[PetWindow] 桌宠窗口连续崩溃 {self._death_streak} 次，"
                    f"停止自动重启（exit={self._last_exit_code}，见 pet_window.log），"
                    f"可手动点「打开桌面宠物」重试"
                )
                return
            logger.warning(
                f"[PetWindow] 桌宠窗口意外退出 (exit={self._last_exit_code})，"
                f"正在重启（第 {self._restarts + 1} 次）"
            )
            proc = self._spawn_locked()
            if proc is None:
                return
            self._proc = proc
            self._started_at = time.time()
            self._last_exit_code = None
            self._restarts += 1
            self._write_pid_file_locked(proc.pid)
            try:
                from cortex.process_registry import register
                register("pet_window", proc)
            except Exception:
                pass

    def shutdown(self) -> None:
        """后端退出时调用：停监控 + 终止窗口（幂等）。"""
        self._monitor_stop.set()
        self.stop()


def get_pet_window_supervisor() -> PetWindowSupervisor:
    return PetWindowSupervisor.get_instance()
