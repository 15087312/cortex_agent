#!/usr/bin/env bash
#
# Cortex Agent 一键安装脚本
#
# 使用方式:
#   curl -fsSL https://raw.githubusercontent.com/15087312/cortex_agent/main/install.sh | bash
#
# 可选参数:
#   CORTEX_DIR=/custom/path curl -fsSL ... | bash
#   CORTEX_BRANCH=develop curl -fsSL ... | bash
#
set -euo pipefail

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*" >&2; }

# ── 配置 ──
REPO_URL="https://github.com/15087312/cortex_agent.git"
INSTALL_DIR="${CORTEX_DIR:-$HOME/cortex_agent}"
BRANCH="${CORTEX_BRANCH:-main}"

# ── 系统检测 ──
detect_os() {
    case "$(uname -s)" in
        Linux*)   OS="linux";;
        Darwin*)  OS="macos";;
        MINGW*|MSYS*|CYGWIN*) OS="windows";;
        *)        OS="unknown";;
    esac
}

# ── Python 检测 ──
find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
                PYTHON="$cmd"
                PYTHON_VER="$ver"
                return 0
            fi
        fi
    done
    return 1
}

# ── 前置检查 ──
check_prerequisites() {
    info "检查系统环境..."

    # Git
    if ! command -v git &>/dev/null; then
        err "未找到 git，请先安装: brew install git / apt install git"
        exit 1
    fi
    ok "git $(git --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"

    # Python
    if ! find_python; then
        err "需要 Python 3.11+，当前未找到或版本过低"
        echo ""
        echo "  安装方式:"
        echo "    macOS:   brew install python@3.13"
        echo "    Ubuntu:  sudo apt install python3.13 python3.13-venv"
        echo "    其他:    https://www.python.org/downloads/"
        exit 1
    fi
    ok "python $PYTHON_VER ($PYTHON)"

    # pip
    if ! "$PYTHON" -m pip --version &>/dev/null; then
        warn "未找到 pip，尝试安装..."
        "$PYTHON" -m ensurepip --upgrade 2>/dev/null || {
            err "pip 安装失败，请手动安装: $PYTHON -m ensurepip --upgrade"
            exit 1
        }
    fi
    ok "pip $("$PYTHON" -m pip --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+')"
}

# ── 克隆 / 更新 ──
clone_or_update() {
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "检测到已有安装: $INSTALL_DIR"
        info "更新到最新版本..."
        cd "$INSTALL_DIR"
        git fetch origin "$BRANCH" --quiet
        git checkout "$BRANCH" --quiet
        git reset --hard "origin/$BRANCH" --quiet
        ok "已更新"
    else
        info "克隆仓库到 $INSTALL_DIR ..."
        git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR" --quiet
        ok "克隆完成"
    fi
    cd "$INSTALL_DIR"
}

# ── 安装依赖 ──
install_deps() {
    info "安装 Python 依赖..."
    "$PYTHON" -m pip install -e . --quiet --no-warn-script-location 2>&1 | tail -1
    ok "依赖安装完成"
}

# ── 配置 ──
setup_env() {
    if [[ -f ".env" ]]; then
        ok ".env 已存在，跳过配置"
        return
    fi

    if [[ -f ".env.example" ]]; then
        cp .env.example .env
        ok "已创建 .env（从 .env.example 复制）"
    else
        touch .env
        ok "已创建空 .env"
    fi

    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  配置模型 API Key${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  支持的模型服务:"
    echo "    • DeepSeek  — https://platform.deepseek.com"
    echo "    • OpenAI    — https://platform.openai.com"
    echo "    • 兼容 OpenAI 格式的任何服务"
    echo ""
    echo "  你可以稍后编辑 .env 文件来修改配置。"
    echo ""

    read -rp "  请输入 API Key（直接回车跳过）: " api_key
    if [[ -n "$api_key" ]]; then
        # 替换 .env 中的 LARGE_MODEL_API_KEY
        if [[ "$OS" == "macos" ]]; then
            sed -i '' "s|^LARGE_MODEL_API_KEY=.*|LARGE_MODEL_API_KEY=$api_key|" .env
        else
            sed -i "s|^LARGE_MODEL_API_KEY=.*|LARGE_MODEL_API_KEY=$api_key|" .env
        fi
        ok "API Key 已写入 .env"
    else
        warn "跳过配置，请稍后编辑 $INSTALL_DIR/.env"
    fi
}

# ── 完成 ──
print_done() {
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}  ✓ Cortex Agent 安装完成！${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  启动方式:"
    echo ""
    echo -e "    ${CYAN}cortex${NC}                         # 启动后端 + 交互终端"
    echo -e "    ${CYAN}cortex --no-tui${NC}                # 只启动后端 API 服务"
    echo -e "    ${CYAN}cortex --port 9000${NC}             # 指定端口"
    echo ""
    echo "  配置文件: $INSTALL_DIR/.env"
    echo "  更新命令: cd $INSTALL_DIR && git pull && pip install -e ."
    echo ""

    # 如果 PATH 中没有 cortex，提示添加
    if ! command -v cortex &>/dev/null; then
        warn "cortex 命令未在 PATH 中，尝试以下方式之一:"
        echo ""
        echo "    方式一: 使用完整路径"
        echo -e "      ${CYAN}$PYTHON -m cortex.main${NC}"
        echo ""
        echo "    方式二: 添加到 PATH（在 ~/.bashrc 或 ~/.zshrc 中添加）"
        echo -e "      ${CYAN}export PATH=\"\$($PYTHON -m site --user-base)/bin:\$PATH\"${NC}"
        echo ""
    fi
}

# ── 主流程 ──
main() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║     Cortex Agent 安装程序                ║${NC}"
    echo -e "${CYAN}║     类人智能后端系统                      ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
    echo ""

    detect_os
    check_prerequisites
    echo ""
    clone_or_update
    install_deps
    setup_env
    print_done
}

main "$@"
