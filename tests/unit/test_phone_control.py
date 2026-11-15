"""手机控制工具 — adb 探测 / 设备连接 / UI 快照 / 各动作的错误降级

这一层此前 0 覆盖（348 行里 299 行没跑过）。它被 AI 直接调用，出错时必须是
「返回可读错误」而不是抛异常，所以逐条钉住降级路径比钉住成功路径更重要。
所有测试用假 device / 假 subprocess，不碰真手机和真 adb。
"""
import subprocess
import types
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.tools import phone_control as pc

BUTTON_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" clickable="false" text="" content-desc="" resource-id="">
    <node class="android.widget.Button" clickable="true" text="确定" content-desc="" resource-id="com.demo:id/ok"/>
    <node class="android.widget.EditText" clickable="true" text="" content-desc="搜索框" resource-id=""/>
    <node class="android.widget.TextView" clickable="false" text="只读文字" content-desc="" resource-id=""/>
    <node class="android.widget.ImageButton" clickable="false" text="" content-desc="" resource-id="com.demo:id/icon"/>
  </node>
</hierarchy>
"""


class FakeObj:
    """device(text=...) / device.xpath(...) 返回的元素对象"""

    def __init__(self, exists=True, wait_result=True, fail=None):
        self.exists = exists
        self.wait_result = wait_result
        self.fail = fail
        self.clicked = 0
        self.cleared = 0
        self.text_set = []

    def click(self):
        if self.fail:
            raise self.fail
        self.clicked += 1

    def clear_text(self):
        self.cleared += 1

    def set_text(self, text):
        self.text_set.append(text)

    def wait(self, timeout=0):
        return self.wait_result

    def wait_gone(self, timeout=0):
        return self.wait_result


class FakeDevice:
    def __init__(self, xml=BUTTON_XML):
        self._xml = xml
        self.info = {
            "productName": "Pixel 7",
            "brand": "google",
            "version": "14",
            "displayWidth": 1080,
            "displayHeight": 2400,
        }
        self.clicks = []
        self.swipes = []
        self.presses = []
        self.started = []
        self.stopped = []
        self.shell = []
        self.screenshots = []
        self.keys = []
        self.clears = 0
        self.xpath_obj = FakeObj()
        self.text_obj = FakeObj()
        self.text_obj.exists = True
        self.rid_obj = FakeObj()
        self.window = (1080, 2400)
        self.packages = ["com.android.settings", "com.ss.android.ugc.aweme"]
        self.service_obj = MagicMock()

    # 查询
    def app_current(self):
        return {"package": "com.demo", "activity": ".Main"}

    def dump_hierarchy(self):
        return self._xml

    def window_size(self):
        return self.window

    # 动作
    def click(self, x, y):
        self.clicks.append((x, y))

    def xpath(self, sel):
        return self.xpath_obj

    def __call__(self, text=None, resourceId=None):
        return self.text_obj if text is not None else self.rid_obj

    def send_keys(self, text, clear=False):
        self.keys.append(text)

    def clear_text(self):
        self.clears += 1

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        self.swipes.append((x1, y1, x2, y2))

    def press(self, key):
        self.presses.append(key)

    def app_start(self, package):
        self.started.append(package)

    def app_stop(self, package):
        self.stopped.append(package)

    def app_list(self):
        return list(self.packages)

    def screenshot(self, path):
        self.screenshots.append(path)

    def service(self, name):
        self.service_obj.name = name
        return self.service_obj


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    monkeypatch.setattr(pc, "_device", None)
    monkeypatch.setattr(pc, "_device_serial", None)
    monkeypatch.setattr(pc, "_ref_map", {})
    # 工具里的 sleep 只为等界面，测试不必真等
    monkeypatch.setattr(pc, "time", types.SimpleNamespace(sleep=lambda s: None))


@pytest.fixture
def device(monkeypatch):
    dev = FakeDevice()
    monkeypatch.setattr(pc, "_device", dev)
    monkeypatch.setattr(pc, "_device_serial", "SERIAL1")
    monkeypatch.setattr(pc, "_import_uiautomator2", lambda: MagicMock())
    return dev


class FakeRun:
    def __init__(self, stdout="", returncode=0, stderr="", error=None):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.error = error
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.error:
            raise self.error
        return types.SimpleNamespace(
            stdout=self.stdout, stderr=self.stderr, returncode=self.returncode
        )


# ── 依赖 / 设备探测 ──

def test_missing_uiautomator2_returns_install_hint(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "uiautomator2":
            raise ImportError("no module")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(pc.PhoneUnavailable) as ei:
        pc._import_uiautomator2()
    assert "pip install uiautomator2" in str(ei.value)


def test_adb_missing_binary_reports_install_hint(monkeypatch):
    fake = FakeRun(error=FileNotFoundError())
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(pc.PhoneUnavailable) as ei:
        pc._list_adb_devices()
    assert "android-platform-tools" in str(ei.value)
    assert "PHONE_ADB_PATH" in str(ei.value)


def test_adb_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(error=subprocess.TimeoutExpired("adb", 10)))
    with pytest.raises(pc.PhoneUnavailable) as ei:
        pc._list_adb_devices()
    assert "超时" in str(ei.value)


def test_adb_failure_includes_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(returncode=1, stderr="server not running"))
    with pytest.raises(pc.PhoneUnavailable) as ei:
        pc._list_adb_devices()
    assert "server not running" in str(ei.value)


def test_adb_parser_keeps_only_authorized_devices(monkeypatch):
    out = (
        "List of devices attached\n"
        "SER1\tdevice\n"
        "SER2\tunauthorized\n"
        "\n"
        "garbage-without-tab\n"
        "SER3\tdevice\n"
    )
    monkeypatch.setattr(subprocess, "run", FakeRun(stdout=out))
    assert pc._list_adb_devices() == ["SER1", "SER3"]


def test_ensure_device_reuses_cached_connection(monkeypatch):
    cached = MagicMock()
    u2 = MagicMock()
    monkeypatch.setattr(pc, "_import_uiautomator2", lambda: u2)
    monkeypatch.setattr(pc, "_device", cached)
    monkeypatch.setattr(pc, "_device_serial", "SER1")
    assert pc._ensure_device() is cached
    u2.connect.assert_not_called()
    assert pc._ensure_device("SER1") is cached


def test_ensure_device_switches_when_other_serial_requested(monkeypatch):
    other = MagicMock()
    u2 = MagicMock()
    u2.connect.return_value = other
    monkeypatch.setattr(pc, "_device", MagicMock())
    monkeypatch.setattr(pc, "_device_serial", "SER1")
    monkeypatch.setattr(pc, "_import_uiautomator2", lambda: u2)

    assert pc._ensure_device("SER2") is other
    assert pc._device_serial == "SER2"
    u2.connect.assert_called_once_with("SER2")


def test_ensure_device_without_any_device_explains_steps(monkeypatch):
    monkeypatch.setattr(pc, "_list_adb_devices", lambda: [])
    monkeypatch.setattr(pc, "_import_uiautomator2", lambda: MagicMock())
    with pytest.raises(pc.PhoneUnavailable) as ei:
        pc._ensure_device()
    assert "USB 调试" in str(ei.value)


def test_ensure_device_connect_error_is_wrapped(monkeypatch):
    u2 = MagicMock()
    u2.connect.side_effect = RuntimeError("device offline")
    monkeypatch.setattr(pc, "_import_uiautomator2", lambda: u2)
    monkeypatch.setattr(pc, "_list_adb_devices", lambda: ["SER9"])
    with pytest.raises(pc.PhoneUnavailable) as ei:
        pc._ensure_device()
    assert "device offline" in str(ei.value)


def test_device_result_wraps_unavailable(monkeypatch):
    monkeypatch.setattr(
        pc, "_ensure_device", lambda serial=None: (_ for _ in ()).throw(pc.PhoneUnavailable("boom"))
    )
    device_obj, err = pc._device_result()
    assert device_obj is None
    assert err["error"] == "boom"
    assert "phone_devices" in err["hint"]


# ── 快照 ──

def test_snapshot_lists_interactive_elements_with_refs():
    dev = FakeDevice()
    out = pc._build_snapshot(dev, "SER1")
    refs = pc._ref_map["SER1"]
    assert refs["e1"] == '//*[@resource-id="com.demo:id/ok"]'
    assert refs["e2"] == '//*[@content-desc="搜索框"]'
    assert refs["e3"] == '//*[@resource-id="com.demo:id/icon"]'
    assert "只读文字" not in out                     # 非可交互的 TextView 不进快照
    assert 'e1 [Button] "确定"' in out


def test_snapshot_prefers_text_selector_when_no_resource_id():
    xml = """<hierarchy><node class="android.widget.Button" clickable="true" text="登录"
    content-desc="" resource-id=""/></hierarchy>"""
    pc._build_snapshot(FakeDevice(xml), "SER1")
    assert pc._ref_map["SER1"] == {"e1": '//*[@text="登录"]'}


def test_snapshot_skips_elements_without_any_handle():
    xml = """<hierarchy><node class="android.widget.Button" clickable="true" text=""
    content-desc="" resource-id=""/></hierarchy>"""
    out = pc._build_snapshot(FakeDevice(xml), "SER1")
    assert out == "(当前界面无可见可交互元素)"
    assert pc._ref_map["SER1"] == {}


def test_snapshot_respects_max_nodes():
    nodes = "".join(
        '<node class="android.widget.Button" clickable="true" text="b{}" content-desc="" resource-id=""/>'.format(i)
        for i in range(5)
    )
    out = pc._build_snapshot(FakeDevice("<hierarchy>{}</hierarchy>".format(nodes)), "SER1", max_nodes=2)
    assert len(out.splitlines()) == 2


def test_snapshot_dump_failure_is_readable():
    dev = FakeDevice()
    dev.dump_hierarchy = MagicMock(side_effect=RuntimeError("uiautomator died"))
    out = pc._build_snapshot(dev, "SER1")
    assert out.startswith("(snapshot 失败:")
    assert "uiautomator died" in out


def test_resolve_passes_unknown_selector_through(monkeypatch):
    monkeypatch.setattr(pc, "_ref_map", {"SER1": {"e1": "//X"}})
    assert pc._resolve("SER1", "e1") == "//X"
    assert pc._resolve("SER1", '//*[@text="原生"]') == '//*[@text="原生"]'
    assert pc._resolve("OTHER", "e1") == "e1"


# ── phone_devices / connect / info ──

def test_phone_devices_empty_and_error(monkeypatch):
    monkeypatch.setattr(pc, "_list_adb_devices", lambda: [])
    r = pc.phone_devices()
    assert r["devices"] == [] and "USB 调试" in r["message"]

    monkeypatch.setattr(
        pc, "_list_adb_devices", lambda: (_ for _ in ()).throw(pc.PhoneUnavailable("no adb"))
    )
    assert pc.phone_devices() == {"error": "no adb"}


def test_phone_devices_lists_serials(monkeypatch):
    monkeypatch.setattr(pc, "_list_adb_devices", lambda: ["A", "B"])
    assert pc.phone_devices() == {"devices": ["A", "B"], "count": 2}


def test_phone_connect_reports_device_info(device):
    r = pc.phone_connect()
    assert r["ok"] is True
    assert r["serial"] == "SERIAL1"
    assert r["model"] == "Pixel 7"
    assert r["screen"] == "1080x2400"


def test_phone_connect_error(monkeypatch):
    monkeypatch.setattr(
        pc, "_ensure_device", lambda serial=None: (_ for _ in ()).throw(pc.PhoneUnavailable("nope"))
    )
    assert pc.phone_connect() == {"error": "nope"}


def test_phone_info_reports_current_app(device):
    r = pc.phone_info()
    assert r["brand"] == "google"
    assert r["current_app"] == "com.demo"
    assert r["current_activity"] == ".Main"


def test_phone_info_survives_backend_exception(device):
    device.app_current = MagicMock(side_effect=RuntimeError("gone"))
    r = pc.phone_info()
    assert r["error"].startswith("读取设备信息失败")


def test_phone_snapshot_returns_refs(device):
    r = pc.phone_snapshot()
    assert r["current_app"] == "com.demo"
    assert "e1 [Button] \"确定\"" in r["snapshot"]


def test_phone_snapshot_unavailable(monkeypatch):
    monkeypatch.setattr(pc, "_device", None)
    monkeypatch.setattr(pc, "_device_serial", None)
    monkeypatch.setattr(
        pc, "_ensure_device", lambda serial=None: (_ for _ in ()).throw(pc.PhoneUnavailable("no phone"))
    )
    assert pc.phone_snapshot()["error"] == "no phone"


# ── 点击 ──

def test_click_by_ref_uses_xpath(device):
    pc._ref_map["SERIAL1"] = {"e1": '//*[@resource-id="com.demo:id/ok"]'}
    r = pc.phone_click("e1")
    assert r["ok"] is True
    assert device.xpath_obj.clicked == 1
    assert "snapshot" in r


def test_click_by_coordinates(device):
    pc.phone_click("120,340")       # 坐标必须是「纯数字+逗号」，带空格会被当成文本
    assert device.clicks == [(120.0, 340.0)]


def test_click_by_text_prefix(device):
    pc.phone_click("text=确定")
    assert device.text_obj.clicked == 1


def test_click_xpath_passthrough(device):
    pc.phone_click('//*[@text="确定"]')
    assert device.xpath_obj.clicked == 1


def test_click_falls_back_to_resource_id_when_text_missing(device):
    device.text_obj.exists = False
    pc.phone_click("com.demo:id/ok")
    assert device.rid_obj.clicked == 1


def test_click_backend_error_is_wrapped(device):
    device.xpath_obj.fail = RuntimeError("stale element")
    r = pc.phone_click('//*[@text="确定"]')
    assert r["error"].startswith("点击失败") and "stale element" in r["error"]


# ── 输入 / 按键 ──

def test_type_into_ref_clears_then_sets(device):
    r = pc.phone_type('//*[@text="搜索"]', "猫粮")
    assert r["ok"] is True
    assert device.xpath_obj.cleared == 1
    assert device.xpath_obj.text_set == ["猫粮"]


def test_type_without_target_uses_focused_field(device):
    pc.phone_type(text="你好", clear=True)
    assert device.clears == 1
    assert device.keys == ["你好"]


def test_type_deduplicates_duplicate_backend_error(device):
    device.text_obj.clear_text = MagicMock(side_effect=RuntimeError("readonly"))
    r = pc.phone_type("搜索框", "x")
    assert r["error"].startswith("输入失败")


def test_press_valid_and_invalid_keys(device):
    assert pc.phone_press("HOME")["ok"] is True
    assert device.presses == ["home"]

    r = pc.phone_press("kitchen_sink")
    assert "不支持的按键" in r["error"]


def test_press_backend_error(device):
    device.press = MagicMock(side_effect=RuntimeError("no key"))
    assert pc.phone_press("back")["error"].startswith("按键失败")


# ── 滑动 ──

@pytest.mark.parametrize(
    "direction,expect",
    [
        ("up", (540.0, 1800.0, 540.0, 600.0)),
        ("down", (540.0, 600.0, 540.0, 1800.0)),
        ("left", (810.0, 1200.0, 270.0, 1200.0)),
        ("right", (270.0, 1200.0, 810.0, 1200.0)),
    ],
)
def test_swipe_directions(device, direction, expect):
    assert pc.phone_swipe(direction)["ok"] is True
    assert device.swipes[-1] == expect


def test_swipe_custom_coordinates_and_scale_clamped(device):
    pc.phone_swipe("10,20,30,40", scale=5)
    assert device.swipes[-1] == (10.0, 20.0, 30.0, 40.0)
    device.swipes.clear()
    pc.phone_swipe("up", scale=0)
    x1, y1, x2, y2 = device.swipes[-1]
    assert 600 <= y1 <= 1800 and 600 <= y2 <= 1800


def test_swipe_rejects_unknown_direction(device):
    r = pc.phone_swipe("sideways")
    assert "不支持的方向" in r["error"]
    assert device.swipes == []


def test_swipe_backend_error(device):
    device.swipe = MagicMock(side_effect=RuntimeError("input blocked"))
    assert pc.phone_swipe("up")["error"].startswith("滑动失败")


# ── 等待 / 文本 ──

def test_wait_pure_sleep_is_capped(device, monkeypatch):
    slept = []
    monkeypatch.setattr(pc, "time", types.SimpleNamespace(sleep=slept.append))
    r = pc.phone_wait(timeout=999)
    assert slept == [60.0]
    assert r == {"ok": True, "waited": 999}


def test_wait_for_element_present(device):
    assert pc.phone_wait("搜索框")["ok"] is True


def test_wait_for_element_gone(device):
    assert pc.phone_wait("搜索框", gone=True)["ok"] is True


def test_wait_timeout_reports_gone_flag(device):
    device.text_obj.wait_result = False
    r = pc.phone_wait("搜索框", gone=True)
    assert "未消失出现" in r["error"]


def test_wait_backend_error(device):
    device.text_obj.wait = MagicMock(side_effect=RuntimeError("rpc reset"))
    assert pc.phone_wait("搜索框")["error"].startswith("等待失败")


def test_phone_text_dedupes_and_truncates(device):
    xml = """<hierarchy>
      <node text="标题" content-desc="" resource-id=""/>
      <node text="标题" content-desc="" resource-id=""/>
      <node text="" content-desc="副标题" resource-id=""/>
    </hierarchy>"""
    device._xml = xml
    r = pc.phone_text()
    assert r["text"] == "标题\n副标题"
    assert r["truncated"] is False

    r2 = pc.phone_text(max_chars=1)
    assert r2["truncated"] is True and r2["text"] == "标"


def test_phone_text_parse_error(device):
    device._xml = "<not-closed>"
    assert pc.phone_text()["error"].startswith("读取文本失败")


# ── 应用管理 ──

def test_app_start_stop_list(device):
    assert pc.phone_app_start("com.demo")["ok"] is True
    assert device.started == ["com.demo"]

    assert pc.phone_app_stop("com.demo")["ok"] is True
    assert device.stopped == ["com.demo"]

    assert pc.phone_app_list()["count"] == 2
    filtered = pc.phone_app_list("DOUYIN")
    assert filtered == {"packages": [], "count": 0}
    assert pc.phone_app_list("ugc")["packages"] == ["com.ss.android.ugc.aweme"]


@pytest.mark.parametrize(
    "func,args,prefix",
    [
        (pc.phone_app_start, ("com.demo",), "启动 com.demo 失败"),
        (pc.phone_app_stop, ("com.demo",), "停止 com.demo 失败"),
        (pc.phone_app_list, ("",), "列出应用失败"),
    ],
)
def test_app_commands_wrap_errors(device, func, args, prefix):
    device.app_start = MagicMock(side_effect=RuntimeError("x"))
    device.app_stop = MagicMock(side_effect=RuntimeError("x"))
    device.app_list = MagicMock(side_effect=RuntimeError("x"))
    assert func(*args)["error"].startswith(prefix)


# ── 截屏 / shell / 关闭 ──

def test_screenshot_default_path_and_custom(device, tmp_path):
    target = tmp_path / "shot.png"
    assert pc.phone_screenshot(str(target)) == {"ok": True, "path": str(target)}
    assert device.screenshots[-1] == str(target)

    r = pc.phone_screenshot()
    assert r["path"].endswith("phone_screenshot.png")


def test_screenshot_error(device, tmp_path):
    device.screenshot = MagicMock(side_effect=OSError("no space"))
    assert pc.phone_screenshot(str(tmp_path / "a.png"))["error"].startswith("截屏失败")


def test_shell_with_connected_device(device, monkeypatch):
    fake = FakeRun(stdout="ok\n", stderr="warn")
    monkeypatch.setattr(subprocess, "run", fake)
    r = pc.phone_shell("dumpsys battery")
    assert r["stdout"] == "ok\n" and r["exit_code"] == 0
    assert fake.calls[0][1:4] == ["-s", "SERIAL1", "shell"]


def test_shell_picks_first_device_when_not_connected(monkeypatch):
    monkeypatch.setattr(pc, "_device_serial", None)
    monkeypatch.setattr(pc, "_list_adb_devices", lambda: ["SER7"])
    fake = FakeRun(stdout="")
    monkeypatch.setattr(subprocess, "run", fake)
    assert pc.phone_shell("ls")["exit_code"] == 0
    assert fake.calls[0][:3] == ["adb", "-s", "SER7"]


def test_shell_without_devices(monkeypatch):
    monkeypatch.setattr(pc, "_device_serial", None)
    monkeypatch.setattr(pc, "_list_adb_devices", lambda: [])
    assert pc.phone_shell("ls") == {"error": "无已授权设备"}


def test_shell_timeout_and_other_errors(monkeypatch):
    monkeypatch.setattr(pc, "_device_serial", "SER1")
    monkeypatch.setattr(
        subprocess, "run", FakeRun(error=subprocess.TimeoutExpired("adb", 30))
    )
    assert "超时" in pc.phone_shell("sleep 99")["error"]

    monkeypatch.setattr(subprocess, "run", FakeRun(error=RuntimeError("kaboom")))
    assert pc.phone_shell("ls")["error"].startswith("执行失败")


def test_close_stops_service_and_clears_state(device):
    r = pc.phone_close()
    assert r == {"ok": True, "message": "已断开"}
    device.service_obj.stop.assert_called_once()
    assert pc._device is None and pc._device_serial is None


def test_close_without_device_is_noop():
    assert pc.phone_close()["ok"] is True


def test_close_survives_service_stop_error(device):
    device.service_obj.stop.side_effect = RuntimeError("already gone")
    assert pc.phone_close()["ok"] is True
