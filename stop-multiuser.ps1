[CmdletBinding()]
param([string]$ConfigFile='.runtime/multiuser.env',[string]$ComposeProject='lkq-multiuser',[switch]$Production)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskArgs=@('scripts/multiuser_runtime.py','stop','--config',$ConfigFile,'--project',$ComposeProject)
if ($Production) {$taskArgs+='--production'}
& '.\.venv\Scripts\python.exe' @taskArgs
if ($LASTEXITCODE -ne 0) {throw 'Multiuser shutdown failed.'}
