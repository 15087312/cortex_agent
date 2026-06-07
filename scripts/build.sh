#!/bin/bash
# 构建脚本 - 支持 Windows exe 打包

set -e

echo "=========================================="
echo "AI Backend 打包构建"
echo "=========================================="

# 检测操作系统
OS=$(uname -s)
echo "操作系统: $OS"

# 创建必要的目录
mkdir -p data/memory
mkdir -p data/notebook
mkdir -p assets

# 导出环境变量（使用 SQLite 和 Fake Redis）
export USE_SQLITE=true
export USE_FAKE_REDIS=true
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

if [ "$OS" == "Darwin" ] || [ "$OS" == "Linux" ]; then
    echo "安装依赖..."
    pip install pyinstaller
    
    echo "开始打包..."
    pyinstaller pyinstaller.spec --clean --noconfirm
    
    echo "打包完成！"
    echo "输出目录: dist/"
    ls -la dist/
    
elif [ "$OS" == "MINGW64_NT" ] || [ "$OS" == "CYGWIN_NT" ] || [[ "$OS" == *"MINGW"* ]] || [[ "$OS" == *"Windows"* ]]; then
    echo "Windows 环境检测"
    
    echo "安装依赖..."
    pip install pyinstaller
    
    echo "开始打包..."
    pyinstaller pyinstaller.spec --clean --noconfirm --windowed
    
    echo "打包完成！"
    echo "输出目录: dist\\"
    dir dist\\ 2>/dev/null || ls -la dist/
fi

echo "=========================================="
echo "构建完成！"
echo "=========================================="
