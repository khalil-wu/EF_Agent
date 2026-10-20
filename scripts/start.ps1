param([switch]$NoBrowser, [string]$Config)

. (Join-Path $PSScriptRoot 'environment.ps1') -Config $Config
$taskShell = (Get-Process -Id $PID).Path
$taskLogs = Join-Path $taskStorage 'logs'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null

function Get-ServiceHealth([string]$Url, [string]$Path='/health') {
    $taskRequest = [Net.WebRequest]::Create($Url.TrimEnd('/') + $Path)
    $taskRequest.Proxy = $null
    $taskRequest.Timeout = 3000
    try { $taskResponse = $taskRequest.GetResponse() }
    catch [Net.WebException] {
        if ($_.Exception.Response) { throw }
        return $null
    }
    $taskReader = [IO.StreamReader]::new($taskResponse.GetResponseStream())
    try { return ($taskReader.ReadToEnd() | ConvertFrom-Json) }
    finally { $taskReader.Close(); $taskResponse.Close() }
}

$taskServices = @(
    @{ Component='worker'; Url=$taskWorkerUrl; Health=(Get-ServiceHealth $taskWorkerUrl) },
    @{ Component='gateway'; Url=$taskGatewayUrl; Health=(Get-ServiceHealth $taskGatewayUrl) }
)
foreach ($taskService in $taskServices) {
    $taskHealth = $taskService.Health
    if ($null -ne $taskHealth -and ($taskHealth.application -ne 'EFAgent' -or $taskHealth.component -ne $taskService.Component -or $taskHealth.source_root -ne $taskRepo -or $taskHealth.storage_dir -ne $taskStorage)) {
        throw "$($taskService.Url) 已被其他实例占用；请使用该实例的配置或换一个端口。"
    }
    $taskPrefix = 'EEGAGENT_' + $taskService.Component.ToUpperInvariant()
    $taskService.NeedsStart = $null -eq $taskHealth -or $taskHealth.build_id -ne [Environment]::GetEnvironmentVariable($taskPrefix+'_BUILD_ID') -or $taskHealth.configuration_id -ne [Environment]::GetEnvironmentVariable($taskPrefix+'_CONFIG_ID')
}
if (@($taskServices | Where-Object NeedsStart).Count) {
    if ($taskServices[1].Health) {
        $taskSessions = @(Get-ServiceHealth $taskGatewayUrl '/sessions')
        if (@($taskSessions | Where-Object running).Count) {
            Write-Host '当前分析仍在运行，继续使用现有版本；分析结束后再次运行此命令更新。'
            if (-not $NoBrowser) { Start-Process ($taskGatewayUrl+'/') }
            return
        }
    }
    Build-EFAgentUI
    . (Join-Path $PSScriptRoot 'environment.ps1')
    $taskBinary = Build-EFAgentGateway
    foreach ($taskService in $taskServices) {
        $taskPrefix = 'EEGAGENT_' + $taskService.Component.ToUpperInvariant()
        $taskService.NeedsStart = $null -eq $taskService.Health -or $taskService.Health.build_id -ne [Environment]::GetEnvironmentVariable($taskPrefix+'_BUILD_ID') -or $taskService.Health.configuration_id -ne [Environment]::GetEnvironmentVariable($taskPrefix+'_CONFIG_ID')
    }
    foreach ($taskService in @($taskServices[1],$taskServices[0])) {
        if ($taskService.NeedsStart -and $taskService.Health) {
            $taskOldProcess = Get-Process -Id $taskService.Health.process_id
            Stop-Process -Id $taskOldProcess.Id
            $taskOldProcess.WaitForExit()
        }
    }
}
foreach ($taskService in $taskServices) {
    if ($taskService.NeedsStart) {
        $taskProcess = Start-Process -FilePath $taskShell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $PSScriptRoot 'serve.ps1')+'"'),'-Component',$taskService.Component) -WorkingDirectory $taskRepo -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $taskLogs ($taskService.Component+'-launcher.out.log')) `
            -RedirectStandardError (Join-Path $taskLogs ($taskService.Component+'-launcher.err.log'))
        $taskDeadline = (Get-Date).AddSeconds(60)
        do {
            if ($taskProcess.HasExited) { throw "$($taskService.Component) 启动失败，日志：$taskLogs" }
            Start-Sleep -Milliseconds 300
            $taskHealth = Get-ServiceHealth $taskService.Url
        } until ($taskHealth.status -eq 'ok' -or (Get-Date) -gt $taskDeadline)
    } else { $taskHealth = Get-ServiceHealth $taskService.Url }
    if ($taskHealth.status -ne 'ok') { throw "$($taskService.Component) 未就绪，日志：$taskLogs" }
    Write-Host "$($taskService.Component) 就绪：$($taskHealth.build_id)"
}
Write-Host "EFAgent：$taskGatewayUrl/"
Write-Host "代码：$taskRepo；日志：$taskLogs"
if (-not $NoBrowser) { Start-Process ($taskGatewayUrl+'/') }
