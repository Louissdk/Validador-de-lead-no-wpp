[CmdletBinding()]
param(
    [string]$Source = (Join-Path $HOME "Downloads\robo python\visivel-kael-python")
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$syncScript = Join-Path $PSScriptRoot "sync_from_local.py"

if (-not (Test-Path -LiteralPath $syncScript -PathType Leaf)) {
    throw "Sincronizador não encontrado: $syncScript"
}

if (-not (Test-Path -LiteralPath (Join-Path $sourcePath "app\lead_worker.py") -PathType Leaf)) {
    throw "Origem inválida: app\lead_worker.py não encontrado em $sourcePath"
}

$repoRootRaw = & git -C $PSScriptRoot rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($repoRootRaw -join ""))) {
    throw "A pasta do sincronizador não está em um clone Git válido."
}
$repoRoot = ($repoRootRaw -join "").Trim()

$remoteRaw = & git -C $repoRoot remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Não foi possível verificar o remote origin do repositório."
}
$remote = ($remoteRaw -join "").Trim()
if ($remote -notmatch '(?i)github\.com[:/]Louissdk/Validador-de-lead-no-wpp(?:\.git)?$') {
    throw "O remote origin não corresponde ao repositório esperado do KAEL. Nenhum arquivo foi publicado."
}

$logDir = Join-Path $env:LOCALAPPDATA "KAEL\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ("sync_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

Add-Content -LiteralPath $logPath -Encoding UTF8 -Value ([Environment]::NewLine + "===== Início da sincronização $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====")
Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "Origem: $sourcePath"
Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "Repositório: $repoRoot"

# O sincronizador só publica depois de executar pytest com sucesso.
# stdout e stderr vão diretamente para o log para evitar interrupções do PowerShell.
& py -3 -u $syncScript --source $sourcePath --commit >> $logPath 2>&1
$syncExitCode = $LASTEXITCODE

Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "Código de saída: $syncExitCode"
Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "===== Fim da sincronização $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="

if ($syncExitCode -ne 0) {
    Write-Host "ERRO: sincronização falhou com código $syncExitCode. Veja o log: $logPath" -ForegroundColor Red
    exit $syncExitCode
}

Write-Host "Sincronização finalizada. Log: $logPath"
exit 0
