$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'backend/.venv is missing; follow README.md first-install steps'
}

& (Join-Path $PSScriptRoot 'check.ps1')
if ($LASTEXITCODE -ne 0) { throw 'database-independent checks failed' }

Push-Location $projectRoot
try {
    & $python -m pet_reception_backend.manage init-test-db
    if ($LASTEXITCODE -ne 0) { throw 'test database initialization failed' }

    $previousRunPostgresTests = $env:RUN_POSTGRES_TESTS
    try {
        $env:RUN_POSTGRES_TESTS = '1'
        Push-Location (Join-Path $projectRoot 'backend')
        try {
            & $python -m pytest -m postgres
            if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL integration tests failed' }
        }
        finally {
            Pop-Location
        }
    }
    finally {
        if ($null -eq $previousRunPostgresTests) {
            Remove-Item Env:RUN_POSTGRES_TESTS -ErrorAction SilentlyContinue
        }
        else {
            $env:RUN_POSTGRES_TESTS = $previousRunPostgresTests
        }
    }
}
finally {
    Pop-Location
}

Write-Output 'All M2-1 checks passed against the independent PostgreSQL test database.'
