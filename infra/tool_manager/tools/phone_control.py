"""
手机控制 — 通过 uiautomator2 + adb 直驱真实 Android 设备

设计原则（沿用 browser_control.py 的思路）：
- 用 UI 树 + 结构化引用定位元素，不截屏、不走视觉模型，快且便宜
- snapshot 返回元素引用（e1/e2...），后续动作直接用 ref
- 依赖缺失 / 无设备时优雅降级，返回可读错误而非抛异常
- AI 可调用的动作集对标 browser_*，保持统一的调用直觉

前置条件：
  brew install android-platform-tools   # 提供 adb
  pip install uiautomator2              # 提供设备控制
  手机开启「开发者选项 → USB 调试」，数据线连接后 `adb devices` 可见

动作集：
  phone_devices / phone_connect / phone_info /
  phone_snapshot / phone_click / phone_type / phone_press /
  phone_swipe / phone_scroll / phone_wait / phone_text /
  phone_app_start / phone_app_stop / phone_app_list /
  phone_screenshot / phone_shell / phone_close
"""
import os
import time
from typing import Any, Dict, List, Optional

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("phone_control")

# ── 会话单例（一个设备连接，多操作复用）──

_device = None
_device_serial = None
_ref_map: Dict[str, Dict[str, str]] = {}  # serial -> {ref: selector}

# 环境变量可覆盖 adb 路径（未装 brew 或自定义安装时用）
_ADB_HINT = os.environ.get("PHONE_ADB_PATH", "adb")


class PhoneUnavailable(Exception):
    """依赖缺失 / 无设备 —— 转换为可读错误返回给 AI。"""


# ── 依赖与设备探测 ──


def _import_uiautomator2():
    """惰性导入 uiautomator2，缺失时给出安装指引。"""
    try:
        import uiautomator2 as u2  # noqa: WPS433
        return u2
    except ImportError:
        raise PhoneUnavailable(
            "未安装 uiautomator2，请执行：pip install uiautomator2"
        )


