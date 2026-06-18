"""
MCP 屏幕差异源 — 通过 MCP stdio 协议连接 screen_diff_server

架构:
  ScreenDiffSource (daemon thread)
      │ 通过 stdin/stdout 发送 MCP JSON-RPC 请求
      ▼
  screen_diff_server (独立子进程)
      │ 截图 → 帧差 → 返回变化数据
      ▼
  DifferenceDetector.ingest("screen", "changed", ...)

工作方式:
  1. 启动时 spawn screen_diff_server 作为持久子进程
  2. daemon 线程以固定间隔向子进程发送 check_screen_changes 请求
  3. 将变化结果通过 DifferenceDetector.ingest() 注入差异检测系统
  4. 子进程退出时自动重启
"""
import json
import os
import select
import subprocess
import sys
import threading
import time
from typing import Optional

from modules.perception.events.bus import get_event_bus
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("mcp_screen_source")

# 屏幕差异检测的默认配置
_DEFAULT_INTERVAL = 1.0          # 检测间隔（秒），和 ExistentialHeartbeat 一致
_CHANGE_THRESHOLD = 0.01         # 最小变化面积比例 (1%)
_HIGH_CHANGE_THRESHOLD = 0.15    # 高变化阈值 (15%) 触发高强度差异
_IDLE_RESET_INTERVAL = 30        # 连续无变化超过 N 秒后重置强度
_PERCEPTION_EVENT_IDLE_INTERVAL = 2.0  # 事件发布最小间隔（秒），避免刷屏


