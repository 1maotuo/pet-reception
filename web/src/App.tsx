import { useCallback, useEffect, useState } from 'react'
import {
  fetchCustomerBooking,
  fetchSystemStatus,
  type BookingRecord,
  type SystemStatus,
} from './api'
import './styles.css'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; value: SystemStatus }
  | { kind: 'error'; message: string }

const voiceGatewayUrl = import.meta.env.VITE_VOICE_GATEWAY_URL ?? 'http://127.0.0.1:3101'
const backendDocsUrl = import.meta.env.VITE_BACKEND_DOCS_URL ?? 'http://127.0.0.1:8010/docs'

type BookingLoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; value: BookingRecord }
  | { kind: 'error'; message: string }

function bookingCredentialFromLocation() {
  const match = window.location.pathname.match(/^\/booking\/([^/]+)\/?$/)
  if (!match) return null
  return {
    confirmationCode: decodeURIComponent(match[1]),
    accessToken: new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '',
  }
}

function formatBookingTime(value: string, timeZone: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'full',
    timeStyle: 'short',
    timeZone,
  }).format(new Date(value))
}

function CustomerBookingPage({ confirmationCode, accessToken }: { confirmationCode: string; accessToken: string }) {
  const [state, setState] = useState<BookingLoadState>({ kind: 'loading' })

  useEffect(() => {
    if (!accessToken) {
      setState({ kind: 'error', message: '链接缺少预约访问凭证' })
      return
    }
    const controller = new AbortController()
    void fetchCustomerBooking(confirmationCode, accessToken, controller.signal)
      .then((value) => setState({ kind: 'ready', value }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState({
          kind: 'error',
          message: error instanceof Error ? error.message : '无法读取预约结果',
        })
      })
    return () => controller.abort()
  }, [accessToken, confirmationCode])

  if (state.kind !== 'ready') {
    return (
      <main className="shell booking-shell">
        <p className="eyebrow">预约结果</p>
        <h1 className="booking-title">{state.kind === 'loading' ? '正在核对预约…' : '无法显示预约'}</h1>
        {state.kind === 'error' && <p className="booking-error" role="alert">{state.message}</p>}
      </main>
    )
  }

  const booking = state.value
  return (
    <main className="shell booking-shell">
      <p className="eyebrow">预约已确认</p>
      <h1 className="booking-title">{booking.pet_name} 的服务安排</h1>
      <p className="lede">此页面通过专属凭证读取后端中已提交的预约记录，不是浏览器生成的成功页面。</p>

      <section className="booking-card" aria-label="预约详情">
        <div className="booking-code">
          <span>确认编号</span>
          <strong>{booking.confirmation_code}</strong>
        </div>
        <dl className="booking-details">
          <div><dt>商家</dt><dd>{booking.merchant_display_name}</dd></div>
          <div><dt>服务</dt><dd>{booking.service_name_zh}</dd></div>
          <div><dt>时间</dt><dd>{formatBookingTime(booking.starts_at, booking.merchant_time_zone)}</dd></div>
          <div><dt>服务人员</dt><dd>{booking.staff_display_name}</dd></div>
          <div><dt>客户</dt><dd>{booking.customer_name}</dd></div>
          <div><dt>联系电话</dt><dd>{booking.contact_phone}</dd></div>
          <div><dt>宠物</dt><dd>{booking.pet_name} · {booking.pet_species}{booking.pet_breed ? ` · ${booking.pet_breed}` : ''}</dd></div>
          {booking.need_description && <div><dt>本次需求</dt><dd>{booking.need_description}</dd></div>}
          {booking.pet_care_notes && <div><dt>照护备注</dt><dd>{booking.pet_care_notes}</dd></div>}
        </dl>
      </section>
      <p className="credential-note">请妥善保存此链接。链接中的访问凭证不会写入页面正文，也不会由前端持久化。</p>
    </main>
  )
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span aria-hidden="true" className={`status-dot ${ok ? 'status-dot--ok' : 'status-dot--warn'}`} />
}

export default function App() {
  const bookingCredential = bookingCredentialFromLocation()
  if (bookingCredential) {
    return <CustomerBookingPage {...bookingCredential} />
  }

  const [state, setState] = useState<LoadState>({ kind: 'loading' })

  const refresh = useCallback((signal?: AbortSignal) => {
    setState({ kind: 'loading' })
    void fetchSystemStatus(signal)
      .then((value) => setState({ kind: 'ready', value }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState({
          kind: 'error',
          message: error instanceof Error ? error.message : '无法连接后端',
        })
      })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    refresh(controller.signal)
    return () => controller.abort()
  }, [refresh])

  const backendOnline = state.kind === 'ready'
  const databaseOnline = state.kind === 'ready' && state.value.database.status === 'connected'

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">M3 工程验证</p>
        <h1>中文宠物服务语音前台</h1>
        <p className="lede">
          当前可用真实商家、会话草稿、数据库排班查询，以及带确认门槛的幂等预约写入。客户结果页只显示后端已提交的同一条预约记录。
        </p>
      </section>

      <section aria-label="服务状态" className="status-grid">
        <article className="status-card">
          <div className="status-card__title">
            <StatusDot ok={backendOnline} />
            <h2>业务后端</h2>
          </div>
          <p>{backendOnline ? '已连接 FastAPI' : state.kind === 'loading' ? '正在检查…' : '连接失败'}</p>
          {state.kind === 'error' && <small role="alert">{state.message}</small>}
        </article>

        <article className="status-card">
          <div className="status-card__title">
            <StatusDot ok={databaseOnline} />
            <h2>PostgreSQL</h2>
          </div>
          <p>
            {state.kind === 'ready'
              ? state.value.database.status === 'connected' ? '连接正常' : '当前不可用'
              : '等待后端状态'}
          </p>
          {state.kind === 'ready' && <small>{state.value.database.detail}</small>}
        </article>

        <article className="status-card">
          <div className="status-card__title">
            <span aria-hidden="true" className="status-dot status-dot--neutral" />
            <h2>实时语音</h2>
          </div>
          <p>由 Qwen Audio Agent 独立提供</p>
          <a href={voiceGatewayUrl} rel="noreferrer" target="_blank">打开语音客户端</a>
        </article>

        <article className="status-card">
          <div className="status-card__title">
            <StatusDot ok={databaseOnline} />
            <h2>预约事务</h2>
          </div>
          <p>{databaseOnline ? 'M3 API 可测试' : '等待数据库连接'}</p>
          <a href={backendDocsUrl} rel="noreferrer" target="_blank">打开 API 文档</a>
        </article>
      </section>

      <div className="actions">
        <button onClick={() => refresh()} type="button">重新检查</button>
        <span>后端状态来自实时 HTTP 请求，不使用前端假数据。</span>
      </div>
    </main>
  )
}
