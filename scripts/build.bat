@echo off
REM AI Backend Windows 打包脚本

echo ==========================================
echo AI Backend 打包构建 (Windows)
echo ==========================================

REM 设置环境变量
set USE_SQLITE=true
set USE_FAKE_REDIS=true
set PYTHONPATH=%PYTHONPATH%;%CD%

REM 安装依赖
echo 安装打包依赖...
pip install pyinstaller

REM 创建必要目录
if not exist "data\memory" mkdir "data\memory"
if not exist "data\notebook" mkdir "data\notebook"
if not exist "assets" mkdir "assets"

REM 开始打包
echo 开始打包...
pyinstaller pyinstaller.spec --clean --noconfirm

if %ERRORLEVEL% EQU 0 (
    echo ==========================================
    echo 打包成功！
    echo 输出目录: dist\
    dir dist\
    echo ==========================================
) else (
    echo ==========================================
    echo 打包失败！
    echo ==========================================
    exit /b 1
)
