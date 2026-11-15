# 手机自动化工具（AI 可调用）

让 AI 能直接操作真实 Android 手机的工具集。已注册到项目的 `ToolRegistry`，AI 通过工具名直接调用，无需额外装配。

## 快速开始

```bash
# 1. 一键安装环境（adb + uiautomator2）并检查设备
bash scripts/setup_phone_control.sh
```

如果脚本无法自动装 adb，手动二选一：

```bash
brew install android-platform-tools        # 有 Homebrew 时
export PHONE_ADB_PATH=/path/to/adb         # 或指定 adb 完整路径
```

**手机侧需要做的**（只能在手机上手动操作）：

1. 设置 → 关于手机 → 连点「版本号」7 次，开启开发者选项
2. 开发者选项 → 打开「USB 调试」
3. 数据线连接电脑，手机上弹窗点「允许」
4. 验证：`adb devices` 应显示 `device` 状态（不是 `unauthorized`）

## 设计思路

沿用项目里 `browser_control.py` 的哲学：**不截屏、不走视觉模型，用结构化引用**。

浏览器端用 DOM，手机端用 **UI 树**（`dump_hierarchy` 得到 XML）。手机 UI 树其实比 DOM 更规整——有稳定的 `resource-id`、`text`、`content-desc`，定位比 CSS selector 可靠。

流程是：

```
phone_snapshot  →  返回 e1/e2/e3... 引用
        ↓
phone_click("e3")  →  直接点击，不用手算坐标
```

这样做的好处：**快、便宜、稳定**。截屏走视觉模型的方案每次要传图片、要推理，又慢又贵，而且分辨率一变就失效。

## 工具清单（16 个）

### 设备与会话

| 工具 | 说明 | 风险 |
|---|---|---|
| `phone_devices` | 列出已连接设备，确认手机是否可用 | LOW |
| `phone_connect` | 连接指定设备（多设备时用），返回型号/系统/分辨率 | MEDIUM |
| `phone_info` | 设备信息 + 当前前台应用 | LOW |
| `phone_close` | 断开连接、释放资源 | LOW |

### 界面理解

| 工具 | 说明 | 风险 |
|---|---|---|
| `phone_snapshot` | 取可交互元素快照（eN 引用）。**首选**，成本低 | LOW |
| `phone_text` | 取界面全部文本，用于阅读理解 | LOW |
| `phone_screenshot` | 截屏存文件。**仅在必须视觉信息时用** | LOW |

### 交互

| 工具 | 说明 | 风险 |
|---|---|---|
| `phone_click` | 点击（eN 引用 / `text=文字` / XPath / `x,y` 坐标） | MEDIUM |
| `phone_type` | 输入文本，支持先清空 | MEDIUM |
| `phone_press` | 按键（back/home/enter/delete/音量/电源/recent） | MEDIUM |
| `phone_swipe` | 滑动（up/down/left/right 或自定义坐标） | MEDIUM |
| `phone_wait` | 等元素出现/消失，或纯等待 | LOW |

### 应用管理

| 工具 | 说明 | 风险 |
|---|---|---|
| `phone_app_start` | 启动 App（传包名） | MEDIUM |
| `phone_app_stop` | 强制停止 App | MEDIUM |
| `phone_app_list` | 列出已安装应用，可按关键词过滤 | LOW |

### 兜底

| 工具 | 说明 | 风险 |
|---|---|---|
| `phone_shell` | 设备上执行 adb shell 命令，兜底复杂操作 | **HIGH** |

## 常用包名

| 应用 | 包名 |
|---|---|
| 抖音 | `com.ss.android.ugc.aweme` |
| 抖音极速版 | `com.ss.android.ugc.aweme.lite` |
| 小红书 | `com.xingin.xhs` |
| 快手 | `com.smile.gifmaker` |
| 微信 | `com.tencent.mm` |
| B站 | `tv.danmaku.bili` |
| 设置 | `com.android.settings` |

用 `phone_app_list(keyword="douyin")` 之类的关键词可以查到准确的包名。

