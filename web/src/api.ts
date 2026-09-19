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
  starts_at: string
  ends_at: string
  merchant_time_zone: string
  confirmation_code: string
  status: 'confirmed'
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
  confirmationCode: string,
  accessToken: string,
  signal?: AbortSignal,
): Promise<BookingRecord> {
  const response = await fetch(
    `${backendBaseUrl}/api/customer/bookings/${encodeURIComponent(confirmationCode)}`,
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
