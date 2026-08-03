#!/bin/bash
# Cortex Agent — macOS 一键启动
# 双击此文件即可启动全部服务 + Qt 桌面客户端

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

PYTHON="/opt/anaconda3/envs/ai_backend/bin/python"

# 测试时跳过视觉模型加载（设为 "mock" 可跳过）
export VISION_BACKEND="${VISION_BACKEND:-mock}"

# ── 检查/启动后端 API :8080 ──────────────────────────────
start_backend() {
    echo "[..] 启动后端 API 服务 (端口 8080)..."
    echo "      VISION_BACKEND=$VISION_BACKEND"
    $PYTHON -m uvicorn api.main:app --host 127.0.0.1 --port 8080 --log-level warning &
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

# ── 启动前端代理 :8765 ───────────────────────────────────
echo "[..] 启动前端服务 (server.py)..."
$PYTHON "$SCRIPT_DIR/server.py" &
SERVER_PID=$!
sleep 1

cleanup() {
    echo "[..] 正在关闭..."
    kill "$SERVER_PID" 2>/dev/null
    kill "$BACKEND_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── 启动 Qt 桌面客户端 ──────────────────────────────────
echo "[..] 打开窗口..."
$PYTHON "$SCRIPT_DIR/main.py"
cleanup
