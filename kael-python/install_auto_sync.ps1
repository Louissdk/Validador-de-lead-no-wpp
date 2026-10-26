[CmdletBinding()]
param(
    [string]$Source = (Join-Path $HOME "Downloads\robo python\visivel-kael-python")
)

$ErrorActionPreference = "Stop"

$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$syncScript = Join-Path $PSScriptRoot "auto_sync.ps1"

if (-not (Test-Path -LiteralPath $syncScript -PathType Leaf)) {
    throw "Arquivo auto_sync.ps1 não encontrado: $syncScript"
}
if (-not (Test-Path -LiteralPath (Join-Path $sourcePath "app\lead_worker.py") -PathType Leaf)) {
    throw "Origem inválida: app\lead_worker.py não encontrado em $sourcePath"
}
if (-not (Test-Path -LiteralPath (Join-Path $sourcePath "tests\test_worker_staged_promotion.py") -PathType Leaf)) {
    throw "Origem inválida: teste de promoção/staging não encontrado."
}

$repoRootRaw = & git -C $PSScriptRoot rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "A pasta do instalador não está em um clone Git válido."
}
$repoRoot = ($repoRootRaw -join "").Trim()

$remoteRaw = & git -C $repoRoot remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Não foi possível verificar o remote origin."
}
$remote = ($remoteRaw -join "").Trim()
if ($remote -notmatch '(?i)github\.com[:/]Louissdk/Validador-de-lead-no-wpp(?:\.git)?$') {
    throw "Remote origin incorreto. A tarefa automática não foi instalada."
}

$taskName = "KAEL GitHub Auto Sync"
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$startBoundary = (Get-Date).AddMinutes(2).ToString("yyyy-MM-ddTHH:mm:ss")
$powershellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

# Quotes são mantidas nos argumentos e escapadas para XML.
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    $syncScript + '" -Source "' + $sourcePath + '"'

$xmlPowershell = [System.Security.SecurityElement]::Escape($powershellExe)
$xmlArguments = [System.Security.SecurityElement]::Escape($arguments)
$xmlWorkingDirectory = [System.Security.SecurityElement]::Escape($repoRoot)
$xmlSid = [System.Security.SecurityElement]::Escape($currentSid)

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Sincroniza o código KAEL para o GitHub a cada 30 minutos. Publica apenas após os testes passarem; não sincroniza credenciais, banco de dados ou dados de leads.</Description>
    <URI>\KAEL GitHub Auto Sync</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT30M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$xmlSid</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT20M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$xmlPowershell</Command>
      <Arguments>$xmlArguments</Arguments>
      <WorkingDirectory>$xmlWorkingDirectory</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName $taskName -Xml $taskXml -Force | Out-Null

Write-Host ""
Write-Host "Tarefa '$taskName' instalada." -ForegroundColor Green
Write-Host "Origem sincronizada: $sourcePath"
Write-Host "Frequência: a cada 30 minutos, enquanto sua sessão do Windows estiver aberta."
Write-Host "A publicação só ocorre se o pytest passar e o filtro de arquivos permitidos aprovar a sincronização."
Write-Host "Logs: $env:LOCALAPPDATA\KAEL\logs"
Write-Host ""
Write-Host "Verificar tarefa: Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Host ('Remover tarefa: Unregister-ScheduledTask -TaskName "{0}" -Confirm:$false' -f $taskName)
