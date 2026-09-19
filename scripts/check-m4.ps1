$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot 'check-m3.ps1')
if ($LASTEXITCODE -ne 0) { throw 'M0-M3 regression checks failed' }

$backendHealth = Invoke-RestMethod 'http://127.0.0.1:8010/health'
if ($backendHealth.status -ne 'ok' -or $backendHealth.database.status -ne 'connected') {
    throw 'running backend/PostgreSQL health check failed'
}

$gatewayHealth = Invoke-RestMethod 'http://127.0.0.1:3101/api/health'
if ($gatewayHealth.status -ne 'ready' -or $gatewayHealth.voiceConfigured -ne $true) {
    throw 'running Qwen voice gateway is not ready/configured'
}

Push-Location $projectRoot
try {
    npm run probe:m4 --workspace @pet-reception/voice-gateway
    if ($LASTEXITCODE -ne 0) { throw 'M4 real HTTP business tool probe failed' }
}
finally {
    Pop-Location
}

Write-Output 'M4 deterministic, PostgreSQL, build, running-service, and real HTTP tool checks passed.'
