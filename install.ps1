#Requires -Version 5.1
<#
.SYNOPSIS
    Cortex Agent Windows 一键安装脚本
.DESCRIPTION
    自动克隆仓库、检查环境、安装依赖
.PARAMETER InstallDir
    安装目录（默认：$HOME\cortex_agent）
.PARAMETER Branch
    分支（默认：main）
.EXAMPLE
    powershell -ExecutionPolicy Bypass -Command "iex (New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/15087312/cortex_agent/main/install.ps1')"
#>

param(
    [string]$InstallDir = "$HOME\cortex_agent",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/15087312/cortex_agent.git"

function Write-Info { Write-Host "[INFO] $args" -ForegroundColor Cyan }
function Write-OK { Write-Host "[✓] $args" -ForegroundColor Green }
function Write-Warn { Write-Host "[!] $args" -ForegroundColor Yellow }
function Write-Err { Write-Host "[✗] $args" -ForegroundColor Red }

# ── 前置检查 ──
function Check-Prerequisites {
    Write-Info "检查系统环境..."

    # Git
    try {
        $gitVer = git --version 2>&1 | Select-String -Pattern "[0-9]+\.[0-9]+\.[0-9]+" -OutVariable match | ForEach-Object { $match[0].Matches[0].Value }
        Write-OK "git $gitVer"
    } catch {
        Write-Err "未找到 git，请先安装: https://git-scm.com/download/win"
        exit 1
    }

    # Python
    $python = $null
    foreach ($cmd in @("python3", "python")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "(\d+)\.(\d+)") {
                $major = [int]$matches[1]
                $minor = [int]$matches[2]
                if ($major -ge 3 -and $minor -ge 11) {
                    $script:python = $cmd
                    Write-OK "python $($matches[0])"
                    return
                }
            }
        } catch {
            continue
        }
    }

    Write-Err "需要 Python 3.11+，请先安装: https://www.python.org/downloads/"
    exit 1
}

# ── 克隆 / 更新 ──
function Clone-Or-Update {
    if (Test-Path "$InstallDir\.git") {
        Write-Info "检测到已有安装: $InstallDir"
        Write-Info "更新到最新版本..."
        Push-Location $InstallDir
        git fetch origin $Branch 2>&1 | Out-Null
        git checkout $Branch 2>&1 | Out-Null
        git reset --hard "origin/$Branch" 2>&1 | Out-Null
        Pop-Location
        Write-OK "已更新"
    } else {
        Write-Info "克隆仓库到 $InstallDir ..."
        git clone --branch $Branch --depth 1 $RepoUrl $InstallDir 2>&1 | Out-Null
        Write-OK "克隆完成"
    }
    Push-Location $InstallDir
}

# ── 安装依赖 ──
function Install-Dependencies {
    Write-Info "安装 Python 依赖..."
    & $script:python -m pip install -e . --quiet 2>&1 | Select-Object -Last 1
    Write-OK "依赖安装完成"
}

# ── 配置 .env ──
function Setup-Env {
    if (Test-Path ".env") {
        Write-OK ".env 已存在，跳过配置"
        return
    }

    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-OK "已创建 .env（从 .env.example 复制）"
    } else {
        New-Item ".env" -ItemType File -Force | Out-Null
        Write-OK "已创建空 .env"
    }

    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  配置模型 API Key" -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  支持的模型服务:"
    Write-Host "    • DeepSeek  — https://platform.deepseek.com"
    Write-Host "    • OpenAI    — https://platform.openai.com"
    Write-Host "    • 兼容 OpenAI 格式的任何服务"
    Write-Host ""

    $apiKey = Read-Host "  请输入 API Key（直接回车跳过）"
    if ($apiKey) {
        $content = Get-Content ".env"
        $content = $content -replace '^LARGE_MODEL_API_KEY=.*', "LARGE_MODEL_API_KEY=$apiKey"
        Set-Content ".env" $content
        Write-OK "API Key 已写入 .env"
    } else {
        Write-Warn "跳过配置，请稍后编辑 .env"
    }
}

# ── 完成 ──
function Print-Done {
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host "  ✓ Cortex Agent 安装完成！" -ForegroundColor Green
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host ""
    Write-Host "  启动方式:"
    Write-Host ""
    Write-Host "    cortex                         # 启动后端 + 交互终端" -ForegroundColor Cyan
    Write-Host "    cortex --no-tui                # 只启动后端 API 服务" -ForegroundColor Cyan
    Write-Host "    cortex --port 9000             # 指定端口" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  配置文件: $(Get-Location)\.env"
    Write-Host "  更新命令: cd $InstallDir && git pull && pip install -e ."
    Write-Host ""

    # 检查 cortex 是否在 PATH 中
    try {
        $null = cortex --version 2>&1
    } catch {
        Write-Warn "cortex 命令未在 PATH 中"
        Write-Host ""
        Write-Host "  解决方案:"
        Write-Host "    • 重启 PowerShell，pip 会自动添加到 PATH"
        Write-Host "    • 或使用完整路径: $script:python -m cortex.main"
        Write-Host ""
    }
}

# ── 主流程 ──
Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Cortex Agent 安装程序                ║" -ForegroundColor Cyan
Write-Host "║     类人智能后端系统                      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

try {
    Check-Prerequisites
    Clone-Or-Update
    Install-Dependencies
    Setup-Env
    Print-Done
} catch {
    Write-Err "安装失败: $_"
    exit 1
} finally {
    Pop-Location -ErrorAction SilentlyContinue
}