def _list_adb_devices() -> List[str]:
    """列出 adb 已连接的设备序列号。"""
    import subprocess

    try:
        out = subprocess.run(
            [_ADB_HINT, "devices"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise PhoneUnavailable(
            "未找到 adb，请执行：brew install android-platform-tools\n"
            "（或设置环境变量 PHONE_ADB_PATH 指向 adb 完整路径）"
        )
    except subprocess.TimeoutExpired:
        raise PhoneUnavailable("adb devices 超时，请检查 adb server 是否正常")

    if out.returncode != 0:
        raise PhoneUnavailable(
            "adb devices 执行失败：{}".format((out.stderr or "").strip()[:200])
        )

    serials = []
    for line in (out.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        if state.strip() == "device":
            serials.append(serial.strip())
        elif state.strip() == "unauthorized":
            logger.warning(
                "[Phone] 设备 %s 未授权 —— 请在手机上确认 USB 调试弹窗", serial
            )
    return serials


def _ensure_device(serial: Optional[str] = None):
    """获取（或建立）设备连接，返回 uiautomator2 Device。"""
    global _device, _device_serial

    u2 = _import_uiautomator2()

    if _device is not None and (serial is None or serial == _device_serial):
        return _device

    target = serial
    if target is None:
        devices = _list_adb_devices()
        if not devices:
            raise PhoneUnavailable(
                "未检测到已授权的 Android 设备。请确认：\n"
                "1. 手机已开启「开发者选项 → USB 调试」\n"
                "2. 数据线已连接，且手机弹窗点了「允许」\n"
                "3. 执行 adb devices 能看到 device 状态"
            )
        target = devices[0]

    try:
        _device = u2.connect(target)
        _device_serial = target
        logger.info("[Phone] 已连接设备 %s", target)
        return _device
    except Exception as e:
        raise PhoneUnavailable("连接设备 {} 失败：{}".format(target, str(e)[:200]))


def _build_snapshot(device, serial: str, max_nodes: int = 120) -> str:
    """UI 树快照：可交互元素 → eN 引用。

    优先用 uiautomator2 的 XPath，退化到属性选择器。
    """
    try:
        # uiautomator2 提供 dump_hierarchy 输出 XML；这里用更轻的 jsonrpc 接口
        import xml.etree.ElementTree as ET

        xml = device.dump_hierarchy()
        root = ET.fromstring(xml)
    except Exception as e:
        return "(snapshot 失败: {})".format(str(e)[:150])

    _ref_map[serial] = {}
    lines: List[str] = []

    # 可交互/可读的节点类型
    interactive_cls = {
        "android.widget.Button", "android.widget.EditText",
        "android.widget.ImageButton", "android.widget.CheckBox",
        "android.widget.Switch", "android.widget.RadioButton",
        "android.widget.Spinner", "android.widget.SeekBar",
    }

    def _walk(node, depth=0):
        if len(lines) >= max_nodes:
            return
        cls = node.get("class", "")
        clickable = node.get("clickable") == "true"
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        rid = node.get("resource-id") or ""

        label = text or desc
        is_interactive = clickable or cls in interactive_cls

        if is_interactive and (label or rid):
            n = len(lines) + 1
            ref = "e{}".format(n)
            # 优先用 resource-id（最稳），退化到文本
            if rid:
                selector = '//*[@resource-id="{}"]'.format(rid)
            elif text:
                selector = '//*[@text="{}"]'.format(text)
            elif desc:
                selector = '//*[@content-desc="{}"]'.format(desc)
            else:
                selector = ""
            if selector:
                _ref_map[serial][ref] = selector
                kind = cls.split(".")[-1] if cls else "View"
                lines.append(
                    '{} [{}] "{}"'.format(ref, kind, label[:60])
                )

        for child in list(node):
            _walk(child, depth + 1)

    _walk(root)
    return "\n".join(lines) if lines else "(当前界面无可见可交互元素)"


def _resolve(serial: str, ref_or_selector: str) -> str:
    """把 eN 引用解析成 XPath；非引用则原样当选择器用。"""
    refs = _ref_map.get(serial, {})
    if ref_or_selector in refs:
        return refs[ref_or_selector]
    return ref_or_selector


def _device_result(serial: Optional[str] = None):
    """统一的设备获取 + 错误包装。"""
    try:
        device = _ensure_device(serial)
        return device, None
    except PhoneUnavailable as e:
        return None, {"error": str(e), "hint": "phone_devices 可查看当前连接状态"}


# ── 工具定义 ──


@ToolRegistry.register(
    "phone_devices",
    description="列出已连接的 Android 设备（adb devices）。用于确认手机是否可用。",
    params={},
    risk_level="LOW",
    category="query",
)
def phone_devices() -> Dict[str, Any]:
    try:
        serials = _list_adb_devices()
        if not serials:
            return {
                "devices": [],
                "message": "无已授权设备。请检查 USB 调试是否开启、数据线是否连接、手机弹窗是否点了允许。",
            }
        return {"devices": serials, "count": len(serials)}
    except PhoneUnavailable as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "phone_connect",
    description="连接指定设备（多设备时用）。不传 serial 则自动连第一台。",
    params={"serial": "可选，设备序列号（phone_devices 可查），默认自动选第一台"},
    risk_level="MEDIUM",
    category="query",
)
def phone_connect(serial: Optional[str] = None) -> Dict[str, Any]:
    try:
        device = _ensure_device(serial)
        info = device.info
        return {
            "ok": True,
            "serial": _device_serial,
            "model": info.get("productName", ""),
            "android_version": info.get("version", ""),
            "screen": "{}x{}".format(info.get("displayWidth", 0), info.get("displayHeight", 0)),
        }
    except PhoneUnavailable as e:
        return {"error": str(e)}


@ToolRegistry.register(
    "phone_info",
    description="取当前设备信息（型号/系统版本/屏幕分辨率/当前前台应用）。",
    params={"serial": "可选，设备序列号"},
    risk_level="LOW",
    category="query",
)
def phone_info(serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        info = device.info
        current = device.app_current()
        return {
            "serial": _device_serial,
            "model": info.get("productName", ""),
            "brand": info.get("brand", ""),
            "android_version": info.get("version", ""),
            "screen": "{}x{}".format(info.get("displayWidth", 0), info.get("displayHeight", 0)),
            "current_app": current.get("package", ""),
            "current_activity": current.get("activity", ""),
        }
    except Exception as e:
        return {"error": "读取设备信息失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_snapshot",
    description="取当前界面的可交互元素快照（eN 引用，供 click/type 等用）。不截屏，成本低。",
    params={"serial": "可选，设备序列号", "max_nodes": "可选，最多返回节点数，默认 120"},
    risk_level="LOW",
    category="query",
)
def phone_snapshot(serial: Optional[str] = None, max_nodes: int = 120) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        current = device.app_current()
        snap = _build_snapshot(device, _device_serial or "", max_nodes=max_nodes)
        return {
            "serial": _device_serial,
            "current_app": current.get("package", ""),
            "snapshot": snap,
        }
    except Exception as e:
        return {"error": "snapshot 失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_click",
    description="点击元素（用 snapshot 的 eN 引用，或 text=文本 / XPath / 坐标 x,y）。无需手算坐标。",
    params={"target": "eN 引用，或 text=按钮文字，或 XPath，或 'x,y' 坐标", "serial": "可选"},
    risk_level="MEDIUM",
    category="mutation",
)
def phone_click(target: str, serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        sel = _resolve(_device_serial or "", target)

        # 坐标形式 "x,y"
        if "," in sel and sel.replace(",", "").replace(".", "").isdigit():
            x, y = [float(v) for v in sel.split(",", 1)]
            device.click(x, y)
        elif sel.startswith("text="):
            device(text=sel[5:]).click()
        elif sel.startswith("//") or sel.startswith("("):
            device.xpath(sel).click()
        else:
            # 兜底：当作 text 或 resource-id 尝试
            obj = device(text=sel)
            if not obj.exists:
                obj = device(resourceId=sel)
            obj.click()

        time.sleep(0.6)  # 等界面响应，让后续 snapshot 拿到新状态
        return {
            "ok": True,
            "snapshot": _build_snapshot(device, _device_serial or ""),
        }
    except Exception as e:
        return {"error": "点击失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_type",
    description="向输入框输入文本。target 用 eN 引用或 XPath；clear=True 先清空。",
    params={
        "target": "eN 引用或 XPath（可留空表示向当前焦点输入）",
        "text": "要输入的文本",
        "clear": "可选，是否先清空，默认 True",
        "serial": "可选",
    },
    risk_level="MEDIUM",
    category="mutation",
)
def phone_type(target: str = "", text: str = "", clear: bool = True,
               serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        if target:
            sel = _resolve(_device_serial or "", target)
            obj = device.xpath(sel) if (sel.startswith("//") or sel.startswith("(")) else device(text=sel)
            if clear:
                obj.clear_text()
            obj.set_text(text)
        else:
            if clear:
                device.clear_text()
            device.send_keys(text, clear=False)

        time.sleep(0.5)
        return {"ok": True, "snapshot": _build_snapshot(device, _device_serial or "")}
    except Exception as e:
        return {"error": "输入失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_press",
    description="按键。key 可选：back / home / enter / delete / volume_up / volume_down / power / recent。",
    params={"key": "按键名，默认 back", "serial": "可选"},
    risk_level="MEDIUM",
    category="mutation",
)
def phone_press(key: str = "back", serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    key_map = {
        "back": "back", "home": "home", "enter": "enter",
        "delete": "delete", "volume_up": "volume_up",
        "volume_down": "volume_down", "power": "power",
        "recent": "recent",
    }
    k = key_map.get(key.lower())
    if not k:
        return {"error": "不支持的按键：{}，可选 {}".format(key, "/".join(key_map))}
    try:
        device.press(k)
        time.sleep(0.6)
        return {"ok": True, "snapshot": _build_snapshot(device, _device_serial or "")}
    except Exception as e:
        return {"error": "按键失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_swipe",
    description="滑动屏幕。direction 或自定义坐标（x1,y1,x2,y2）。常用于翻页/刷视频。",
    params={
        "direction": "up/down/left/right，或 'x1,y1,x2,y2' 自定义",
        "scale": "可选，滑动幅度比例 0~1，默认 0.5",
        "serial": "可选",
    },
    risk_level="MEDIUM",
    category="mutation",
)
def phone_swipe(direction: str = "up", scale: float = 0.5,
                serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        w, h = device.window_size()
        scale = max(0.1, min(1.0, float(scale)))

        if "," in direction:
            x1, y1, x2, y2 = [float(v) for v in direction.split(",")[:4]]
        else:
            d = direction.lower()
            cx, cy = w / 2, h / 2
            dx, dy = w * scale / 2, h * scale / 2
            if d == "up":
                x1, y1, x2, y2 = cx, cy + dy, cx, cy - dy
            elif d == "down":
                x1, y1, x2, y2 = cx, cy - dy, cx, cy + dy
            elif d == "left":
                x1, y1, x2, y2 = cx + dx, cy, cx - dx, cy
            elif d == "right":
                x1, y1, x2, y2 = cx - dx, cy, cx + dx, cy
            else:
                return {"error": "不支持的方向：{}，可选 up/down/left/right 或坐标".format(direction)}

        device.swipe(x1, y1, x2, y2, duration=0.3)
        time.sleep(0.6)
        return {"ok": True, "snapshot": _build_snapshot(device, _device_serial or "")}
    except Exception as e:
        return {"error": "滑动失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_wait",
    description="等待元素出现/消失，或等待指定秒数。用于等加载完成。",
    params={
        "target": "可选，eN 引用/text=文本/XPath；留空则纯等待",
        "timeout": "可选，超时秒数，默认 10",
        "gone": "可选，True 表示等元素消失，默认 False",
        "serial": "可选",
    },
    risk_level="LOW",
    category="query",
)
def phone_wait(target: str = "", timeout: float = 10.0, gone: bool = False,
               serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        if not target:
            time.sleep(min(float(timeout), 60.0))
            return {"ok": True, "waited": timeout}

        sel = _resolve(_device_serial or "", target)
        obj = device.xpath(sel) if (sel.startswith("//") or sel.startswith("(")) else device(text=sel)

        if gone:
            ok = obj.wait_gone(timeout=float(timeout))
        else:
            ok = obj.wait(timeout=float(timeout))

        if ok:
            return {"ok": True, "snapshot": _build_snapshot(device, _device_serial or "")}
        return {"error": "等待超时：{} 未{}出现".format(target, "消失" if gone else "")}
    except Exception as e:
        return {"error": "等待失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_text",
    description="取当前界面的全部文本内容（用于阅读理解界面，比 snapshot 全）。",
    params={"max_chars": "可选，最多返回字符数，默认 3000", "serial": "可选"},
    risk_level="LOW",
    category="query",
)
def phone_text(max_chars: int = 3000, serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(device.dump_hierarchy())
        texts: List[str] = []
        seen = set()

        def _walk(node):
            t = (node.get("text") or "").strip()
            d = (node.get("content-desc") or "").strip()
            for v in (t, d):
                if v and v not in seen:
                    seen.add(v)
                    texts.append(v)
            for child in list(node):
                _walk(child)

        _walk(root)
        joined = "\n".join(texts)
        return {
            "text": joined[:max_chars],
            "truncated": len(joined) > max_chars,
            "total_chars": len(joined),
        }
    except Exception as e:
        return {"error": "读取文本失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_app_start",
    description="启动应用。package 传包名（如 com.ss.android.ugc.aweme 抖音）。",
    params={"package": "应用包名", "serial": "可选"},
    risk_level="MEDIUM",
    category="mutation",
)
def phone_app_start(package: str, serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        device.app_start(package)
        time.sleep(2.5)  # 等冷启动
        return {"ok": True, "snapshot": _build_snapshot(device, _device_serial or "")}
    except Exception as e:
        return {"error": "启动 {} 失败：{}".format(package, str(e)[:200])}


@ToolRegistry.register(
    "phone_app_stop",
    description="停止应用（强制退出）。",
    params={"package": "应用包名", "serial": "可选"},
    risk_level="MEDIUM",
    category="mutation",
)
def phone_app_stop(package: str, serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        device.app_stop(package)
        return {"ok": True, "stopped": package}
    except Exception as e:
        return {"error": "停止 {} 失败：{}".format(package, str(e)[:200])}


@ToolRegistry.register(
    "phone_app_list",
    description="列出设备上已安装的应用（包名）。用于找目标 App。",
    params={"keyword": "可选，按关键词过滤包名", "serial": "可选"},
    risk_level="LOW",
    category="query",
)
def phone_app_list(keyword: str = "", serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        pkgs = sorted(device.app_list())
        if keyword:
            kw = keyword.lower()
            pkgs = [p for p in pkgs if kw in p.lower()]
        return {"packages": pkgs[:200], "count": len(pkgs)}
    except Exception as e:
        return {"error": "列出应用失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_screenshot",
    description="截屏保存到本地文件。仅在需要视觉信息时用（比 snapshot 贵，优先用 snapshot）。",
    params={"path": "可选，保存路径，默认 data/phone_screenshot.png", "serial": "可选"},
    risk_level="LOW",
    category="query",
)
def phone_screenshot(path: str = "", serial: Optional[str] = None) -> Dict[str, Any]:
    device, err = _device_result(serial)
    if err:
        return err
    try:
        if not path:
            path = os.path.join("data", "phone_screenshot.png")
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        device.screenshot(path)
        return {"ok": True, "path": os.path.abspath(path)}
    except Exception as e:
        return {"error": "截屏失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_shell",
    description="在设备上执行 adb shell 命令（兜底复杂操作，如 dumpsys / am start / input）。",
    params={"command": "要执行的 shell 命令", "serial": "可选"},
    risk_level="HIGH",
    category="admin",
)
def phone_shell(command: str, serial: Optional[str] = None) -> Dict[str, Any]:
    import subprocess

    try:
        target = serial or _device_serial
        if target is None:
            devices = _list_adb_devices()
            if not devices:
                raise PhoneUnavailable("无已授权设备")
            target = devices[0]

        out = subprocess.run(
            [_ADB_HINT, "-s", target, "shell", command],
            capture_output=True, text=True, timeout=30,
        )
        return {
            "exit_code": out.returncode,
            "stdout": (out.stdout or "")[:5000],
            "stderr": (out.stderr or "")[:2000],
        }
    except PhoneUnavailable as e:
        return {"error": str(e)}
    except subprocess.TimeoutExpired:
        return {"error": "命令执行超时（30s）"}
    except Exception as e:
        return {"error": "执行失败：{}".format(str(e)[:200])}


@ToolRegistry.register(
    "phone_close",
    description="断开设备连接并释放资源。任务结束时可调用。",
    params={"serial": "可选"},
    risk_level="LOW",
    category="query",
)
def phone_close(serial: Optional[str] = None) -> Dict[str, Any]:
    global _device, _device_serial
    try:
        if _device is not None:
            try:
                _device.service("uiautomator").stop()
            except Exception:
                pass
            _device = None
            _device_serial = None
        _ref_map.clear()
        return {"ok": True, "message": "已断开"}
    except Exception as e:
        return {"error": "断开失败：{}".format(str(e)[:200])}
