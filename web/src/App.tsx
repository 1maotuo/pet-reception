import { useCallback, useEffect, useState } from 'react'
import {
  fetchCustomerBooking,
  fetchSystemStatus,
  simulateCustomerPayment,
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
  try {
    return {
      kind: 'credential' as const,
      bookingReference: decodeURIComponent(match[1]),
      accessToken: new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '',
    }
  } catch {
    return { kind: 'invalid' as const }
  }
}

function formatBookingTime(value: string, timeZone: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'full',
    timeStyle: 'short',
    timeZone,
  }).format(new Date(value))
}

function CustomerBookingPage({ bookingReference, accessToken }: { bookingReference: string; accessToken: string }) {
  const [state, setState] = useState<BookingLoadState>({ kind: 'loading' })
  const [paymentPending, setPaymentPending] = useState(false)
  const [paymentError, setPaymentError] = useState('')

  useEffect(() => {
    if (!accessToken) {
      setState({ kind: 'error', message: '链接缺少预约访问凭证' })
      return
    }
    const controller = new AbortController()
    void fetchCustomerBooking(bookingReference, accessToken, controller.signal)
      .then((value) => setState({ kind: 'ready', value }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState({
          kind: 'error',
          message: error instanceof Error ? error.message : '无法读取预约结果',
        })
      })
    return () => controller.abort()
  }, [accessToken, bookingReference])

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
  const pay = async () => {
    setPaymentPending(true)
    setPaymentError('')
    try {
      const updated = await simulateCustomerPayment(bookingReference, accessToken)
      setState({ kind: 'ready', value: updated })
    } catch (error) {
      setPaymentError(error instanceof Error ? error.message : '模拟支付失败')
      try {
        const refreshed = await fetchCustomerBooking(bookingReference, accessToken)
        setState({ kind: 'ready', value: refreshed })
      } catch {
        // Keep the last verified record when the follow-up read also fails.
      }
    } finally {
      setPaymentPending(false)
    }
  }
  const isAwaitingPayment = booking.status === 'awaiting_payment'
  const isConfirmed = booking.status === 'confirmed'
  return (
    <main className="shell booking-shell">
      <p className="eyebrow">
        {isConfirmed ? '预约已确认' : isAwaitingPayment ? '等待模拟支付' : '支付保留已过期'}
      </p>
      <h1 className="booking-title">{booking.pet_name} 的服务安排</h1>
      <p className="lede">
        {isConfirmed
          ? '后端已经记录本地模拟支付成功，以下是正式确认记录。'
          : isAwaitingPayment
            ? '该时段正在为您临时保留。完成本地模拟支付后才会生成正式确认编号。此页面不会采集银行卡信息。'
            : '支付保留已超时释放；请返回语音前台重新查询空档。'}
      </p>

      {isAwaitingPayment && (
        <section className="payment-panel" aria-label="本地模拟支付">
          <p>本地开发验证：模拟一次支付成功事件，不连接真实支付平台。</p>
          <button type="button" disabled={paymentPending} onClick={() => void pay()}>
            {paymentPending ? '正在处理…' : '模拟支付成功'}
          </button>
          {paymentError && <p className="booking-error" role="alert">{paymentError}</p>}
        </section>
      )}

      <section className="booking-card" aria-label="预约详情">
        <div className="booking-code">
          <span>{isConfirmed ? '确认编号' : '预约参考号'}</span>
          <strong>{booking.confirmation_code ?? booking.customer_reference}</strong>
        </div>
        <dl className="booking-details">
          <div><dt>商家</dt><dd>{booking.merchant_display_name}</dd></div>
          <div><dt>服务</dt><dd>{booking.service_name_zh}</dd></div>
          <div><dt>时间</dt><dd>{formatBookingTime(booking.starts_at, booking.merchant_time_zone)}</dd></div>
          <div><dt>服务人员</dt><dd>{booking.staff_display_name}</dd></div>
          <div><dt>客户</dt><dd>{booking.customer_name}</dd></div>
          <div><dt>联系电话</dt><dd>{booking.contact_phone}</dd></div>
          <div><dt>宠物</dt><dd>{booking.pet_name} · {booking.pet_species}{booking.pet_breed ? ` · ${booking.pet_breed}` : ''}</dd></div>
          {isAwaitingPayment && booking.payment_expires_at && <div><dt>保留至</dt><dd>{formatBookingTime(booking.payment_expires_at, booking.merchant_time_zone)}</dd></div>}
          {booking.need_description && <div><dt>本次需求</dt><dd>{booking.need_description}</dd></div>}
          {booking.pet_care_notes && <div><dt>照护备注</dt><dd>{booking.pet_care_notes}</dd></div>}
        </dl>
      </section>
      <p className="credential-note">请妥善保存此链接。链接中的访问凭证不会写入页面正文，也不会由前端持久化；模拟支付事件不包含真实支付信息。</p>
    </main>
  )
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span aria-hidden="true" className={`status-dot ${ok ? 'status-dot--ok' : 'status-dot--warn'}`} />
}

function SystemStatusPage() {
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

export default function App() {
  const bookingLocation = bookingCredentialFromLocation()
  if (bookingLocation?.kind === 'credential') {
    return (
      <CustomerBookingPage
        bookingReference={bookingLocation.bookingReference}
        accessToken={bookingLocation.accessToken}
      />
    )
  }
  if (bookingLocation?.kind === 'invalid') {
    return (
      <main className="shell booking-shell">
        <p className="eyebrow">预约结果</p>
        <h1 className="booking-title">无法显示预约</h1>
        <p className="booking-error" role="alert">预约链接格式无效</p>
      </main>
    )
  }
  return <SystemStatusPage />
}
