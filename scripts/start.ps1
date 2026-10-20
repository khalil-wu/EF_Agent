param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path $PSScriptRoot -Parent
$taskEntry = Join-Path $PSScriptRoot 'serve.ps1'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'serve.local.ps1')) {
    $taskEntry = Join-Path $PSScriptRoot 'serve.local.ps1'
}
$taskShell = (Get-Process -Id $PID).Path
$taskLogs = Join-Path $taskRepo 'storage/logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskWorkerUrl = 'http://127.0.0.1:8100'
$taskGatewayUrl = 'http://127.0.0.1:8080'
if ($env:EEGAGENT_WORKER_URL) { $taskWorkerUrl = $env:EEGAGENT_WORKER_URL }
if ($env:EFAGENT_WORKER_URL) { $taskWorkerUrl = $env:EFAGENT_WORKER_URL }
if ($env:EEGAGENT_ADDR) { $taskGatewayUrl = 'http://' + $env:EEGAGENT_ADDR }
if ($env:EFAGENT_ADDR) { $taskGatewayUrl = 'http://' + $env:EFAGENT_ADDR }

function Get-ServiceHealth([string]$Url) {
    $taskRequest = [System.Net.WebRequest]::Create($Url.TrimEnd('/') + '/health')
    $taskRequest.Proxy = $null
    $taskRequest.Timeout = 2000
    try {
        $taskResponse = $taskRequest.GetResponse()
    } catch [System.Net.WebException] {
        if ($_.Exception.Status -in @([System.Net.WebExceptionStatus]::ConnectFailure, [System.Net.WebExceptionStatus]::Timeout)) { return $null }
        throw
    }
    $taskReader = [System.IO.StreamReader]::new($taskResponse.GetResponseStream())
    $taskBody = $taskReader.ReadToEnd()
    $taskReader.Close()
    $taskResponse.Close()
    return ($taskBody | ConvertFrom-Json)
}

foreach ($taskService in @(
    @{ Component = 'worker'; Url = $taskWorkerUrl },
    @{ Component = 'gateway'; Url = $taskGatewayUrl }
)) {
    $taskHealth = Get-ServiceHealth $taskService.Url
    if ($taskHealth.status -eq 'ok') { continue }
    if ($null -ne $taskHealth) { throw "$($taskService.Component) health: $($taskHealth.status)" }

    $taskProcess = Start-Process -FilePath $taskShell -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $taskEntry + '"'),
        '-Component', $taskService.Component
    ) -WorkingDirectory $taskRepo -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $taskLogs ($taskService.Component + '-launcher.out.log')) `
        -RedirectStandardError (Join-Path $taskLogs ($taskService.Component + '-launcher.err.log'))
    $taskDeadline = (Get-Date).AddSeconds(60)
    do {
        if ($taskProcess.HasExited) { throw "$($taskService.Component) 启动失败，日志：$taskLogs" }
        Start-Sleep -Milliseconds 300
        $taskHealth = Get-ServiceHealth $taskService.Url
    } until ($taskHealth.status -eq 'ok' -or (Get-Date) -gt $taskDeadline)
    if ($taskHealth.status -ne 'ok') { throw "$($taskService.Component) 尚未就绪，日志：$taskLogs" }
}

Write-Host "EFAgent 已启动：$taskGatewayUrl/"
Write-Host "日志：$taskLogs"
if (-not $NoBrowser) { Start-Process ($taskGatewayUrl.TrimEnd('/') + '/') }
