$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$examplePath = Join-Path $projectRoot '.env.example'
$envPath = Join-Path $projectRoot '.env'

function New-RandomToken {
    $bytes = [byte[]]::new(32)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

if (Test-Path -LiteralPath $envPath) {
    $existing = Get-Content -Raw -LiteralPath $envPath
    $additions = [System.Collections.Generic.List[string]]::new()
    if ($existing -notmatch '(?m)^MERCHANT_ADMIN_TOKEN=') {
        $additions.Add("MERCHANT_ADMIN_TOKEN=$(New-RandomToken)")
    }
    if ($existing -notmatch '(?m)^BOOKING_CREDENTIAL_SECRET=') {
        $additions.Add("BOOKING_CREDENTIAL_SECRET=$(New-RandomToken)")
    }
    if ($existing -notmatch '(?m)^BOOKING_HOLD_MINUTES=') {
        $additions.Add('BOOKING_HOLD_MINUTES=5')
    }
    if ($additions.Count -eq 0) {
        Write-Output '.env already contains the required generated values; no changes made.'
        exit 0
    }
    $prefix = if ($existing.EndsWith("`n")) { '' } else { "`r`n" }
    [System.IO.File]::AppendAllText(
        $envPath,
        $prefix + ($additions -join "`r`n") + "`r`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Output 'Added missing generated M3 values to the ignored local .env.'
    exit 0
}

$content = Get-Content -Raw -LiteralPath $examplePath
$content = $content.Replace('GENERATE_A_RANDOM_SERVICE_TOKEN', (New-RandomToken))
$content = $content.Replace('GENERATE_A_RANDOM_MERCHANT_ADMIN_TOKEN', (New-RandomToken))
$content = $content.Replace('GENERATE_A_RANDOM_BOOKING_CREDENTIAL_SECRET', (New-RandomToken))
$content = $content.Replace(
    'GENERATE_A_RANDOM_AUTH_SECRET_AT_LEAST_32_CHARS',
    (New-RandomToken)
)
[System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
Write-Output "Created ignored local configuration: $envPath"
