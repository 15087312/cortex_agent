@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Cortex Agent
cd /d "%~dp0"

echo ==============================================
echo   Cortex Agent - 桌面客户端（一键启动）
echo   首次运行会自动安装依赖（需要网络，约几分钟）
echo ==============================================
echo.

rem ── 1. 查找 Python 3.11+ ──────────────────────────
set "PY="
py -3 --version >nul 2>&1
if %ERRORLEVEL%==0 set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1
    if %ERRORLEVEL%==0 set "PY=python"
)
if not defined PY (
    echo [ERR] 未检测到 Python 3，请先安装：
    echo        https://www.python.org/downloads/
    echo        安装时务必勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERR] 需要 Python 3.11 或更高版本，当前版本过旧。
    echo        请升级：https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] 找到 Python (%PY%)

rem ── 2. 虚拟环境 ────────────────────────────────────
set "VENVPY=.venv\Scripts\python.exe"
if not exist "%VENVPY%" (
    echo [..] 首次运行：创建虚拟环境 .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERR] 创建虚拟环境失败，请检查 Python 安装。
        pause
        exit /b 1
    )
    echo [OK] 虚拟环境已创建
)

rem ── 3. 安装后端依赖（requirements.txt + Qt 壳）─────
"%VENVPY%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [..] 安装后端依赖（首次需要几分钟，含 torch 等）...
    "%VENVPY%" -m pip install --upgrade pip -q
    "%VENVPY%" -m pip install -r requirements.txt -q
    if errorlevel 1 (
        echo [ERR] 后端依赖安装失败，请手动执行：
        echo        "%VENVPY%" -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [OK] 后端依赖安装完成
)

"%VENVPY%" -c "from PyQt6.QtWidgets import QApplication" >nul 2>&1
if errorlevel 1 (
    echo [..] 安装 Qt 桌面依赖（PyQt6 + WebEngine）...
    "%VENVPY%" -m pip install PyQt6 PyQt6-WebEngine -q
    if errorlevel 1 (
        echo [WARN] Qt 依赖安装失败，桌面窗口不可用；可手动运行：
        echo        "%VENVPY%" -m pip install PyQt6 PyQt6-WebEngine
    ) else (
        echo [OK] Qt 桌面依赖安装完成
    )
)

rem ── 4. 可选：Whisper 本地语音识别（约 2GB）────────
"%VENVPY%" -c "import whisper" >nul 2>&1
if errorlevel 1 (
    set /p WANT_WHISPER="是否安装 Whisper 语音识别（约 2GB，可选）？[y/N]: "
    if /i "!WANT_WHISPER!"=="y" (
        echo [..] 安装 Whisper ...
        "%VENVPY%" -m pip install openai-whisper -q
    ) else (
        echo [..] 跳过 Whisper。之后可运行 "%VENVPY%" -m pip install openai-whisper 补装
    )
)

rem ── 5. 前端构建产物检查（dist 缺失时用 npm 构建）──
if not exist "frontend\dist\index.html" (
    echo [..] 前端未构建，尝试 npm build ...
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [WARN] 未找到 Node.js/npm，无法构建前端；网页界面可能不可用。
    ) else (
        pushd frontend
        call npm run build
        popd
    )
)

rem ── 6. 确保后端 API :8080 ──────────────────────────────
curl -s http://127.0.0.1:8080/health >nul 2>&1
if errorlevel 1 (
    echo [..] 启动后端 API 服务 (端口 8080)...
    start "Cortex Backend" /b "%VENVPY%" -m uvicorn api.main:app --host 127.0.0.1 --port 8080 --log-level warning
    echo [..] 等待后端就绪（首次需加载感知/视觉/embedding，可能 1~2 分钟）...
    set /a tries=0
    :wait_backend
    curl -s http://127.0.0.1:8080/health >nul 2>&1
    if errorlevel 1 (
        set /a tries+=1
        if !tries! lss 120 (
            timeout /t 1 /nobreak >nul
            goto wait_backend
        ) else (
            echo [WARN] 后端启动超时（120s）。可在 ~/.cortex/settings.json 设 VISION_BACKEND=mock 加快
        )
    ) else (
        echo [OK] 后端已就绪
    )
) else (
    echo [OK] 后端已在运行 (8080)
)

rem ── 7. 启动桌面客户端（Qt 窗口，内含前端服务）───────
echo [..] 启动桌面客户端...
"%VENVPY%" frontend\main.py
if errorlevel 1 (
    echo.
    echo [ERR] 客户端异常退出，请查看上方错误信息。
    pause
)
