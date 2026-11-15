#!/usr/bin/env bash
# 手机自动化环境安装脚本
#
# 用法：bash scripts/setup_phone_control.sh
#
# 作用：
#   1. 检查 / 安装 adb（Android 平台工具）
#   2. 安装 Python 依赖 uiautomator2
#   3. 检查设备连接状态，并给出排查指引
#
# 注意：本脚本不会自动开启手机的 USB 调试，那需要你在手机上手动操作。

set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; }

echo "=== 手机自动化环境检查 ==="
echo

# ── 1. adb ──
echo "[1/3] 检查 adb ..."
if command -v adb >/dev/null 2>&1; then
    ok "adb 已安装：$(command -v adb)"
    adb version 2>/dev/null | head -1
else
    warn "未找到 adb"
    if command -v brew >/dev/null 2>&1; then
        echo "    正在通过 brew 安装 android-platform-tools ..."
        if brew install android-platform-tools; then
            ok "adb 安装完成"
        else
            err "brew 安装失败，请手动安装"
        fi
    else
        err "未找到 brew，无法自动安装。请任选其一："
        echo "    · 安装 Homebrew 后执行：brew install android-platform-tools"
        echo "    · 从 https://developer.android.com/tools/releases/platform-tools 下载后"
        echo "      解压，并把 platform-tools 目录加入 PATH"
        echo "    · 或设置环境变量指向 adb：export PHONE_ADB_PATH=/path/to/adb"
    fi
fi
echo

# ── 2. Python 依赖 ──
echo "[2/3] 检查 uiautomator2 ..."
if python3 -c "import uiautomator2" 2>/dev/null; then
    ok "uiautomator2 已安装"
else
    warn "未安装 uiautomator2，正在安装 ..."
    if python3 -m pip install uiautomator2; then
        ok "uiautomator2 安装完成"
    else
        err "安装失败，请手动执行：python3 -m pip install uiautomator2"
    fi
fi
echo

# ── 3. 设备连接 ──
echo "[3/3] 检查设备连接 ..."
if command -v adb >/dev/null 2>&1; then
    # 确保 adb server 已启动
    adb start-server >/dev/null 2>&1

    devices_output=$(adb devices 2>&1)
    echo "$devices_output"
    echo

    if echo "$devices_output" | grep -q "unauthorized"; then
        err "设备未授权 —— 请在手机屏幕上点击「允许 USB 调试」"
    elif echo "$devices_output" | grep -qE "device$"; then
        ok "检测到已授权设备"
    else
        warn "未检测到设备。请依次确认："
        echo "    1. 手机已开启「开发者选项 → USB 调试」"
        echo "       （设置 → 关于手机 → 连点「版本号」7 次可开启开发者选项）"
        echo "    2. 数据线已连接（部分数据线只能充电，不能传数据）"
        echo "    3. 手机上弹出的「允许 USB 调试吗？」点了允许"
        echo "    4. 如果是小米/华为等，可能还需开启「USB 调试（安全设置）」"
    fi
else
    warn "跳过（adb 未安装）"
fi

echo
echo "=== 检查完成 ==="
echo "安装就绪后，AI 可直接调用以下工具："
echo "  phone_devices    — 查看设备"
echo "  phone_snapshot   — 取界面元素快照（eN 引用）"
echo "  phone_click      — 点击元素"
echo "  phone_type       — 输入文本"
echo "  phone_app_start  — 启动 App"
