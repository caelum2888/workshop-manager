# Restaura um backup do banco SQLite. Exige confirmação explícita e preserva
# o banco atual (com sufixo .antes-de-restaurar-<data>) antes de sobrescrever.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$backupsDir = Join-Path $root "backups"

if (-not (Test-Path $py)) {
    Write-Host "Ambiente virtual não encontrado em $py." -ForegroundColor Red
    Write-Host "Rode iniciar.bat primeiro (ele cria a .venv)."
    exit 1
}
if (-not (Test-Path $backupsDir)) {
    Write-Host "Nenhum backup encontrado em $backupsDir."
    exit 1
}
$backups = Get-ChildItem -Path $backupsDir -Filter "*.db" | Sort-Object LastWriteTime -Descending
if ($backups.Count -eq 0) {
    Write-Host "Nenhum backup encontrado em $backupsDir."
    exit 1
}

Write-Host ""
Write-Host "=== Restauração de backup ===" -ForegroundColor Cyan
Write-Host "IMPORTANTE: pare o servidor (feche a janela do iniciar.bat) antes de continuar." -ForegroundColor Yellow
Write-Host ""
for ($i = 0; $i -lt $backups.Count; $i++) {
    Write-Host ("  [{0}] {1}   ({2:yyyy-MM-dd HH:mm})" -f ($i + 1), $backups[$i].Name, $backups[$i].LastWriteTime)
}
Write-Host ""
$choice = Read-Host "Digite o número do backup a restaurar (ou Enter para cancelar)"
if ([string]::IsNullOrWhiteSpace($choice)) { Write-Host "Cancelado."; exit 0 }

$index = 0
if (-not [int]::TryParse($choice, [ref]$index) -or $index -lt 1 -or $index -gt $backups.Count) {
    Write-Host "Opção inválida." -ForegroundColor Red
    exit 1
}
$selected = $backups[$index - 1]

Write-Host ""
Write-Host "Isso vai substituir o banco atual pelo conteúdo de: $($selected.Name)"
Write-Host "O banco atual será preservado com sufixo '.antes-de-restaurar-<data>' antes da troca."
$confirm = Read-Host "Para confirmar, digite RESTAURAR"
if ($confirm -ne "RESTAURAR") { Write-Host "Cancelado."; exit 0 }

& $py (Join-Path $root "scripts\db_restore.py") $selected.FullName
exit $LASTEXITCODE
