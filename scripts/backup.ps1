# Backup consistente do banco SQLite local (usa a API de backup do sqlite3, via Python).
# Uso:
#   .\scripts\backup.ps1                     # backups\oficinas-AAAAMMDD-HHMMSS.db
#   .\scripts\backup.ps1 -Prefix pre-migracao   # backups\pre-migracao-AAAAMMDD-HHMMSS.db
param(
    [string]$Prefix
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Ambiente virtual não encontrado em $py." -ForegroundColor Red
    Write-Host "Rode iniciar.bat primeiro (ele cria a .venv), ou crie manualmente com: python -m venv .venv"
    exit 1
}

$scriptArgs = @((Join-Path $root "scripts\db_backup.py"))
if ($Prefix) { $scriptArgs += @("--prefix", $Prefix) }

& $py @scriptArgs
exit $LASTEXITCODE
