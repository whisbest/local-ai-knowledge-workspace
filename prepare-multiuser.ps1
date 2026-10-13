[CmdletBinding()]
param([string]$ConfigFile='.runtime/multiuser.env',[int]$Port=18770)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' scripts/multiuser_runtime.py prepare --config $ConfigFile --port $Port
if ($LASTEXITCODE -ne 0) {throw 'Preparation failed.'}
