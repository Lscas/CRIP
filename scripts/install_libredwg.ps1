param()

$ErrorActionPreference = 'Stop'
$version = '0.14'
$expectedSha256 = '1AD7E15344D20B3426C3435B078D82FB84B35062815946B2CCA9C5FC9810FEA8'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$target = Join-Path $projectRoot ".local\tools\libredwg-$version-win64"
$executable = Join-Path $target 'dwg2dxf.exe'

if (Test-Path -LiteralPath $executable) {
    & $executable --version
    exit $LASTEXITCODE
}

$archive = Join-Path $env:TEMP "cirp-libredwg-$version-win64.zip"
$url = "https://github.com/LibreDWG/libredwg/releases/download/$version/libredwg-$version-win64.zip"
Write-Host "Downloading GNU LibreDWG $version from its official release..."
Invoke-WebRequest $url -OutFile $archive -UseBasicParsing
$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
if ($actualSha256 -cne $expectedSha256) {
    throw "LibreDWG archive integrity check failed. Expected $expectedSha256, got $actualSha256."
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $target -Force
if (-not (Test-Path -LiteralPath $executable)) {
    throw 'LibreDWG archive did not contain dwg2dxf.exe.'
}
& $executable --version
if ($LASTEXITCODE -ne 0) {
    throw 'LibreDWG executable did not start successfully.'
}
Write-Host "Installed to $target. CIRP will discover it automatically."
