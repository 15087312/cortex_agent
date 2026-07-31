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

:: 检查语音依赖（热键 Push-to-Talk / Whisper STT / TTS），没有就自动装
python -c "import pynput, pyaudio, gTTS, speech_recognition" 2>nul
if %ERRORLEVEL% neq 0 (
    echo [..] 正在安装语音依赖（首次运行需要）...
    pip install pynput pyaudio gTTS SpeechRecognition -q
    if %ERRORLEVEL% neq 0 (
        echo [WARN] 语音依赖安装失败，语音功能不可用（可手动运行: pip install pynput pyaudio gTTS SpeechRecognition）
    ) else (
        echo [OK] 语音依赖安装完成
    )
)

:: 检查 Whisper STT 引擎（本地识别，依赖 torch，体积较大）
python -c "import whisper" 2>nul
if %ERRORLEVEL% neq 0 (
    echo [..] 正在安装 Whisper 语音识别引擎（首次运行需要，约 2GB）...
    pip install openai-whisper -q
    if %ERRORLEVEL% neq 0 (
        echo [WARN] Whisper 安装失败，语音识别不可用（可手动运行: pip install openai-whisper）
    ) else (
        echo [OK] Whisper 安装完成
    )
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
