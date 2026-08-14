"""
屏幕内容源 — 持久子进程调用 screen_monitor_server 提取 OCR + 视觉分析

使用 MCP JSON-RPC 持久子进程模式（与 ScreenDiffSource 一致）。
首次初始化时加载 RapidOCR 模型 (~10s)，后续调用 <1s。
发布 SCREEN_OCR / SCREEN_UI 事件到事件总线。
"""
import json
import os
import subprocess
import sys
import threading
import time
import weakref
from queue import Queue, Empty
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("screen_monitor_source")

DEFAULT_INTERVAL = 3.0
_RESPONSE_TIMEOUT = 30.0


class ScreenMonitorSource:
    """屏幕内容源 — 持久 MCP 子进程 + 后台线程"""

    source_type = "screen_monitor"

    # 活跃实例追踪（weakref）：测试/退出时统一 stop，避免后台线程遗留
    _all_instances = weakref.WeakSet()

    def __init__(self, server_script: str = None, interval: float = None):
        self._interval = interval or DEFAULT_INTERVAL
        ScreenMonitorSource._all_instances.add(self)
        self._server_script = server_script or self._find_server_script()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_text_lines: list = []
        # 持久子进程状态
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._reader_running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._resp_queue: Queue = Queue()
        self._proc_restarts = 0
        self._consecutive_timeouts = 0  # 连续超时计数：>=2 才强制重启，避免误杀健康进程

    @staticmethod
    def _find_server_script() -> str:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        )
        return os.path.join(project_root, "infra", "mcp", "servers", "screen_monitor_server.py")

    # ── 生命周期 ──

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        # 启动时立即建立子进程（预热 RapidOCR 模型）
        self._ensure_process()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="screen-monitor")
        self._thread.start()
        logger.info(f"屏幕内容源已启动 (interval={self._interval}s)")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._close_process()
        ScreenMonitorSource._all_instances.discard(self)
        logger.info("屏幕内容源已停止")

    # ── 持久子进程管理 ──

    def _ensure_process(self) -> bool:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return True
            self._close_process()
            try:
                script = self._server_script
                if not os.path.exists(script):
                    logger.warning(f"screen_monitor_server.py 未找到: {script}")
                    return False
                self._proc = subprocess.Popen(
                    [sys.executable, script],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                )
                self._reader_running = True
                self._reader_thread = threading.Thread(
                    target=self._read_stdout_loop, daemon=True, name="screen-monitor-reader"
                )
                self._reader_thread.start()

                init_req = {"jsonrpc": "2.0", "id": "init_1", "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                       "clientInfo": {"name": "cortex-screen-monitor", "version": "1.0"}}}
                self._send_request(init_req)
                resp = self._read_response(timeout=180.0)
                if resp and "result" in resp:
                    self._send_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
                    self._proc_restarts += 1
                    logger.info(f"screen_monitor_server 已启动 (重启 #{self._proc_restarts})")
                    return True
                else:
                    logger.warning(f"screen_monitor_server 初始化失败: {resp}")
                    self._close_process()
                    return False
            except Exception as e:
                logger.error(f"screen_monitor_server 启动失败: {e}")
                self._close_process()
                return False

    def _close_process(self):
        self._reader_running = False
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
        # 等待旧 reader 线程退出，避免重启后新旧线程竞争读新进程 stdout
        if self._reader_thread:
            self._reader_thread.join(timeout=3)
            self._reader_thread = None
        while not self._resp_queue.empty():
            try:
                self._resp_queue.get_nowait()
            except Empty:
                break

    def _send_request(self, req: dict):
        with self._lock:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()

    _req_counter: int = 0

    def _call_mcp_tool(self, tool_name: str, arguments: dict = None) -> Optional[dict]:
        ScreenMonitorSource._req_counter += 1
        req = {"jsonrpc": "2.0", "id": f"tool_{ScreenMonitorSource._req_counter}",
               "method": "tools/call", "params": {"name": tool_name, "arguments": arguments or {}}}
        t0 = time.time()
        self._send_request(req)
        resp = self._read_response(timeout=_RESPONSE_TIMEOUT)
        elapsed = time.time() - t0
        if elapsed > 15:
            logger.debug(f"[{tool_name}] 响应 {elapsed:.1f}s (慢)")
        if resp and "result" in resp:
            for item in resp["result"].get("content", []):
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if tool_name == "analyze_ui_elements":
                        return self._parse_ui_elements(text)
                    return {"text": text}
        else:
            # 连续超时达到阈值才强制重启：单次超时可能是慢但健康（如 OCR 对复杂画面），
            # 连续超时说明子进程已卡死（如 OCR/轮廓检测死循环）——kill 后由 _run_loop 下轮 _ensure_process 拉起新进程
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= 2:
                logger.error(f"[{tool_name}] 连续 {self._consecutive_timeouts} 次无响应 (超时 {_RESPONSE_TIMEOUT}s, 实际等待 {elapsed:.1f}s)，强制重启子进程")
                self._close_process()
            else:
                logger.error(f"[{tool_name}] 无响应 (超时 {_RESPONSE_TIMEOUT}s, 实际等待 {elapsed:.1f}s)")
        return None

    def _read_response(self, timeout: float = 5.0) -> Optional[dict]:
        try:
            line = self._resp_queue.get(timeout=timeout)
            return json.loads(line)
        except (Empty, json.JSONDecodeError):
            return None

    def _read_stdout_loop(self):
        while self._reader_running:
            try:
                if self._proc and self._proc.stdout:
                    line = self._proc.stdout.readline()
                    if line:
                        self._resp_queue.put(line.strip())
                    else:
                        break
                else:
                    time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

    # ── 后台循环 ──

    def _run_loop(self):
        while self._running:
            try:
                if not self._ensure_process():
                    time.sleep(self._interval)
                    continue
                result = self._call_mcp_tool("analyze_ui_elements")
                if result and result.get("elements"):
                    self._consecutive_timeouts = 0  # 正常响应后重置连续超时计数
                    self._publish_to_event_bus(result["elements"])
                else:
                    # 有响应但结果为空（如截图失败）也视为正常——只有真正无响应才算超时
                    if result is not None:
                        self._consecutive_timeouts = 0
            except Exception as e:
                logger.debug(f"屏幕内容分析失败: {e}")
            time.sleep(self._interval)

    # ── UI 元素解析 ──

    @staticmethod
    def _parse_ui_elements(raw_text: str) -> dict:
        """解析 analyze_ui_elements 的结构化输出"""
        import re
        elements = []
        for line in raw_text.split("\n"):
            m = re.search(r'\[(\w+)\]\s*"([^"]+)"\s*位置=\(([\d,]+)\)-\(([\d,]+)\)\s*置信度=([\d.]+)', line)
            if m:
                etype, text, pos1, pos2, conf = m.groups()
                x1, y1 = pos1.split(",")
                x2, y2 = pos2.split(",")
                elements.append({
                    "type": etype,
                    "text": text.strip(),
                    "x": int(x1), "y": int(y1),
                    "w": int(x2) - int(x1), "h": int(y2) - int(y1),
                    "confidence": float(conf),
                })
        return {"elements": elements}

    def _filter_and_diff(self, elements: list) -> tuple[list, list]:
        """过滤高置信度元素，对比上次找出新变化"""
        high_conf = [e for e in elements if e["confidence"] >= 0.7 and len(e["text"]) > 1]
        high_conf.sort(key=lambda e: e["confidence"], reverse=True)

        current_texts = {e["text"] for e in high_conf}
        prev_texts = set(self._last_text_lines) if isinstance(self._last_text_lines, list) else set()

        new_elements = [e for e in high_conf if e["text"] not in prev_texts]
        changed_elements = [e for e in high_conf if e["text"] in current_texts]

        self._last_text_lines = list(current_texts)[-100:]
        return new_elements, changed_elements

    # ── 公共方法 ──

    def analyze_ui_elements(self, confidence_threshold: float = 0.3) -> dict:
        """公共 API: 同步分析屏幕 UI 元素（供 LLM 工具调用，非后台过滤）"""
        if not self._ensure_process():
            return {"elements": []}
        result = self._call_mcp_tool("analyze_ui_elements", {"confidence_threshold": confidence_threshold})
        return result if result else {"elements": []}

    def _call_capture_and_analyze(self) -> str:
        """公共 API: 同步获取屏幕分析文本（用于预热/测试）"""
        if not self._ensure_process():
            return ""
        result = self._call_mcp_tool("capture_and_analyze", {"extract_text": True})
        return result.get("text", "") if result else ""

    # ── 事件发布 ──

    def _publish_to_event_bus(self, elements: list):
        if not elements:
            return
        new_elems, _ = self._filter_and_diff(elements)
        if not new_elems:
            return

        # 新变化的文本行（按置信度降序已排序）
        text_lines = [e["text"] for e in new_elems[:15]]
        top_conf = new_elems[:5]
        summary_lines = [f'  {e["text"]} ({e["confidence"]:.0%})' for e in top_conf]

        try:
            from modules.perception.events.bus import get_event_bus
            from modules.perception.events.types import PerceptionEvent, PerceptionEventType

            event_bus = get_event_bus()

            event_bus.publish(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_OCR,
                source="screen_monitor",
                importance=0.5,
                payload={
                    "source_type": "screen_monitor",
                    "text": "\n".join(text_lines),
                    "new_lines": text_lines,
                    "changed_count": len(new_elems),
                    "top_elements": summary_lines,
                    "intensity": min(0.9, len(new_elems) * 0.1),
                },
            ))

            event_bus.publish(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_UI,
                source="screen_monitor",
                importance=0.3,
                payload={
                    "source_type": "screen_monitor",
                    "element_count": len(elements),
                    "high_conf_count": len([e for e in elements if e["confidence"] >= 0.7]),
                    "new_count": len(new_elems),
                    "description": f"屏幕元素: {len(elements)} 个, 高置信度新增 {len(new_elems)} 个",
                    "intensity": 0.3,
                },
            ))
        except Exception as e:
            logger.debug(f"发布屏幕内容事件失败: {e}")

    @staticmethod
    def _parse_text_lines(text: str) -> list:
        lines = []
        in_text_section = False
        for line in text.split("\n"):
            stripped = line.strip()
            if "检测到的文字:" in stripped:
                in_text_section = True
                continue
            if in_text_section:
                if stripped.startswith('"') and stripped.endswith('"'):
                    lines.append(stripped[1:-1].strip())
                elif stripped and not stripped.startswith("-") and not stripped.startswith("屏幕"):
                    lines.append(stripped)
            if stripped.startswith("按钮") and "区域:" in stripped:
                in_text_section = False
        return [l for l in lines if l and len(l) > 1]


def get_screen_monitor_source() -> ScreenMonitorSource:
    return ScreenMonitorSource()
