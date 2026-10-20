param([string]$Config)

$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path $PSScriptRoot -Parent
if ($Config) { . $Config }
foreach ($taskVariable in Get-ChildItem Env:EFAGENT_*) {
    [Environment]::SetEnvironmentVariable('EEGAGENT_' + $taskVariable.Name.Substring(8), $taskVariable.Value, 'Process')
}
$taskDefaults = @{
    EEGAGENT_STORAGE_DIR = Join-Path $taskRepo 'storage'
    EEGAGENT_SKILLS_DIR = Join-Path $taskRepo 'skills'
    EEGAGENT_DATA_DIRS = Join-Path $taskRepo 'data'
    EEGAGENT_KNOWLEDGE_DIR = Join-Path $taskRepo 'knowledge'
    EEGAGENT_MODEL_REGISTRY = Join-Path $taskRepo 'configs/model_registry.yaml'
    EEGAGENT_LLM_CAPABILITIES = Join-Path $taskRepo 'configs/conversation_models.json'
    EEGAGENT_PYTHON = Join-Path $taskRepo '.venv/Scripts/python.exe'
    EEGAGENT_WORKER_URL = 'http://127.0.0.1:8100'
    EEGAGENT_ADDR = '127.0.0.1:8080'
}
foreach ($taskKey in $taskDefaults.Keys) {
    if (-not [Environment]::GetEnvironmentVariable($taskKey, 'Process')) {
        [Environment]::SetEnvironmentVariable($taskKey, $taskDefaults[$taskKey], 'Process')
    }
}
foreach ($taskKey in 'EEGAGENT_STORAGE_DIR','EEGAGENT_SKILLS_DIR','EEGAGENT_KNOWLEDGE_DIR','EEGAGENT_MODEL_REGISTRY','EEGAGENT_LLM_CAPABILITIES','EEGAGENT_PYTHON','EEGAGENT_LOCAL_FONTS','EEGAGENT_STATIC_DIR') {
    $taskValue = [Environment]::GetEnvironmentVariable($taskKey, 'Process')
    if ($taskValue) {
        if (-not [IO.Path]::IsPathRooted($taskValue)) { $taskValue = Join-Path $taskRepo $taskValue }
        [Environment]::SetEnvironmentVariable($taskKey, [IO.Path]::GetFullPath($taskValue), 'Process')
    }
}
$env:EEGAGENT_SOURCE_ROOT = $taskRepo
$taskSourcePath = Join-Path $taskRepo 'src'
$env:PYTHONPATH = (@($taskSourcePath) + @($env:PYTHONPATH -split [IO.Path]::PathSeparator | Where-Object { $_ -and $_ -ne $taskSourcePath })) -join [IO.Path]::PathSeparator
$env:EEGAGENT_DATA_DIRS = (($env:EEGAGENT_DATA_DIRS -split ';' | Where-Object { $_ } | ForEach-Object {
    if ([IO.Path]::IsPathRooted($_)) { [IO.Path]::GetFullPath($_) } else { [IO.Path]::GetFullPath((Join-Path $taskRepo $_)) }
}) -join ';')
$taskStorage = [IO.Path]::GetFullPath($env:EEGAGENT_STORAGE_DIR)
$taskWorkerUri = [uri]$env:EEGAGENT_WORKER_URL
$taskGatewayAddress = $env:EEGAGENT_ADDR
if ($taskGatewayAddress.StartsWith(':')) { $taskGatewayAddress = '127.0.0.1' + $taskGatewayAddress }
$taskGatewayUri = [uri]('http://' + $taskGatewayAddress)
$taskGatewayHost = if ($taskGatewayUri.Host -in '0.0.0.0','[::]') { '127.0.0.1' } else { $taskGatewayUri.Host }
$taskGatewayUrl = 'http://' + $taskGatewayHost + ':' + $taskGatewayUri.Port
$taskWorkerUrl = $env:EEGAGENT_WORKER_URL.TrimEnd('/')
$taskStatic = Join-Path $taskRepo 'gateway/internal/server/static'
$taskUiSources = @(Get-ChildItem -LiteralPath (Join-Path $taskRepo 'frontend/src'),(Join-Path $taskRepo 'frontend/bridge'),(Join-Path $taskRepo 'frontend/scripts') -Recurse -File | Where-Object { $_.Extension -in '.ts','.tsx','.js','.mjs','.css','.html' -and $_.Name -notmatch '\.test\.' })
$taskUiSources += Get-Item -LiteralPath (Join-Path $taskRepo 'frontend/vite.config.ts'),(Join-Path $taskRepo 'frontend/package.json'),(Join-Path $taskRepo 'frontend/package-lock.json')
$taskPythonSources = @(Get-ChildItem -LiteralPath (Join-Path $taskRepo 'src') -Recurse -Filter '*.py')
$taskGatewaySources = @(Get-ChildItem -LiteralPath (Join-Path $taskRepo 'gateway'),(Join-Path $taskRepo 'skills'),(Join-Path $taskRepo 'contracts') -Recurse -File | Where-Object { ($_.Extension -in '.go','.js','.css','.html','.json','.md','.txt') -and $_.Name -notmatch '_test\.go$' })
$taskGatewaySources += Get-Item -LiteralPath (Join-Path $taskRepo 'src/eegagent/worker/tools.py')
$env:EEGAGENT_WORKER_BUILD_ID = 'worker-' + (($taskPythonSources | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc.Ticks)
$env:EEGAGENT_GATEWAY_BUILD_ID = 'gateway-' + ((@($taskGatewaySources) + @($taskUiSources) | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc.Ticks)

function Get-EFAgentConfigId($Value) {
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Compress))))).Replace('-','').ToLowerInvariant() }
    finally { $taskHasher.Dispose() }
}
$taskRegistryTime = (Get-Item -LiteralPath $env:EEGAGENT_MODEL_REGISTRY).LastWriteTimeUtc.Ticks
$taskCapabilityTime = (Get-Item -LiteralPath $env:EEGAGENT_LLM_CAPABILITIES).LastWriteTimeUtc.Ticks
$taskFontTime = if ($env:EEGAGENT_LOCAL_FONTS) { (Get-Item -LiteralPath $env:EEGAGENT_LOCAL_FONTS).LastWriteTimeUtc.Ticks } else { 0 }
$env:EEGAGENT_WORKER_CONFIG_ID = Get-EFAgentConfigId @($taskStorage,$env:EEGAGENT_DATA_DIRS,$env:EEGAGENT_KNOWLEDGE_DIR,$env:EEGAGENT_MODEL_REGISTRY,$taskRegistryTime,$taskWorkerUrl,$env:EEGAGENT_PYTHON)
$env:EEGAGENT_GATEWAY_CONFIG_ID = Get-EFAgentConfigId @($taskStorage,$env:EEGAGENT_SKILLS_DIR,$taskWorkerUrl,$env:EEGAGENT_LLM_BASE_URL,$env:EEGAGENT_LLM_MODEL,$env:EEGAGENT_LLM_API_KEY,$env:EEGAGENT_LLM_PROXY,$env:EEGAGENT_LLM_CAPABILITIES,$taskCapabilityTime,$env:EEGAGENT_LOCAL_FONTS,$taskFontTime)