class ScreenDiffSource:
    """MCP 屏幕差异源

    后台线程持续检测屏幕像素变化，将结果注入 DifferenceDetector。
    """

    def __init__(self, server_script: str = "", interval: float = _DEFAULT_INTERVAL):
        self._interval = interval
        self._change_threshold = settings.SCREEN_DIFF_CHANGE_THRESHOLD
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._event_bus = None  # 延迟获取，避免循环导入

        # 状态跟踪
        self._last_change_ratio = 0.0
        self._consecutive_no_change = 0
        self._total_changes = 0
        self._scan_count = 0
        self._last_activity_time = 0.0
        self._proc_restarts = 0
        self._last_event_publish_time = 0.0

        # 定位 server 脚本
        self._server_script = server_script or self._find_server_script()

    @staticmethod
    def _find_server_script() -> str:
        """自动定位 screen_diff_server.py 的路径"""
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        candidate = os.path.join(
            project_root, "infra", "mcp", "servers", "screen_diff_server.py"
        )
        if os.path.exists(candidate):
            return candidate
        # 兜底
        return "infra/mcp/servers/screen_diff_server.py"

    # ── 生命周期 ──

    def start(self) -> None:
        """启动后台检测线程"""
        if self._running:
            return
        self._running = True
        self._last_activity_time = time.time()

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="mcp-screen-diff",
        )
        self._thread.start()
        logger.info(f"屏幕差异源已启动 (interval={self._interval}s, server={self._server_script})")

    def stop(self) -> None:
        """停止检测线程并关闭子进程"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._close_process()
        logger.info(f"屏幕差异源已停止 (已检测 {self._total_changes} 次变化)")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def interval(self) -> float:
        """检测间隔（秒）"""
        return self._interval

    @interval.setter
    def interval(self, value: float) -> None:
        """设置检测间隔（秒），有效范围 0.1~30"""
        self._interval = max(0.1, min(30.0, value))

    @property
    def change_threshold(self) -> float:
        return self._change_threshold

    @change_threshold.setter
    def change_threshold(self, value: float) -> None:
        self._change_threshold = max(0.0, min(1.0, value))

    def get_stats(self) -> dict:
        return {
            "running": self._running,
            "scan_count": self._scan_count,
            "total_changes": self._total_changes,
            "last_change_ratio": round(self._last_change_ratio, 4),
            "consecutive_no_change": self._consecutive_no_change,
            "proc_restarts": self._proc_restarts,
        }

    # ── 子进程管理 ──

    def _ensure_process(self) -> bool:
        """确保子进程在运行"""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return True

            # 关闭旧进程
            self._close_process()

            # 启动新进程
            try:
                script = self._server_script
                if not os.path.isabs(script):
                    # 从项目根目录查找
                    project_root = os.path.dirname(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    )
                    script = os.path.join(project_root, script)

                if not os.path.exists(script):
                    logger.warning(f"screen_diff_server.py 未找到: {script}")
                    return False

                self._proc = subprocess.Popen(
                    [sys.executable, script],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                # 发送 initialize 请求
                init_req = {
                    "jsonrpc": "2.0",
                    "id": "init_1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "cortex-screen-diff", "version": "1.0"},
                    },
                }
                self._send_request(init_req)
                # 等待 initialize 响应
                resp = self._read_response()
                if resp and "result" in resp:
                    # 发送 initialized 通知
                    self._send_request({
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    })
                    self._proc_restarts += 1
                    logger.info(f"screen_diff_server 已启动 (重启 #{self._proc_restarts})")
                    return True
                else:
                    logger.warning(f"screen_diff_server 初始化失败: {resp}")
                    self._close_process()
                    return False

            except Exception as e:
                logger.error(f"启动 screen_diff_server 失败: {e}")
                self._close_process()
                return False

    def _close_process(self):
        """关闭子进程"""
        with self._lock:
            if self._proc:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None

    def _send_request(self, req: dict):
        """发送 JSON-RPC 请求到子进程 stdin"""
        with self._lock:
            if self._proc and self._proc.stdin:
                line = json.dumps(req, ensure_ascii=False)
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()

    _req_counter: int = 0

    def _call_mcp_tool(self, tool_name: str, arguments: dict = None) -> Optional[dict]:
        """调用 MCP 工具的简化封装"""
        ScreenDiffSource._req_counter += 1
        req = {
            "jsonrpc": "2.0",
            "id": f"tool_{ScreenDiffSource._req_counter}",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        self._send_request(req)
        resp = self._read_response(timeout=10.0)
        if resp and "result" in resp:
            content = resp["result"].get("content", [])
            for item in content:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"text": text}
        return None

    def capture(self) -> Optional[dict]:
        """
        公共 API：手动执行一次屏幕帧差检测。

        返回变化数据 dict（changed, change_ratio, regions, width, height 等），
        或 None 表示子进程不可用。
        """
        if not self._ensure_process():
            return None
        return self._call_mcp_tool("check_screen_changes")

    def capture_screenshot(self) -> Optional[dict]:
        """
        公共 API：手动截取当前屏幕并返回 base64 编码的图像。
        """
        if not self._ensure_process():
            return None
        return self._call_mcp_tool("capture_screenshot")

    def _read_response(self, timeout: float = 5.0) -> Optional[dict]:
        """从子进程 stdout 读取一行 JSON 响应"""
        with self._lock:
            if not self._proc or not self._proc.stdout:
                return None
            try:
                if select.select([self._proc.stdout], [], [], timeout)[0]:
                    line = self._proc.stdout.readline()
                    if line:
                        return json.loads(line.strip())
            except Exception:
                pass
        return None

    # ── 检测主循环 ──

    def _run_loop(self):
        """后台检测主循环"""
        # 先启动子进程
        if not self._ensure_process():
            logger.error("无法启动 screen_diff_server，屏幕差异源不可用")
            self._running = False
            return

        while self._running:
            try:
                self._check_once()
            except Exception as e:
                logger.debug(f"屏幕检测异常 (非致命): {e}")
                # 子进程可能挂了，尝试重启
                if not self._ensure_process():
                    time.sleep(self._interval * 3)

            # 等待下一个检测间隔（支持中途停止）
            for _ in range(int(self._interval * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    def _check_once(self):
        """执行一次检测"""
        if not self._ensure_process():
            return

        data = self._call_mcp_tool("check_screen_changes")
        if not data:
            return

        self._scan_count += 1
        changed = data.get("changed", False)
        change_ratio = data.get("change_ratio", 0.0)
        regions = data.get("regions", [])

        if changed:
            # 计算 urgency: 归一化到 0-1
            urgency = min(change_ratio / 0.5, 1.0)

            # 注入 DifferenceDetector
            try:
                from modules.perception.difference import get_detector
                detector = get_detector()
                detector.ingest(
                    target_type="screen",
                    change_type="changed",
                    target="display",
                    details={
                        "change_ratio": change_ratio,
                        "regions": regions,
                        "width": data.get("width", 0),
                        "height": data.get("height", 0),
                    },
                    urgency=urgency,
                )
            except Exception as e:
                logger.debug(f"注入差异失败 (非致命): {e}")

            self._last_change_ratio = change_ratio
            self._consecutive_no_change = 0
            self._total_changes += 1
            self._last_activity_time = time.time()
            # 发布事件到 PerceptionEventBus（限频 2s）
            self._publish_screen_diff_event(change_ratio, regions, data)

            if change_ratio >= _HIGH_CHANGE_THRESHOLD:
                logger.info(f"屏幕大幅变化: {change_ratio:.1%} ({len(regions)} 区域)")
        else:
            self._consecutive_no_change += 1
            self._last_change_ratio = 0.0

    def _publish_screen_diff_event(self, change_ratio: float,
                                    regions: list, data: dict) -> None:
        """发布屏幕差异事件到事件总线（带限频，避免刷屏）"""
        now = time.time()
        if now - self._last_event_publish_time < _PERCEPTION_EVENT_IDLE_INTERVAL:
            return
        try:
            if self._event_bus is None:
                self._event_bus = get_event_bus()
            event = PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_DIFF,
                source="screen_diff_mcp",
                importance=min(change_ratio * 2, 1.0),
                payload={
                    "change_ratio": round(change_ratio, 4),
                    "changed_regions": regions,
                    "width": data.get("width", 0),
                    "height": data.get("height", 0),
                },
            )
            self._event_bus.publish(event)
            self._last_event_publish_time = now
        except Exception as e:
            logger.debug(f"发布屏幕差异事件失败 (非致命): {e}")


# 全局实例
_instance: Optional[ScreenDiffSource] = None
_instance_lock = threading.Lock()


def get_screen_diff_source() -> ScreenDiffSource:
    """获取全局 ScreenDiffSource 单例（线程安全）"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ScreenDiffSource()
    return _instance
