param([string]$Config, [Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs)
. (Join-Path $PSScriptRoot 'environment.ps1') -Config $Config
Set-Location $taskRepo
& $env:EEGAGENT_PYTHON -m eegagent.knowledge @CommandArgs
exit $LASTEXITCODE
