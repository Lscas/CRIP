param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8010,
    [ValidateRange(1024, 65535)]
    [int]$SetupPort = 8011
)

$ErrorActionPreference = 'Stop'
if ($Port -eq $SetupPort) { throw 'The setup page and CIRP app ports must differ.' }

Write-Host 'CIRP custom OpenAI-compatible model launcher'
Write-Host 'Configure a remote API or a loopback local model in the browser. Keys are saved only when Windows DPAPI storage is selected.'
Remove-Item Env:CIRP_API_KEY -ErrorAction SilentlyContinue
& "$PSScriptRoot\start-local.cmd" --install-only
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python "$PSScriptRoot\scripts\gemini_local_setup.py" --provider custom --setup-port $SetupPort --app-port $Port
exit $LASTEXITCODE
