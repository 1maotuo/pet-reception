import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  vi.unstubAllGlobals()
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
  })

  it('does not claim success when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network offline')))

    render(<App />)

    expect(await screen.findByText('连接失败')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('network offline')
  })
})
