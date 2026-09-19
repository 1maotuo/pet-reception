param(
    [string]$BackendUrl = 'http://127.0.0.1:8010'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env'

if (-not (Test-Path -LiteralPath $envPath)) {
    throw '.env is missing; run scripts/init-env.ps1 first'
}

function Get-DotEnvValue([string]$name) {
    $prefix = "$name="
    $line = Get-Content -LiteralPath $envPath |
        Where-Object { $_.StartsWith($prefix) } |
        Select-Object -First 1
    if ($null -eq $line) { return '' }
    return $line.Substring($prefix.Length)
}

$serviceToken = Get-DotEnvValue 'VOICE_GATEWAY_SERVICE_TOKEN'
if (-not $serviceToken -or $serviceToken -eq 'GENERATE_A_RANDOM_SERVICE_TOKEN') {
    throw 'VOICE_GATEWAY_SERVICE_TOKEN is not configured in .env'
}
$merchantId = Get-DotEnvValue 'DEMO_MERCHANT_ID'
if (-not $merchantId) { $merchantId = 'demo-grooming-001' }

$suffix = [guid]::NewGuid().ToString('N')
$headers = @{
    Authorization = "Bearer $serviceToken"
    'X-Merchant-ID' = $merchantId
    'X-Voice-Owner-ID' = "m2_probe_owner_$suffix"
    'X-Voice-Session-ID' = "m2_probe_session_$suffix"
    'X-Voice-Turn-ID' = "m2_probe_turn_$suffix"
}

$merchant = Invoke-RestMethod "$BackendUrl/api/internal/voice/merchant-info" -Headers $headers
$initial = Invoke-RestMethod "$BackendUrl/api/internal/voice/booking-draft" -Headers $headers
$payload = @{
    expected_version = $initial.draft.version
    pet_name = 'M2 演示宠物'
    pet_species = 'dog'
    service_id = 'demo-bath'
    preferred_date = '2026-10-02'
} | ConvertTo-Json
$updated = Invoke-RestMethod "$BackendUrl/api/internal/voice/booking-draft" `
    -Method Patch `
    -Headers $headers `
    -ContentType 'application/json' `
    -Body $payload

[PSCustomObject]@{
    Merchant = $merchant.merchant.display_name
    DraftId = $updated.draft.id
    InitialVersion = $initial.draft.version
    UpdatedVersion = $updated.draft.version
    ChangedFields = $updated.changed_fields -join ', '
    MissingForAvailability = $updated.missing_for_availability -join ', '
    MissingForBooking = $updated.missing_for_booking -join ', '
}
