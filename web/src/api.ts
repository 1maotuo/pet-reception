export type DatabaseStatus = {
  status: 'connected' | 'unavailable' | 'not_configured'
  detail: string
}

export type SystemStatus = {
  service: 'pet-reception-backend'
  status: 'ok' | 'degraded'
  database: DatabaseStatus
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
