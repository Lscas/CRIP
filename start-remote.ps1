param([switch]$Pages, [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-app.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$Arguments = @('scripts/remote_preview.py', '--port', "$Port")
if ($Pages) { $Arguments += '--pages' }
& '.\.venv\Scripts\python.exe' @Arguments
exit $LASTEXITCODE
