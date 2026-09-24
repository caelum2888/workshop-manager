# Inicia o Oficinas Manager para uso diário no computador do trabalho.
# Não é o fluxo de desenvolvimento: sem --reload, sem docs interativas fora do necessário.
# Chamado por iniciar.bat (duplo clique). Pode ser rodado direto também:
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($msg) {
    Write-Host ""
    Write-Host "ERRO: $msg" -ForegroundColor Red
    exit 1
}

Write-Host "=== Oficinas Manager ===" -ForegroundColor Cyan
Write-Host "Pasta do projeto: $root"
Write-Host ""

# 2. Python compatível ------------------------------------------------------
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Fail "Python não encontrado. Instale o Python 3.12 em https://www.python.org/downloads/ marcando 'Add python.exe to PATH', depois rode iniciar.bat de novo."
}
$verOutput = (& python --version 2>&1) -join " "
if ($verOutput -notmatch "Python 3\.(1[1-9]|[2-9]\d)") {
    Fail "Python 3.11+ é necessário (encontrado: $verOutput)."
}

# 3. .venv --------------------------------------------------------------
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Criando ambiente virtual (.venv)..."
    python -m venv (Join-Path $root ".venv")
    if (-not (Test-Path $venvPython)) { Fail "Não foi possível criar a .venv." }
}

# 4. dependências (só reinstala se requirements.txt mudou desde a última vez) ----
$reqFile = Join-Path $root "requirements.txt"
$reqHashFile = Join-Path $root ".venv\requirements.sha256"
$currentHash = (Get-FileHash $reqFile -Algorithm SHA256).Hash
$installedHash = if (Test-Path $reqHashFile) { (Get-Content $reqHashFile -Raw).Trim() } else { "" }
if ($currentHash -ne $installedHash) {
    Write-Host "Instalando dependências (primeira vez ou requirements.txt mudou)..."
    & $venvPython -m pip install --quiet --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fail "Falha ao atualizar o pip." }
    & $venvPython -m pip install --quiet -r $reqFile
    if ($LASTEXITCODE -ne 0) { Fail "Falha ao instalar dependências. Veja o erro acima." }
    Set-Content -Path $reqHashFile -Value $currentHash -NoNewline
    Write-Host "  Dependências instaladas."
}

# 5. .env local seguro --------------------------------------------------
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "Criando .env local com uma chave de segurança gerada..."
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = [Convert]::ToBase64String($bytes) -replace '[+/=]', ''
    @"
APP_ENV=development
SECRET_KEY=$secret
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_NAME=oficinas_session
DATABASE_URL=
LLM_API_KEY=
LLM_MODEL=claude-opus-5
"@ | Set-Content -Path $envFile -Encoding utf8
    Write-Host "  .env criado. A chave gerada nunca é exibida nem sai deste computador."
}
$envContent = Get-Content $envFile -Raw
$secretMatch = [regex]::Match($envContent, '(?m)^SECRET_KEY=(.*)$')
$secretLength = if ($secretMatch.Success) { $secretMatch.Groups[1].Value.Trim().Length } else { 0 }
$secureSession = $secretLength -ge 32   # só o comprimento é usado; o valor nunca é lido/mostrado

# 6. diretório de dados ---------------------------------------------------
$dataDir = Join-Path $root "data"
if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }

# 7-8. backup pré-migration + migration -----------------------------------
$dbPath = Join-Path $dataDir "oficinas.db"
if (Test-Path $dbPath) {
    Write-Host "Fazendo backup de segurança antes de verificar o banco..."
    & $venvPython (Join-Path $root "scripts\db_backup.py") --prefix pre-migracao
    if ($LASTEXITCODE -ne 0) {
        Fail "O backup de segurança falhou; a inicialização foi interrompida para não arriscar o banco de dados. Veja o erro acima."
    }
}
Write-Host "Verificando/aplicando migrations..."
& $venvPython -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { Fail "Falha ao aplicar as migrations do banco. Veja o erro acima." }

# 9. confirma que existe admin ---------------------------------------------
& $venvPython (Join-Path $root "scripts\check_admin.py") | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Nenhum administrador encontrado." -ForegroundColor Yellow
    $answer = Read-Host "Criar o primeiro administrador agora? (S/N)"
    if ($answer -match '^[sS]') {
        $adminName = Read-Host "Nome do administrador"
        $adminEmail = Read-Host "E-mail do administrador"
        & $venvPython (Join-Path $root "scripts\create_admin.py") --name $adminName --email $adminEmail
        if ($LASTEXITCODE -ne 0) { Fail "Não foi possível criar o administrador. Veja o erro acima." }
    } else {
        Write-Host "Você pode criar depois com:"
        Write-Host '  .venv\Scripts\python.exe scripts\create_admin.py --name "Nome" --email email@exemplo.org'
    }
}

# 10-11. porta --------------------------------------------------------------
$port = 8000
while (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    if ($port -eq 8000) { Write-Host ""; Write-Host "A porta $port já está em uso." -ForegroundColor Yellow }
    $port++
}
if ($port -ne 8000) { Write-Host "Usando a porta $port em vez disso." }

# host / rede local ----------------------------------------------------
$localIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -notmatch 'Loopback' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1).IPAddress
if ($localIp -and -not $secureSession) {
    Write-Host ""
    Write-Host "A configuração de sessão não é segura o suficiente para expor na rede local; iniciando apenas em 127.0.0.1." -ForegroundColor Yellow
    $localIp = $null
}
$hostBind = if ($localIp) { "0.0.0.0" } else { "127.0.0.1" }

# logs ------------------------------------------------------------------
$logsDir = Join-Path $root "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$logFile = Join-Path $logsDir ("oficinas-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

Write-Host ""
Write-Host "Sistema iniciado." -ForegroundColor Green
Write-Host ""
Write-Host "Computador:"
Write-Host "  http://127.0.0.1:$port"
if ($localIp) {
    Write-Host ""
    Write-Host "Rede local (outros dispositivos na mesma rede Wi-Fi):"
    Write-Host "  http://$localIp`:$port"
}
Write-Host ""
Write-Host "Deixe esta janela aberta enquanto usa o sistema. Feche-a (ou Ctrl+C) para encerrar."
Write-Host "Log desta sessão: $logFile"
Write-Host ""

# A partir daqui só roda um processo nativo de longa duração: os logs normais
# do uvicorn vão para stderr, e com ErrorActionPreference=Stop cada linha
# viraria um erro terminando o servidor assim que ele acabasse de subir.
$ErrorActionPreference = "Continue"
& $venvPython -m uvicorn app.main:app --host $hostBind --port $port 2>&1 | Tee-Object -FilePath $logFile -Append
exit $LASTEXITCODE