## 使用示例

### 示例 1：看当前屏幕上有什么

```
phone_snapshot()
→ e1 [Button] "登录"
  e2 [EditText] "手机号"
  e3 [Button] "注册"
```

### 示例 2：打开抖音并刷几条

```
phone_app_start(package="com.ss.android.ugc.aweme")
phone_snapshot()                        # 看清界面，可能有关闭弹窗的按钮
phone_click(target="e2")                # 点掉弹窗
phone_swipe(direction="up", scale=0.6)  # 上滑翻页
phone_swipe(direction="up", scale=0.6)
```

### 示例 3：输入并搜索

```
phone_click(target="text=搜索")
phone_type(target="e1", text="美食教程")
phone_press(key="enter")
```

## 设计上的取舍

### 为什么默认不用截屏

截屏 + 视觉模型是**每次操作都要付一次推理成本**，而且：
- 慢：一张图几 MB，上传 + 推理几秒钟
- 贵：每次点击都等于一次多模态请求
- 脆：屏幕分辨率、主题、字号一变就影响识别

UI 树是**文本**，几 KB，直接读，而且 `resource-id` 是开发者写的稳定标识。所以 `phone_screenshot` 只在真的需要「看」的时候用（比如验证码图片、无法用节点描述的界面）。

### 优雅降级

没装 `uiautomator2` 或没连设备时，工具**不抛异常**，而是返回可读的错误：

```json
{
  "error": "未检测到已授权的 Android 设备。请确认：\n1. 手机已开启「开发者选项 → USB 调试」...",
  "hint": "phone_devices 可查看当前连接状态"
}
```

这样 AI 能理解问题并按提示反馈给你，而不是崩掉。

### 坐标兜底

`phone_click` 支持 `"x,y"` 坐标形式。这是为极少数拿不到节点的情况准备的（比如 Canvas 绘制的界面）。正常情况优先用 `eN` 引用，因为坐标在不同分辨率下会失效。

## 已知限制

1. **只支持 Android**。iOS 需要完全不同的方案（Appium + WebDriverAgent），且签名限制多。
2. **需要真机或模拟器**，且必须开启 USB 调试。
3. **UI 树依赖 App 的实现**。如果 App 用 Flutter/Unity/Canvas 绘制，节点可能拿不到，此时只能退回坐标或截屏识别。
4. **没有内置验证码识别**。短信验证码、图形验证码需要额外处理（人工介入或接 OCR）。
5. **`phone_shell` 是 HIGH 风险**，能执行任意命令，建议在权限控制里限制调用角色。

## 与自动发布的关系

这套工具能解决网页自动化过不去的一些问题——**真实设备、真实 App、真实登录态**，不需要反指纹那一整套（stealth.js、patchright 等）。

但要说清楚：**它解决的是「环境」问题，不是「行为」问题**。平台的风控不只看环境指纹，也看行为模式（同设备、同 IP、固定节奏、批量发布）。而且像抖音发布时的**短信验证码**、视频号的**微信扫码实名**，这些是平台**故意设计成必须有人**的环节，真机也绕不开。

合规上也要注意：用真机做批量账号操作更接近「设备农场」的定义，风险比网页自动化更直接。个人单账号自用没问题，规模化要谨慎。

## 排障

| 现象 | 原因 / 解决 |
|---|---|
| `未找到 adb` | 装 `android-platform-tools`，或设 `PHONE_ADB_PATH` |
| `adb devices` 显示 `unauthorized` | 手机上点「允许 USB 调试」，或撤销授权后重连 |
| `adb devices` 为空 | 换数据线（有些只能充电）、换 USB 口、重开 USB 调试 |
| `未安装 uiautomator2` | `python3 -m pip install uiautomator2` |
| snapshot 为空 | 界面可能全是图片/Canvas；试试 `phone_screenshot` |
| 点击没反应 | 元素可能被遮挡，先 `phone_wait` 或关掉弹窗 |
| 连接超时 | 重启 adb：`adb kill-server && adb start-server` |
