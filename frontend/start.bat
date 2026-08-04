@echo off
REM Cortex Agent - Windows 一键启动（Qt 桌面客户端）
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0.."

set "PYTHON=python"
where python >nul 2>&1
if errorlevel 1 (
    echo [ERR] 未找到 python，请先安装并加入 PATH
    pause
    exit /b 1
)

REM 测试时跳过视觉模型加载（设为 mock 可跳过）
if not defined VISION_BACKEND set "VISION_BACKEND=mock"
echo        VISION_BACKEND=!VISION_BACKEND!

REM ---- 检查/启动后端 API :8080 ----
curl -sf http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 goto backend_already

echo [..] 启动后端 API 服务 ^(端口 8080^)...
start "cortex-backend" /min %PYTHON% -m uvicorn api.main:app --host 127.0.0.1 --port 8080 --log-level warning
echo       ^（首次启动需加载感知/视觉/embedding，可能需 1~2 分钟）
set /a tries=0
:wait_backend
set /a tries+=1
if !tries! GEQ 120 goto backend_timeout
timeout /t 1 /nobreak >nul
curl -sf http://127.0.0.1:8080/health >nul 2>&1
if errorlevel 1 goto wait_backend
echo [OK] 后端已启动 ^(!tries!s^)
goto backend_ok

:backend_already
echo [OK] 后端服务已在运行
goto backend_ok

:backend_timeout
echo [ERR] 后端启动超时 ^(120s^)。若视觉模型加载过慢，可在 ~\.cortex\settings.json 设 VISION_BACKEND=mock
pause
exit /b 1

:backend_ok
REM ---- 启动前端代理 :8765 ----
echo [..] 启动前端服务 ^(server.py^)...
start "cortex-front" /min %PYTHON% "%~dp0server.py"
timeout /t 1 /nobreak >nul

REM ---- 启动 Qt 桌面客户端 ----
echo [..] 打开窗口...
%PYTHON% "%~dp0main.py"

endlocal
