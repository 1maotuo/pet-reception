import { useCallback, useEffect, useState } from 'react'
import { fetchSystemStatus, type SystemStatus } from './api'
import './styles.css'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; value: SystemStatus }
  | { kind: 'error'; message: string }

const voiceGatewayUrl = import.meta.env.VITE_VOICE_GATEWAY_URL ?? 'http://127.0.0.1:3101'

function StatusDot({ ok }: { ok: boolean }) {
  return <span aria-hidden="true" className={`status-dot ${ok ? 'status-dot--ok' : 'status-dot--warn'}`} />
}

export default function App() {
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
        <p className="eyebrow">M0 · M1 工程验证</p>
        <h1>中文宠物服务语音前台</h1>
        <p className="lede">
          当前页面只展示真实运行状态。预约写入尚未实现，也不会生成模拟预约编号。
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
      </section>

      <div className="actions">
        <button onClick={() => refresh()} type="button">重新检查</button>
        <span>后端状态来自实时 HTTP 请求，不使用前端假数据。</span>
      </div>
    </main>
  )
}