function Build-EFAgentUI {
    $taskBundle = Get-Item -LiteralPath (Join-Path $taskStatic 'transcript-ui.js')
    if (@($taskUiSources | Where-Object { $_.LastWriteTimeUtc -gt $taskBundle.LastWriteTimeUtc }).Count) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskRepo 'frontend/node_modules'))) { & npm --prefix (Join-Path $taskRepo 'frontend') ci; if ($LASTEXITCODE) { throw '前端依赖安装失败' } }
        & npm --prefix (Join-Path $taskRepo 'frontend') run build
        if ($LASTEXITCODE) { throw '前端构建失败' }
    }
}

function Build-EFAgentGateway {
    $taskBin = Join-Path $taskStorage 'bin'
    New-Item -ItemType Directory -Path $taskBin -Force | Out-Null
    $taskBinary = Join-Path $taskBin ($env:EEGAGENT_GATEWAY_BUILD_ID + '.exe')
    if (-not (Test-Path -LiteralPath $taskBinary)) {
        Push-Location (Join-Path $taskRepo 'gateway')
        try { & go build -o $taskBinary ./cmd/gateway; if ($LASTEXITCODE) { throw '网关构建失败' } }
        finally { Pop-Location }
    }
    return $taskBinary
}

function Publish-EFAgentLocalUI {
    if ($env:EEGAGENT_LOCAL_FONTS) {
        $taskUi = Join-Path $taskStorage 'ui'
        New-Item -ItemType Directory -Path $taskUi -Force | Out-Null
        Copy-Item -Path (Join-Path $taskStatic '*') -Destination $taskUi -Recurse -Force
        [IO.File]::AppendAllText((Join-Path $taskUi 'app.css'), "`n" + [IO.File]::ReadAllText($env:EEGAGENT_LOCAL_FONTS))
        Copy-Item -LiteralPath (Join-Path (Split-Path $env:EEGAGENT_LOCAL_FONTS -Parent) 'fonts') -Destination $taskUi -Recurse -Force
        $env:EEGAGENT_STATIC_DIR = $taskUi
    }
}
