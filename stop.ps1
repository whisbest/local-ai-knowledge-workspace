[CmdletBinding()]
param([string]$ComposeProject = 'local-knowledge-qa', [switch]$StopOwnedOllama)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe' }
if (-not (Test-Path -LiteralPath $dockerExe)) { throw 'Docker Desktop is missing.' }
$env:PATH = (Split-Path -Parent $dockerExe) + ';' + $env:PATH
& $dockerExe compose -p $ComposeProject down --timeout 30
if ($LASTEXITCODE -ne 0) { throw 'Application stop failed. Your data has not been deleted.' }
if ($StopOwnedOllama) {
    $pidFile = Join-Path $PSScriptRoot '.runtime/ollama-owned.pid'
    if (Test-Path -LiteralPath $pidFile) {
        $ownedPid = [int](Get-Content -LiteralPath $pidFile)
        $ownedProcess = Get-Process -Id $ownedPid -ErrorAction SilentlyContinue
        $expected = Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'
        if ($ownedProcess -and $ownedProcess.Path -eq $expected) { Stop-Process -Id $ownedPid }
        Remove-Item -LiteralPath $pidFile
    }
}
Write-Host 'Application stopped. Documents, indexes, conversations, settings and backups are retained.'
Write-Host 'Docker Desktop and existing Ollama services are left available for other applications.'
