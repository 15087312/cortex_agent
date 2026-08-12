#Requires -Version 5.1
<#
.SYNOPSIS
    Cortex Agent Windows Installation Script
.DESCRIPTION
    Automatic repository cloning, environment checking, and dependency installation
.PARAMETER InstallDir
    Installation directory (default: $HOME\cortex_agent)
.PARAMETER Branch
    Git branch (default: main)
.EXAMPLE
    iex (New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/15087312/cortex_agent/main/install.ps1')
#>

param(
    [string]$InstallDir = "$HOME\cortex_agent",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/15087312/cortex_agent.git"

function Write-Info { Write-Host "[INFO] $args" -ForegroundColor Cyan }
function Write-OK { Write-Host "[OK] $args" -ForegroundColor Green }
function Write-Warn { Write-Host "[!] $args" -ForegroundColor Yellow }
function Write-Err { Write-Host "[ERROR] $args" -ForegroundColor Red }

function Check-Prerequisites {
    Write-Info "Checking system environment..."

    try {
        $gitVer = git --version 2>&1 | Select-String -Pattern "[0-9]+\.[0-9]+\.[0-9]+" -OutVariable match | ForEach-Object { $match[0].Matches[0].Value }
        Write-OK "git $gitVer"
    } catch {
        Write-Err "Git not found. Please install from https://git-scm.com/download/win"
        exit 1
    }

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

    Write-Err "Python 3.11+ required. Please install from https://www.python.org/downloads/"
    exit 1
}

function Clone-Or-Update {
    if (Test-Path "$InstallDir\.git") {
        Write-Info "Found existing installation: $InstallDir"
        Write-Info "Removing old version..."
        try {
            Remove-Item -Recurse -Force $InstallDir
            Write-OK "Removed"
        } catch {
            Write-Err "Failed to remove old installation: $_"
            exit 1
        }
    }

    Write-Info "Cloning repository to $InstallDir ..."
    try {
        git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
        Write-OK "Clone completed"
    } catch {
        Write-Err "Clone failed: $_"
        exit 1
    }

    Push-Location $InstallDir
}

function Install-Dependencies {
    Write-Info "Installing Python dependencies..."
    & $script:python -m pip install -e . --quiet 2>&1 | Select-Object -Last 1
    Write-Info "Installing Qt desktop dependencies (PyQt6 + WebEngine)..."
    & $script:python -m pip install PyQt6 PyQt6-WebEngine --quiet 2>&1 | Select-Object -Last 1
    Write-OK "Dependencies installed"
}

function Build-Frontend {
    if (Test-Path "frontend\dist\index.html") {
        Write-OK "frontend/dist already built, skipping"
        return
    }
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Info "Building Vue frontend (npm run build)..."
        Push-Location "frontend"
        try {
            & npm run build 2>&1 | Select-Object -Last 1
            if (Test-Path "dist\index.html") {
                Write-OK "Frontend built"
            } else {
                Write-Warn "npm build failed, run manually: cd frontend && npm run build"
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Warn "npm not found, frontend not built (browser UI unavailable). Install Node.js: https://nodejs.org"
    }
}

function Setup-OmniParser {
    if (Test-Path "OmniParser/README.md") {
        Write-OK "OmniParser already exists, skipping"
        return
    }
    Write-Info "Downloading OmniParser UI detection model (~500MB)..."
    git clone --depth 1 https://github.com/microsoft/OmniParser.git OmniParser 2>&1 | Select-Object -Last 1
    if (Test-Path "OmniParser") {
        Write-OK "OmniParser downloaded"
        Write-Warn "OmniParser weights need separate download, see OmniParser/README.md"
    } else {
        Write-Warn "OmniParser download failed"
    }
}

function Replace-Env {
    param([string]$Key, [string]$Value)
    if ($Value) {
        $content = Get-Content ".env" -Raw
        $content = [regex]::Replace($content, "(?m)^$Key=.*", "$Key=$Value")
        Set-Content ".env" $content -NoNewline
    }
}

function Setup-Env {
    if (Test-Path ".env") {
        Write-OK ".env already exists, skipping"
        return
    }

    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-OK "Created .env from template"
    } else {
        New-Item ".env" -ItemType File -Force | Out-Null
        Write-OK "Created empty .env"
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Configure Model API Key" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Supported services:"
    Write-Host "    - DeepSeek   https://platform.deepseek.com"
    Write-Host "    - OpenAI     https://platform.openai.com"
    Write-Host "    - ModelScope https://modelscope.cn"
    Write-Host "    - Groq / Mistral / Any OpenAI-compatible service"
    Write-Host ""
    Write-Host "  Configure 3 tiers (large/medium/small), 9 values total."
    Write-Host "  Press Enter to skip, edit .env later."
    Write-Host ""

    Write-Host "  [1/3] Large model (orchestrator)" -ForegroundColor Cyan
    $largeKey = Read-Host "    API Key"
    $largeUrl = Read-Host "    API URL (Enter=https://api.deepseek.com/v1/chat/completions)"
    if (-not $largeUrl) { $largeUrl = "https://api.deepseek.com/v1/chat/completions" }
    $largeName = Read-Host "    Model name (Enter=deepseek-v4-flash)"
    if (-not $largeName) { $largeName = "deepseek-v4-flash" }
    Replace-Env "LARGE_MODEL_API_KEY" $largeKey
    Replace-Env "LARGE_MODEL_API_URL" $largeUrl
    Replace-Env "LARGE_MODEL_NAME" $largeName

    Write-Host ""
    Write-Host "  [2/3] Medium model (reasoning)" -ForegroundColor Cyan
    $mediumKey = Read-Host "    API Key (Enter=use large model)"
    if (-not $mediumKey) { $mediumKey = $largeKey }
    $mediumUrl = Read-Host "    API URL (Enter=use large model)"
    if (-not $mediumUrl) { $mediumUrl = $largeUrl }
    $mediumName = Read-Host "    Model name (Enter=use large model)"
    if (-not $mediumName) { $mediumName = $largeName }
    Replace-Env "MEDIUM_MODEL_API_KEY" $mediumKey
    Replace-Env "MEDIUM_MODEL_API_URL" $mediumUrl
    Replace-Env "MEDIUM_MODEL_NAME" $mediumName

    Write-Host ""
    Write-Host "  [3/3] Small model (lightweight)" -ForegroundColor Cyan
    $smallKey = Read-Host "    API Key (Enter=use large model)"
    if (-not $smallKey) { $smallKey = $largeKey }
    $smallUrl = Read-Host "    API URL (Enter=use large model)"
    if (-not $smallUrl) { $smallUrl = $largeUrl }
    $smallName = Read-Host "    Model name (Enter=use large model)"
    if (-not $smallName) { $smallName = $largeName }
    Replace-Env "SMALL_MODEL_API_KEY" $smallKey
    Replace-Env "SMALL_MODEL_API_URL" $smallUrl
    Replace-Env "SMALL_MODEL_NAME" $smallName

    Write-Host ""
    Write-OK "Model config saved to .env"
}

function Print-Done {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Installation Complete!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Usage:"
    Write-Host ""
    Write-Host "    cortex                         Start backend + interactive terminal" -ForegroundColor Cyan
    Write-Host "    cortex --no-tui                Start backend only (API mode)" -ForegroundColor Cyan
    Write-Host "    cortex --port 9000             Specify port" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Config file: $(Get-Location)\.env"
    Write-Host "  Update: cd $InstallDir && git pull && pip install -e ."
    Write-Host ""

    try {
        $null = cortex --version 2>&1
    } catch {
        Write-Warn "cortex command not in PATH"
        Write-Host ""
        Write-Host "  Solution:"
        Write-Host "    - Restart PowerShell (pip adds to PATH automatically)"
        Write-Host "    - Or use: $script:python -m cortex.main"
        Write-Host ""
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Cortex Agent Installer" -ForegroundColor Cyan
Write-Host "  Humanoid Intelligence Backend" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

try {
    Check-Prerequisites
    Clone-Or-Update
    Install-Dependencies
    Build-Frontend
    Setup-OmniParser
    Setup-Env
    Print-Done
} catch {
    Write-Err "Installation failed: $_"
    exit 1
} finally {
    Pop-Location -ErrorAction SilentlyContinue
}
