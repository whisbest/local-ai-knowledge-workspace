[CmdletBinding()]
param([string]$Stage,[switch]$All,[switch]$Benchmark)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$taskArgs=@('scripts/verify_multiuser.py')
if ($All) {$taskArgs+='--all'} elseif ($Benchmark) {$taskArgs+='--benchmark'} elseif ($Stage) {$taskArgs+=@('--stage',$Stage)} else {throw 'Choose -Stage, -All or -Benchmark'}
& '.\.venv\Scripts\python.exe' @taskArgs
if ($LASTEXITCODE -ne 0) {throw 'Verification failed. Fix before continuing.'}
