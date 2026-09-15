param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [ValidateRange(1024, 65535)]
    [int]$SetupPort = 8011
)

$ErrorActionPreference = 'Stop'
if ($Port -eq $SetupPort) {
    throw 'The setup page and CIRP app ports must differ.'
}

Write-Host 'CIRP Gemini 3.6 Flash local live launcher'
Write-Host 'Saved keys use Windows DPAPI current-user encryption; no plaintext .env, key command-line argument, or key log is created.'
Write-Host 'Conservative ledger rates: input CNY 7.50/M tokens; output CNY 37.50/M tokens.'
Remove-Item Env:CIRP_API_KEY -ErrorAction SilentlyContinue
Write-Host '[CHECK] Checking project dependencies without an API key.'
& "$PSScriptRoot\start-local.cmd" --install-only
if ($LASTEXITCODE -ne 0) {
    Write-Host '[STOP] Dependencies are not ready; no API key was requested or read.'
    exit $LASTEXITCODE
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python "$PSScriptRoot\scripts\gemini_local_setup.py" --provider gemini --setup-port $SetupPort --app-port $Port
exit $LASTEXITCODE
