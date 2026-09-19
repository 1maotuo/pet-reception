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
$adminToken = Get-DotEnvValue 'MERCHANT_ADMIN_TOKEN'
if (-not $serviceToken -or $serviceToken -eq 'GENERATE_A_RANDOM_SERVICE_TOKEN') {
    throw 'VOICE_GATEWAY_SERVICE_TOKEN is not configured in .env'
}
if (-not $adminToken -or $adminToken -eq 'GENERATE_A_RANDOM_MERCHANT_ADMIN_TOKEN') {
    throw 'MERCHANT_ADMIN_TOKEN is not configured in .env'
}
$merchantId = Get-DotEnvValue 'DEMO_MERCHANT_ID'
if (-not $merchantId) { $merchantId = 'demo-grooming-001' }

$suffix = [guid]::NewGuid().ToString('N')
# Spread repeatable development probes over future weeks so old probe records
# do not eventually consume every candidate on one hard-coded date.
$weekOffset = 26 + ([Convert]::ToInt32($suffix.Substring(0, 2), 16) % 104)
$targetDate = Get-DateOnOrAfter ([DateTime]::UtcNow.Date.AddDays(7 * $weekOffset)) Tuesday
$voiceHeaders = @{
    Authorization = "Bearer $serviceToken"
    'X-Merchant-ID' = $merchantId
    'X-Voice-Owner-ID' = "m3_probe_owner_$suffix"
    'X-Voice-Session-ID' = "m3_probe_session_$suffix"
    'X-Voice-Turn-ID' = "m3_probe_prepare_turn_$suffix"
}

$draftPayload = @{
    expected_version = 0
    customer_name = 'M3 演示客户'
    contact_phone = '+1-415-555-0198'
    pet_name = 'M3 演示宠物'
    pet_species = 'dog'
    pet_breed = 'demo breed'
    pet_age_years = 3
    pet_care_notes = 'M3 HTTP probe data'
    need_description = '基础洗护演示'
    service_id = 'demo-bath'
    preferred_date = $targetDate.ToString('yyyy-MM-dd')
    preferred_time_of_day = 'morning'
    staff_preference_text = 'Lisa'
    staff_id = 'demo-staff-lisa'
} | ConvertTo-Json
$draft = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-draft" `
    -Method Patch `
    -Headers $voiceHeaders `
    -Body $draftPayload

$searchPayload = @{
    expected_availability_revision = $draft.draft.availability_revision
} | ConvertTo-Json
$search = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/availability-searches" `
    -Method Post `
    -Headers $voiceHeaders `
    -Body $searchPayload
$candidate = $search.search.candidates | Select-Object -First 1
if ($null -eq $candidate) { throw 'no real availability candidate was returned' }

$preparePayload = @{
    expected_version = $draft.draft.version
    candidate_id = $candidate.id
} | ConvertTo-Json
$prepared = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-preparations" `
    -Method Post `
    -Headers $voiceHeaders `
    -Body $preparePayload

$presentationHeaders = $voiceHeaders.Clone()
$presentationHeaders['X-Presentation-Event-ID'] = "m3_probe_presentation_$suffix"
$presentPayload = @{
    expected_draft_version = $prepared.preparation.draft_version
} | ConvertTo-Json
$presented = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-preparations/$($prepared.preparation.id)/presented" `
    -Method Post `
    -Headers $presentationHeaders `
    -Body $presentPayload

$confirmationHeaders = $voiceHeaders.Clone()
$confirmationHeaders['X-Voice-Turn-ID'] = "m3_probe_confirm_turn_$suffix"
$confirmationHeaders['X-Confirmation-Event-ID'] = "m3_probe_confirmation_$suffix"
$confirmPayload = @{
    expected_draft_version = $prepared.preparation.draft_version
} | ConvertTo-Json
$awaitingPayment = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-preparations/$($prepared.preparation.id)/confirm" `
    -Method Post `
    -Headers $confirmationHeaders `
    -Body $confirmPayload
$retried = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-preparations/$($prepared.preparation.id)/confirm" `
    -Method Post `
    -Headers $confirmationHeaders `
    -Body $confirmPayload
$statusBeforePayment = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-operations/$($prepared.operation_id)" `
    -Headers $confirmationHeaders

$customerHeaders = @{
    Authorization = "Bearer $($awaitingPayment.customer_credential.access_token)"
}
$customerBeforePayment = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/customer/bookings/$($awaitingPayment.customer_credential.booking_reference)" `
    -Headers $customerHeaders
$paymentHeaders = $customerHeaders.Clone()
$paymentHeaders['X-Payment-Event-ID'] = "m3_probe_payment_$suffix"
$paid = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/customer/bookings/$($awaitingPayment.customer_credential.booking_reference)/simulate-payment" `
    -Method Post `
    -Headers $paymentHeaders
$statusAfterPayment = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/voice/booking-operations/$($prepared.operation_id)" `
    -Headers $confirmationHeaders
$customerAfterPayment = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/customer/bookings/$($awaitingPayment.customer_credential.booking_reference)" `
    -Headers $customerHeaders
$adminHeaders = @{
    Authorization = "Bearer $adminToken"
    'X-Merchant-ID' = $merchantId
}
$merchant = Invoke-JsonRequest `
    -Uri "$BackendUrl/api/internal/merchant/bookings/$($paid.booking.id)" `
    -Headers $adminHeaders

[PSCustomObject]@{
    PreparedStatus = $prepared.status
    PresentedStatus = $presented.status
    ConfirmationStatus = $awaitingPayment.status
    PaymentStatus = $paid.booking.status
    BookingId = $paid.booking.id
    BookingReference = $awaitingPayment.customer_credential.booking_reference
    ConfirmationCode = $paid.booking.confirmation_code
    IdempotentRetrySameBooking = ($retried.booking.id -eq $awaitingPayment.booking.id)
    StatusBeforePaymentWasPending = ($statusBeforePayment.status -eq 'awaiting_payment')
    CustomerBeforePaymentWasPending = ($customerBeforePayment.booking.status -eq 'awaiting_payment')
    StatusAfterPaymentSameBooking = ($statusAfterPayment.booking.id -eq $paid.booking.id)
    CustomerAfterPaymentSameBooking = ($customerAfterPayment.booking.id -eq $paid.booking.id)
    MerchantReadSameBooking = ($merchant.booking.id -eq $paid.booking.id)
    CustomerCredentialIssued = [bool]$awaitingPayment.customer_credential.access_token
    CustomerUrlAvailable = [bool]$awaitingPayment.customer_credential.credential_url
}
