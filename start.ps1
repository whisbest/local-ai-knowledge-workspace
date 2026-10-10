[CmdletBinding()]
param([switch]$NoBrowser, [string]$ComposeProject = 'local-knowledge-qa')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$configuredDataPath = if ($env:DATA_PATH) { $env:DATA_PATH } else { './data' }
if (-not $env:DATA_PATH -and (Test-Path -LiteralPath '.env')) {
    foreach ($line in Get-Content -LiteralPath '.env') { if ($line -match '^DATA_PATH=(.+)$') { $configuredDataPath = $Matches[1].Trim().Trim('"').Trim("'") } }
}
$env:HOST_DATA_PATH = if ([IO.Path]::IsPathRooted($configuredDataPath)) { [IO.Path]::GetFullPath($configuredDataPath) } else { [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $configuredDataPath)) }
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe' }
if (-not (Test-Path -LiteralPath $dockerExe)) { throw 'Docker Desktop is missing. Install Docker Desktop first.' }
$env:PATH = (Split-Path -Parent $dockerExe) + ';' + $env:PATH
$desktopExe = Join-Path (Split-Path -Parent (Split-Path -Parent $dockerExe)) 'Docker Desktop.exe'
# This host has EFS-encrypted AppData. Only the Docker process receives the alternate paths.
function Test-DockerEngine {
    $savedPreference = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; & $dockerExe info --format '{{.ServerVersion}}' 2>$null | Out-Null; return $LASTEXITCODE -eq 0 }
    finally { $ErrorActionPreference = $savedPreference }
}
$engineReady = Test-DockerEngine
if (-not $engineReady) {
    $savedRoaming = $env:APPDATA; $savedLocal = $env:LOCALAPPDATA
    try {
        $env:APPDATA = Join-Path $PSScriptRoot '.runtime/roaming'
        $env:LOCALAPPDATA = Join-Path $PSScriptRoot '.runtime/local'
        New-Item -ItemType Directory -Force -Path $env:APPDATA,$env:LOCALAPPDATA | Out-Null
        Start-Process -FilePath $desktopExe -WindowStyle Hidden
    } finally { $env:APPDATA = $savedRoaming; $env:LOCALAPPDATA = $savedLocal }
    Write-Host 'Waiting for Docker Desktop...'
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        if (Test-DockerEngine) { $engineReady = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $engineReady) { throw 'Docker did not start. Check Docker Desktop / WSL 2.' }
}
# Local Ollama is optional in API mode; start it if installed and not already serving.
$ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3 | Out-Null } catch {
    if (Test-Path -LiteralPath $ollamaExe) {
        $runtime = Join-Path $PSScriptRoot '.runtime'
        New-Item -ItemType Directory -Force -Path $runtime | Out-Null
        $process = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'ollama-out.log') -RedirectStandardError (Join-Path $runtime 'ollama-error.log')
        $process.Id | Set-Content -LiteralPath (Join-Path $runtime 'ollama-owned.pid')
    } else { Write-Host 'Ollama is not installed; API mode remains available.' }
}
foreach ($imageName in @('local-knowledge-qa-api:v1','local-knowledge-qa-web:v1')) {
    & $dockerExe image inspect $imageName 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Image $imageName is missing. First-time preparation requires: docker compose build" }
}
# Never pull or build during daily startup. Bind-mounted data survives recreation.
& $dockerExe compose -p $ComposeProject up -d --no-build --pull never --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw 'Application startup failed. Check docker compose logs.' }
$appPort = if ($env:APP_PORT) { $env:APP_PORT } else { '18765' }
if (Test-Path -LiteralPath '.env') {
    foreach ($line in Get-Content -LiteralPath '.env') { if ($line -match '^APP_PORT=(\d+)$' -and -not $env:APP_PORT) { $appPort = $Matches[1] } }
}
$url = "http://127.0.0.1:$appPort"
Write-Host "Application ready: $url"
Write-Host 'Data is retained in the configured DATA_PATH (default: project/data).'
if (-not $NoBrowser) { Start-Process $url }
