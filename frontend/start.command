#!/bin/bash
# Cortex Agent — macOS 一键启动
# 双击此文件即可自动装依赖（首次）+ 启动全部服务 + Qt 桌面客户端

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

# 测试时跳过视觉模型加载（设为 "mock" 可跳过）
export VISION_BACKEND="${VISION_BACKEND:-mock}"

# ── 1. 查找 Python 3.11+ ───────────────────────────────────
PYTHON=""
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="python"
else
    echo "[ERR] 需要 Python 3.11+。安装: brew install python@3.13"
    read -r -p "按回车退出" _
    exit 1
fi
echo "[OK] 找到 Python ($PYTHON)"

# ── 2. 虚拟环境 + 依赖 ────────────────────────────────────
VENV="$PROJECT_DIR/.venv"
VENVPY="$VENV/bin/python"
if [ ! -x "$VENVPY" ]; then
    echo "[..] 首次运行：创建虚拟环境 .venv ..."
    "$PYTHON" -m venv "$VENV"
fi

if ! "$VENVPY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    echo "[..] 安装后端依赖（首次约几分钟）..."
    "$VENVPY" -m pip install --upgrade pip -q
    "$VENVPY" -m pip install -r requirements.txt -q
fi

if ! "$VENVPY" -c "from PyQt6.QtWidgets import QApplication" >/dev/null 2>&1; then
    echo "[..] 安装 Qt 桌面依赖（PyQt6 + WebEngine）..."
    "$VENVPY" -m pip install PyQt6 PyQt6-WebEngine -q
fi

# ── 3. 前端构建产物检查（dist 缺失时用 npm 构建）─────────
if [ ! -f "frontend/dist/index.html" ]; then
    echo "[..] 前端未构建，尝试 npm build ..."
    if command -v npm >/dev/null 2>&1; then
        ( cd frontend && npm run build ) || echo "[WARN] npm build 失败"
    else
        echo "[WARN] 未找到 npm，前端页面可能不可用。请安装 Node.js"
    fi
fi

# ── 4. 检查/启动后端 API :8080 ────────────────────────────
start_backend() {
    echo "[..] 启动后端 API 服务 (端口 8080)..."
    echo "      VISION_BACKEND=$VISION_BACKEND"
    "$VENVPY" -m uvicorn api.main:app --host 127.0.0.1 --port 8080 --log-level warning &
    BACKEND_PID=$!
    echo "      （首次启动需加载感知/视觉/embedding，可能需 1~2 分钟）"
    for i in $(seq 1 120); do
        if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
            echo "[OK] 后端已启动 (${i}s)"
            return 0
        fi
        sleep 1
    done
    echo "[ERR] 后端启动超时 (120s)。若视觉模型加载过慢，可在 ~/.cortex/settings.json 设 VISION_BACKEND=mock"
    return 1
}

if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "[OK] 后端服务已在运行"
else
    start_backend || exit 1
fi

# ── 5. 启动前端代理 :8765 ─────────────────────────────────
echo "[..] 启动前端服务 (server.py)..."
"$VENVPY" "$SCRIPT_DIR/server.py" &
SERVER_PID=$!
sleep 1

cleanup() {
    echo "[..] 正在关闭..."
    kill "$SERVER_PID" 2>/dev/null
    kill "$BACKEND_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── 6. 启动 Qt 桌面客户端 ─────────────────────────────────
echo "[..] 打开窗口..."
"$VENVPY" "$SCRIPT_DIR/main.py"
cleanup
