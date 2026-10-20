param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('worker', 'gateway')]
    [string]$Component,
    [string]$Config
)

. (Join-Path $PSScriptRoot 'environment.ps1') -Config $Config
if ($Component -eq 'worker') {
    Set-Location $taskRepo
    & $env:EEGAGENT_PYTHON -m uvicorn eegagent.worker.app:app --host $taskWorkerUri.Host --port $taskWorkerUri.Port --app-dir (Join-Path $taskRepo 'src')
} else {
    Build-EFAgentUI
    . (Join-Path $PSScriptRoot 'environment.ps1')
    $taskBinary = Build-EFAgentGateway
    Publish-EFAgentLocalUI
    Set-Location (Join-Path $taskRepo 'gateway')
    & $taskBinary
}
exit $LASTEXITCODE
