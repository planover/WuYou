param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendRoot = Join-Path $RepoRoot "backend"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "未找到虚拟环境，正在创建 .venv..." -ForegroundColor Yellow
    python -m venv (Join-Path $RepoRoot ".venv")
}

$env:UNIBOX_ENVIRONMENT = "development"
$env:UNIBOX_DATA_DIR = Join-Path $BackendRoot "app\data"

Set-Location $BackendRoot
Write-Host "UniBox 正在启动: http://127.0.0.1:$Port" -ForegroundColor Cyan
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
