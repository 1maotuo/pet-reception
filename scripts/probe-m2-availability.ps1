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

function Invoke-JsonRequest {
    param(
        [string]$Uri,
        [string]$Method = 'Get',
        [hashtable]$Headers,
        [string]$Body = ''
    )
    $arguments = @{
        Uri = $Uri
        Method = $Method
        Headers = $Headers
        UseBasicParsing = $true
    }
    if ($Body) {
        $arguments.ContentType = 'application/json'
        $arguments.Body = $Body
    }
    $response = Invoke-WebRequest @arguments
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
        return $response.Content | ConvertFrom-Json -DateKind String
    }

    # Windows PowerShell 5.1 converts ISO timestamps into the computer's local
    # time zone. JavaScriptSerializer keeps them as the merchant-offset strings
    # returned by the API, so the probe evidence remains unambiguous.
    Add-Type -AssemblyName System.Web.Extensions
    $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $utf8Content = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
    return $serializer.DeserializeObject($utf8Content)
}

function Get-DateOnOrAfter([datetime]$start, [DayOfWeek]$weekday) {
    $candidate = $start.Date
    while ($candidate.DayOfWeek -ne $weekday) {
        $candidate = $candidate.AddDays(1)
    }
    return $candidate
}

$serviceToken = Get-DotEnvValue 'VOICE_GATEWAY_SERVICE_TOKEN'
if (-not $serviceToken -or $serviceToken -eq 'GENERATE_A_RANDOM_SERVICE_TOKEN') {
    throw 'VOICE_GATEWAY_SERVICE_TOKEN is not configured in .env'
}
$merchantId = Get-DotEnvValue 'DEMO_MERCHANT_ID'
if (-not $merchantId) { $merchantId = 'demo-grooming-001' }

$suffix = [guid]::NewGuid().ToString('N')
$openDate = Get-DateOnOrAfter ([DateTime]::UtcNow.Date.AddDays(14)) Friday
$closedDate = Get-DateOnOrAfter ($openDate.AddDays(1)) Sunday
$headers = @{
    Authorization = "Bearer $serviceToken"
    'X-Merchant-ID' = $merchantId
    'X-Voice-Owner-ID' = "m2_availability_owner_$suffix"
    'X-Voice-Session-ID' = "m2_availability_session_$suffix"
    'X-Voice-Turn-ID' = "m2_availability_turn_$suffix"
}

$merchant = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/merchant-info" `
    -Headers $headers
$draftPayload = @{
    expected_version = 0
    service_id = 'demo-full-groom'
    preferred_date = $openDate.ToString('yyyy-MM-dd')
    preferred_time_of_day = 'morning'
    staff_preference_text = 'Lisa'
    staff_id = 'demo-staff-lisa'
} | ConvertTo-Json
$draft = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-draft" `
    -Method Patch `
    -Headers $headers `
    -Body $draftPayload

$searchPayload = @{
    expected_availability_revision = $draft.draft.availability_revision
} | ConvertTo-Json
$available = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/availability-searches" `
    -Method Post `
    -Headers $headers `
    -Body $searchPayload

$phonePayload = @{
    expected_version = $draft.draft.version
    contact_phone = '+1-415-555-0101'
} | ConvertTo-Json
$afterPhone = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-draft" `
    -Method Patch `
    -Headers $headers `
    -Body $phonePayload
$stillCurrent = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/availability-searches/$($available.search.id)" `
    -Headers $headers

$closedDayPayload = @{
    expected_version = $afterPhone.draft.version
    preferred_date = $closedDate.ToString('yyyy-MM-dd')
    preferred_time_of_day = 'flexible'
} | ConvertTo-Json
$closedDayDraft = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-draft" `
    -Method Patch `
    -Headers $headers `
    -Body $closedDayPayload
$stale = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/availability-searches/$($available.search.id)" `
    -Headers $headers
$closedDaySearchPayload = @{
    expected_availability_revision = $closedDayDraft.draft.availability_revision
} | ConvertTo-Json
$closedDay = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/availability-searches" `
    -Method Post `
    -Headers $headers `
    -Body $closedDaySearchPayload

$firstCandidate = $available.search.candidates | Select-Object -First 1
$firstAlternative = $closedDay.search.alternative_dates | Select-Object -First 1
[PSCustomObject]@{
    Merchant = $merchant.merchant.display_name
    Staff = ($merchant.merchant.staff.display_name -join ', ')
    SearchStatus = $available.search.status
    FirstCandidate = "$($firstCandidate.staff_display_name) $($firstCandidate.starts_at)"
    ServiceEndsAt = $firstCandidate.ends_at
    BusyInterval = "$($firstCandidate.busy_starts_at) -> $($firstCandidate.busy_ends_at)"
    PhoneChangeKeptCurrent = $stillCurrent.search.is_current
    OldSearchAfterDateChange = $stale.search.status
    ClosedDayStatus = $closedDay.search.status
    FirstAlternative = "$($firstAlternative.date) $($firstAlternative.first_start_at)"
    BookingCreated = $closedDay.search.booking_created
}
