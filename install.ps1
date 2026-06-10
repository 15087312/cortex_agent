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
        Write-Info "Updating to latest version..."
        Push-Location $InstallDir
        try {
            git fetch origin $Branch 2>&1 | Out-Null
            git checkout $Branch 2>&1 | Out-Null
            git reset --hard "origin/$Branch" 2>&1 | Out-Null
            Write-OK "Updated"
        } catch {
            Write-Err "Update failed: $_"
            exit 1
        }
        return
    }

    Write-Info "Cloning repository to $InstallDir ..."
    try {
        git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
        Write-OK "Clone completed"
    } catch {
        Write-Err "Clone failed: $_"
        Write-Err "Check your internet connection or try manually:"
        Write-Host "  git clone --branch $Branch $RepoUrl $InstallDir"
        exit 1
    }

    Push-Location $InstallDir
}

function Install-Dependencies {
    Write-Info "Installing Python dependencies..."
    & $script:python -m pip install -e . --quiet 2>&1 | Select-Object -Last 1
    Write-OK "Dependencies installed"
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
    Write-Host "    - DeepSeek  https://platform.deepseek.com"
    Write-Host "    - OpenAI    https://platform.openai.com"
    Write-Host "    - Compatible OpenAI-format services"
    Write-Host ""

    $apiKey = Read-Host "  Enter API Key (press Enter to skip)"
    if ($apiKey) {
        $content = Get-Content ".env"
        $content = $content -replace '^LARGE_MODEL_API_KEY=.*', "LARGE_MODEL_API_KEY=$apiKey"
        Set-Content ".env" $content
        Write-OK "API Key saved to .env"
    } else {
        Write-Warn "Skipped, edit .env manually later"
    }
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
    Setup-Env
    Print-Done
} catch {
    Write-Err "Installation failed: $_"
    exit 1
} finally {
    Pop-Location -ErrorAction SilentlyContinue
}
