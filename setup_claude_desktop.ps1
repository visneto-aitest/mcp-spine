# MCP Spine - Claude Desktop Setup Script
# Run: powershell -ExecutionPolicy Bypass -File setup_claude_desktop.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  MCP Spine - Claude Desktop Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Find the config file ---
Write-Host "[1/3] Finding claude_desktop_config.json..." -ForegroundColor Yellow

$configPath = $null

# Check standard path first (direct .exe installer)
$standardPath = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
$standardDir = Join-Path $env:APPDATA "Claude"

# Check MSIX path (Microsoft Store / WinGet)
$msixBase = Join-Path $env:LOCALAPPDATA "Packages"
$msixPath = $null
if (Test-Path $msixBase) {
    $msixDirs = Get-ChildItem -Path $msixBase -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue
    foreach ($dir in $msixDirs) {
        $candidate = Join-Path $dir.FullName "LocalCache\Roaming\Claude\claude_desktop_config.json"
        $candidateDir = Join-Path $dir.FullName "LocalCache\Roaming\Claude"
        if (Test-Path $candidateDir) {
            $msixPath = $candidate
            break
        }
    }
}

# Pick the right one
if ($msixPath -and (Test-Path (Split-Path $msixPath))) {
    $configPath = $msixPath
    Write-Host "  Found MSIX install: $configPath" -ForegroundColor Green
} elseif (Test-Path $standardDir) {
    $configPath = $standardPath
    Write-Host "  Found standard install: $configPath" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Claude Desktop config directory not found." -ForegroundColor Red
    Write-Host "  Launch Claude Desktop at least once, then re-run this script." -ForegroundColor Red
    exit 1
}

# --- Step 2: Build paths ---
Write-Host ""
Write-Host "[2/3] Resolving paths..." -ForegroundColor Yellow

$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    Write-Host "  ERROR: Python not found on PATH" -ForegroundColor Red
    exit 1
}
Write-Host "  Python: $pythonPath" -ForegroundColor Green

$projectDir = (Get-Location).Path
$spineToml = Join-Path $projectDir "spine_test.toml"
if (-not (Test-Path $spineToml)) {
    Write-Host "  ERROR: spine_test.toml not found in current directory." -ForegroundColor Red
    Write-Host "  Run this from: cd 'C:\Users\PC\Desktop\MCP (The Spine)'" -ForegroundColor Gray
    exit 1
}
Write-Host "  Project: $projectDir" -ForegroundColor Green

# --- Step 3: Write config ---
Write-Host ""
Write-Host "[3/3] Writing config..." -ForegroundColor Yellow

# Escape backslashes for JSON strings
$pyJson = $pythonPath.Replace('\', '\\')
$tomlJson = $spineToml.Replace('\', '\\')
$cwdJson = $projectDir.Replace('\', '\\')

# Backup existing config
if (Test-Path $configPath) {
    $backupPath = "$configPath.backup"
    Copy-Item $configPath $backupPath -Force
    Write-Host "  Backed up existing config to: $backupPath" -ForegroundColor Gray
}

# Ensure directory exists
$configDir = Split-Path -Parent $configPath
if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
}

# Write clean JSON directly (no ConvertTo-Json quirks)
$json = @"
{
  "mcpServers": {
    "spine": {
      "command": "$pyJson",
      "args": ["-m", "spine.cli", "serve", "--config", "$tomlJson"],
      "cwd": "$cwdJson"
    }
  }
}
"@

[System.IO.File]::WriteAllText($configPath, $json, [System.Text.Encoding]::UTF8)

Write-Host "  Written to: $configPath" -ForegroundColor Green
Write-Host ""
Write-Host "  Config contents:" -ForegroundColor Cyan
Write-Host $json
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Next steps:" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  1. Quit Claude Desktop completely" -ForegroundColor White
Write-Host "     (System tray > right-click > Quit)" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Restart Claude Desktop" -ForegroundColor White
Write-Host ""
Write-Host "  3. Look for the tools icon in the chat input" -ForegroundColor White
Write-Host ""
Write-Host "  4. Try asking Claude:" -ForegroundColor White
Write-Host '     "What files are in my project directory?"' -ForegroundColor Cyan
Write-Host ""
