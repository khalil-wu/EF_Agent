param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('worker', 'gateway')]
    [string]$Component
)

$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path $PSScriptRoot -Parent

# Public EFAgent names map to the existing worker and gateway configuration.
foreach ($taskVariable in Get-ChildItem Env:EFAGENT_*) {
    $taskInternalName = 'EEGAGENT_' + $taskVariable.Name.Substring('EFAGENT_'.Length)
    [Environment]::SetEnvironmentVariable($taskInternalName, $taskVariable.Value, 'Process')
}
$taskDefaults = @{
    EEGAGENT_STORAGE_DIR = Join-Path $taskRepo 'storage'
    EEGAGENT_SKILLS_DIR = Join-Path $taskRepo 'skills'
    EEGAGENT_DATA_DIRS = Join-Path $taskRepo 'data'
    EEGAGENT_KNOWLEDGE_DIR = Join-Path $taskRepo 'knowledge'
    EEGAGENT_MODEL_REGISTRY = Join-Path $taskRepo 'configs/model_registry.yaml'
    EEGAGENT_WORKER_URL = 'http://127.0.0.1:8100'
    EEGAGENT_ADDR = '127.0.0.1:8080'
}
foreach ($taskKey in $taskDefaults.Keys) {
    if (-not [Environment]::GetEnvironmentVariable($taskKey, 'Process')) {
        [Environment]::SetEnvironmentVariable($taskKey, $taskDefaults[$taskKey], 'Process')
    }
}

if ($Component -eq 'worker') {
    Set-Location $taskRepo
    & (Join-Path $taskRepo '.venv/Scripts/python.exe') -m uvicorn eegagent.worker.app:app --host 127.0.0.1 --port 8100 --app-dir src
} else {
    Set-Location (Join-Path $taskRepo 'gateway')
    & go run ./cmd/gateway
}
exit $LASTEXITCODE
