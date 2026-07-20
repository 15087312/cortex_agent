@echo off
chcp 65001 >nul
title Cortex Agent

echo ==============================================
echo   Cortex Agent - Qt 桌面客户端
echo ==============================================
echo.

:: 检查 Python
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERR] 未检测到 Python，请先安装
    echo       下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 检查 PyQt6，没有就自动装
python -c "from PyQt6.QtWidgets import QApplication" 2>nul
if %ERRORLEVEL% neq 0 (
    echo [..] 正在安装 PyQt6（首次运行需要，约 2 分钟）...
    pip install PyQt6 PyQt6-WebEngine -q
    if %ERRORLEVEL% neq 0 (
        echo [ERR] PyQt6 安装失败，请手动运行: pip install PyQt6 PyQt6-WebEngine
        pause
        exit /b 1
    )
    echo [OK] PyQt6 安装完成
)

cd /d "%~dp0"
echo [..] 启动中...
start /b python frontend\main.py
echo [OK] 窗口已打开，关闭窗口即可停止服务
echo.
echo 如果窗口未自动弹出，请手动打开:
echo   http://localhost:8765
echo.
pause
