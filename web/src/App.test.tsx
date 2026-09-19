import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', '/')
})

describe('App', () => {
  it('renders the real backend and database status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        service: 'pet-reception-backend',
        status: 'ok',
        database: { status: 'connected', detail: 'PostgreSQL 连接正常' },
      }),
    }))

    render(<App />)

    expect(await screen.findByText('已连接 FastAPI')).toBeInTheDocument()
    expect(screen.getByText('连接正常')).toBeInTheDocument()
    expect(screen.getByText('PostgreSQL 连接正常')).toBeInTheDocument()
    expect(screen.getByText('M3 API 可测试')).toBeInTheDocument()
  })

  it('does not claim success when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network offline')))

    render(<App />)

    expect(await screen.findByText('连接失败')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('network offline')
  })

  it('renders a confirmed booking only from the credential-protected backend record', async () => {
    window.history.replaceState({}, '', '/booking/PR-ABC123#token=customer-secret')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        booking: {
          id: 'booking-1',
          merchant_display_name: 'Paws & Bubbles 宠物美容（演示）',
          staff_display_name: 'Lisa',
          service_name_zh: '基础洗护',
          customer_name: '王女士',
          contact_phone: '+86-138-0000-0000',
          pet_name: '豆包',
          pet_species: '犬',
          pet_breed: '贵宾犬',
          pet_age_years: 3,
          pet_care_notes: '耳朵敏感',
          need_description: '洗护并检查轻微打结',
          starts_at: '2026-12-01T17:00:00Z',
          ends_at: '2026-12-01T18:00:00Z',
          merchant_time_zone: 'America/Los_Angeles',
          confirmation_code: 'PR-ABC123',
          status: 'confirmed',
        },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    expect(await screen.findByText('豆包 的服务安排')).toBeInTheDocument()
    expect(screen.getByText('PR-ABC123')).toBeInTheDocument()
    expect(screen.getByText('基础洗护')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/customer/bookings/PR-ABC123',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer customer-secret' }),
      }),
    )
    expect(document.body).not.toHaveTextContent('customer-secret')
  })

  it('does not render a success result for an invalid booking credential', async () => {
    window.history.replaceState({}, '', '/booking/PR-NOT-FOUND#token=wrong')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('预约凭证无效或预约不存在')
    expect(screen.queryByText('预约已确认')).not.toBeInTheDocument()
  })
})
