$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'backend/.venv is missing; follow README.md first-install steps'
}

$minimumNodeVersion = [version]'24.15.0'
$portableNode = Join-Path (Split-Path -Parent $projectRoot) 'work\runtimes\node-v24.15.0-win-x64\node.exe'
$nodeSources = @(
    (Get-Command node -All -ErrorAction SilentlyContinue | ForEach-Object Source)
    $portableNode
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Unique
$nodeSource = $nodeSources |
    Where-Object {
        $candidateVersion = [version]((& $_ --version).TrimStart('v').Split('-')[0])
        $candidateVersion.Major -eq 24 -and $candidateVersion -ge $minimumNodeVersion
    } |
    Select-Object -First 1
if ($null -eq $nodeSource) {
    $foundVersions = $nodeSources |
        ForEach-Object { & $_ --version } |
        Sort-Object -Unique
    throw "Node ^24.15.0 is required; found $($foundVersions -join ', ')"
}
$env:Path = "$(Split-Path -Parent $nodeSource);$env:Path"

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

Write-Output 'All database-independent M0/M1/M2/M3 checks passed.'
