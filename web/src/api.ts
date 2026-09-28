export type DatabaseStatus = {
  status: 'connected' | 'unavailable' | 'not_configured'
  detail: string
}

export type SystemStatus = {
  service: 'pet-reception-backend'
  status: 'ok' | 'degraded'
  database: DatabaseStatus
}

export type BookingRecord = {
  id: string
  merchant_display_name: string
  staff_display_name: string
  service_name_zh: string
  customer_name: string
  contact_phone: string
  pet_name: string
  pet_species: string
  pet_breed: string | null
  pet_age_years: string | number | null
  pet_care_notes: string | null
  need_description: string | null
  selected_addon_ids: string[]
  service_quote: {
    line_items: Array<{
      item_id: string
      display_name: string
      kind: 'base_service' | 'addon' | 'included_selection'
      pricing_semantics: string
      amount_min_cents: number | null
      amount_max_cents: number | null
    }>
    currency: string | null
    total_semantics: string
    total_min_cents: number | null
    total_max_cents: number | null
    legacy_price_note: string
  } | null
  starts_at: string
  ends_at: string
  merchant_time_zone: string
  customer_reference: string
  confirmation_code: string | null
  payment_expires_at: string | null
  paid_at: string | null
  payment_expired_at: string | null
  payment_method: string | null
  deposit_amount_cents: number | null
  deposit_currency: string | null
  deposit_application: 'applied_to_service_total' | 'application_unconfirmed' | null
  deposit_note: string | null
  payment_amount_cents: number | null
  payment_currency: string | null
  status: 'awaiting_payment' | 'confirmed' | 'payment_expired'
}

const backendBaseUrl = (import.meta.env.VITE_BACKEND_URL ?? '').replace(/\/$/, '')

export async function fetchSystemStatus(signal?: AbortSignal): Promise<SystemStatus> {
  const response = await fetch(`${backendBaseUrl}/api/system/status`, {
    headers: { Accept: 'application/json' },
    signal,
  })
  if (!response.ok) {
    throw new Error(`后端状态接口返回 HTTP ${response.status}`)
  }
  return response.json() as Promise<SystemStatus>
}

export async function fetchCustomerBooking(
  bookingReference: string,
  accessToken: string,
  signal?: AbortSignal,
): Promise<BookingRecord> {
  const response = await fetch(
    `${backendBaseUrl}/api/customer/bookings/${encodeURIComponent(bookingReference)}`,
    {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      signal,
    },
  )
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? '预约凭证无效或预约不存在'
        : `预约结果接口返回 HTTP ${response.status}`,
    )
  }
  const result = await response.json() as { booking: BookingRecord }
  return result.booking
}

export async function simulateCustomerPayment(
  bookingReference: string,
  accessToken: string,
): Promise<BookingRecord> {
  const response = await fetch(
    `${backendBaseUrl}/api/customer/bookings/${encodeURIComponent(bookingReference)}/simulate-payment`,
    {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
        'X-Payment-Event-ID': `web-payment-${crypto.randomUUID()}`,
      },
    },
  )
  if (!response.ok) {
    let code = ''
    try {
      const body = await response.json() as { detail?: { code?: string } }
      code = body.detail?.code ?? ''
    } catch {
      // A proxy may return a non-JSON error; preserve the HTTP error below.
    }
    throw new Error(
      code === 'payment_expired'
        ? '支付保留已过期，请重新预约'
        : response.status === 404
          ? '预约凭证无效或预约不存在'
          : `模拟支付接口返回 HTTP ${response.status}`,
    )
  }
  const result = await response.json() as { booking: BookingRecord }
  return result.booking
}
