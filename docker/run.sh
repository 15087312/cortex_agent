#!/usr/bin/env bash
# ============================================================
# Humanoid AGI — 一键启动脚本
# 自动：构建/拉取后端镜像 → 启动服务 → 安装 CLI → 打开界面
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8080}"

echo "========================================"
echo "  Humanoid AGI — 一键启动"
echo "========================================"

# ── 1. 检查 Docker ──────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[✗] Docker 未安装。请先安装 Docker Desktop："
    echo "    https://www.docker.com/products/docker-desktop/"
    exit 1
fi

# ── 2. 检查 .env ─────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "[!] 未找到 .env 文件，正在从 .env.example 复制..."
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo "[!] 请编辑 $PROJECT_DIR/.env 填入你的 API Key"
        echo "    然后重新运行 ./docker/run.sh"
        exit 1
    else
        echo "[✗] 缺少 .env 文件"
        exit 1
    fi
fi

# ── 3. 构建并启动后端 ──────────────────────────────────
echo ""
echo "[1/3] 构建后端 Docker 镜像..."
cd "$PROJECT_DIR"
docker compose -f docker/docker-compose.yml build backend

echo ""
echo "[2/3] 启动后端服务..."
docker compose -f docker/docker-compose.yml up -d backend

echo "等待后端就绪..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${BACKEND_PORT}/health" >/dev/null 2>&1; then
        echo "[✓] 后端已就绪 (localhost:${BACKEND_PORT})"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "[✗] 后端启动超时，请检查日志：docker compose logs backend"
        exit 1
    fi
    sleep 2
done

# ── 4. 准备 CLI ─────────────────────────────────────────
echo ""
echo "[3/3] 准备 CLI..."
VENV_DIR="$PROJECT_DIR/.cli-venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "创建 CLI 虚拟环境..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# 安装 CLI 依赖
if [ ! -f "$VENV_DIR/.deps_installed" ]; then
    echo "安装 CLI 依赖 (textual, httpx)..."
    pip install --quiet textual httpx
    touch "$VENV_DIR/.deps_installed"
fi

# ── 5. 启动 CLI ─────────────────────────────────────────
echo ""
echo "========================================"
echo "  启动 CLI (连接 localhost:${BACKEND_PORT})"
echo "  提示: 按 q 退出 / Ctrl+C 停止"
echo "========================================"
echo ""

cd "$PROJECT_DIR"
PYTHONPATH="$PROJECT_DIR" python -m cli_tui.main --api-url "http://localhost:${BACKEND_PORT}"

# ── 清理 ────────────────────────────────────────────────
echo ""
echo "正在停止后端服务..."
docker compose -f docker/docker-compose.yml stop backend
echo "[✓] 已停止"
