$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$examplePath = Join-Path $projectRoot '.env.example'
$envPath = Join-Path $projectRoot '.env'

if (Test-Path -LiteralPath $envPath) {
    Write-Output ".env already exists; refusing to overwrite it."
    exit 0
}

function New-RandomToken {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

$content = Get-Content -Raw -LiteralPath $examplePath
$content = $content.Replace('GENERATE_A_RANDOM_SERVICE_TOKEN', (New-RandomToken))
$content = $content.Replace(
    'GENERATE_A_RANDOM_AUTH_SECRET_AT_LEAST_32_CHARS',
    (New-RandomToken)
)
[System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
Write-Output "Created ignored local configuration: $envPath"
