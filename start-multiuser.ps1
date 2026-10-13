[CmdletBinding()]
param([string]$ConfigFile='.runtime/multiuser.env',[string]$ComposeProject='lkq-multiuser',[switch]$Production)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$taskArgs=@('scripts/multiuser_runtime.py','start','--config',$ConfigFile,'--project',$ComposeProject)
if ($Production) {$taskArgs+='--production'}
& $taskPython @taskArgs
if ($LASTEXITCODE -ne 0) {throw 'Multiuser startup failed.'}
