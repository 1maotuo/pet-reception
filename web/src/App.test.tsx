import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
    window.history.replaceState({}, '', '/booking/BK-ABC123#token=customer-secret')
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
          customer_reference: 'BK-ABC123',
          confirmation_code: 'PR-ABC123',
          payment_expires_at: '2026-12-01T16:30:00Z',
          paid_at: '2026-09-20T00:00:00Z',
          payment_expired_at: null,
          payment_method: 'local_simulation',
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
      '/api/customer/bookings/BK-ABC123',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer customer-secret' }),
      }),
    )
    expect(document.body).not.toHaveTextContent('customer-secret')
  })

  it('shows a payment hold and only claims confirmation after simulated payment succeeds', async () => {
    window.history.replaceState({}, '', '/booking/BK-PENDING#token=customer-secret')
    const pendingBooking = {
      id: 'booking-pending',
      merchant_display_name: 'Paws & Bubbles 宠物美容（演示）',
      staff_display_name: 'Lisa',
      service_name_zh: '基础洗护',
      customer_name: '王女士',
      contact_phone: '+86-138-0000-0000',
      pet_name: '豆包',
      pet_species: '犬',
      pet_breed: null,
      pet_age_years: null,
      pet_care_notes: null,
      need_description: null,
      selected_addon_ids: ['teeth-brushing'],
      service_quote: {
        line_items: [
          { item_id: 'service', display_name: '基础洗护', kind: 'base_service', pricing_semantics: 'fixed', amount_min_cents: 6000, amount_max_cents: 6000 },
          { item_id: 'teeth-brushing', display_name: '刷牙', kind: 'addon', pricing_semantics: 'fixed', amount_min_cents: 1200, amount_max_cents: 1200 },
        ],
        currency: 'USD', total_semantics: 'fixed', total_min_cents: 7200,
        total_max_cents: 7200, legacy_price_note: 'synthetic',
      },
      starts_at: '2026-12-01T17:00:00Z',
      ends_at: '2026-12-01T18:00:00Z',
      merchant_time_zone: 'America/Los_Angeles',
      customer_reference: 'BK-PENDING',
      confirmation_code: null,
      payment_expires_at: '2026-12-01T16:30:00Z',
      paid_at: null,
      payment_expired_at: null,
      payment_method: null,
      deposit_amount_cents: 2000,
      deposit_currency: 'USD',
      deposit_application: 'applied_to_service_total',
      deposit_note: '测试预约金',
      payment_amount_cents: null,
      payment_currency: null,
      status: 'awaiting_payment',
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ booking: pendingBooking }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          booking: {
            ...pendingBooking,
            confirmation_code: 'PR-CONFIRMED',
            paid_at: '2026-09-20T00:00:00Z',
            payment_method: 'local_simulation',
            payment_amount_cents: 2000,
            payment_currency: 'USD',
            status: 'confirmed',
          },
        }),
      })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    expect(await screen.findByText('等待模拟支付')).toBeInTheDocument()
    expect(screen.queryByText('预约已确认')).not.toBeInTheDocument()
    expect(screen.getByText('US$72.00')).toBeInTheDocument()
    expect(screen.getByText('刷牙')).toBeInTheDocument()
    expect(screen.getByText('US$20.00')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '模拟支付预约金' }))
    expect(await screen.findByText('预约已确认')).toBeInTheDocument()
    expect(screen.getByText('PR-CONFIRMED')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/customer/bookings/BK-PENDING/simulate-payment',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer customer-secret',
          'X-Payment-Event-ID': expect.stringMatching(/^web-payment-/),
        }),
      }),
    )
  })

  it('does not render a success result for an invalid booking credential', async () => {
    window.history.replaceState({}, '', '/booking/PR-NOT-FOUND#token=wrong')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('预约凭证无效或预约不存在')
    expect(screen.queryByText('预约已确认')).not.toBeInTheDocument()
  })

  it('shows a controlled error for a malformed encoded booking URL', () => {
    window.history.replaceState({}, '', '/booking/%#token=unused')

    render(<App />)

    expect(screen.getByRole('alert')).toHaveTextContent('预约链接格式无效')
  })
})
