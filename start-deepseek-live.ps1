param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8010,
    [ValidateRange(1024, 65535)]
    [int]$SetupPort = 8011
)

$ErrorActionPreference = 'Stop'
if ($Port -eq $SetupPort) {
    throw 'The setup page and CIRP app ports must differ.'
}

Write-Host 'CIRP DeepSeek V4 Flash local live launcher'
Write-Host 'Saved keys use Windows DPAPI current-user encryption; no plaintext .env, key command-line argument, or key log is created.'
Write-Host 'Conservative ledger rates: input CNY 4.40/M tokens; output CNY 13.20/M tokens.'
Write-Host 'Data disclosure: after Analyze is clicked, eligible full-page PNG derivatives and parsed text are sent to the official DeepSeek API.'
Remove-Item Env:CIRP_API_KEY -ErrorAction SilentlyContinue
Write-Host '[CHECK] Checking project dependencies without an API key.'
& "$PSScriptRoot\start-local.cmd" --install-only
if ($LASTEXITCODE -ne 0) {
    Write-Host '[STOP] Dependencies are not ready; no API key was requested or read.'
    exit $LASTEXITCODE
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python "$PSScriptRoot\scripts\deepseek_local_setup.py" --setup-port $SetupPort --app-port $Port
exit $LASTEXITCODE
