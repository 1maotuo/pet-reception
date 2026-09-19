$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'backend/.venv is missing; follow README.md first-install steps'
}

$nodeVersion = node --version
if ($nodeVersion -ne 'v24.15.0') {
    throw "Node v24.15.0 is required; found $nodeVersion"
}

Push-Location (Join-Path $projectRoot 'backend')
try {
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'backend tests failed' }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'backend dependency check failed' }
}
finally {
    Pop-Location
}

Push-Location $projectRoot
try {
    npm test
    if ($LASTEXITCODE -ne 0) { throw 'Node tests failed' }
    npm run build:web
    if ($LASTEXITCODE -ne 0) { throw 'web build failed' }
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed' }
}
finally {
    Pop-Location
}

Write-Output 'All deterministic M0/M1 checks passed.'
