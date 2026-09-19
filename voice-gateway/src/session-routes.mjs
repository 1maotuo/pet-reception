import { randomUUID } from 'node:crypto'

const SESSION_ID = /^[A-Za-z0-9._:-]{1,128}$/

export function createBookingSessionRouteInstaller({ toolClient }) {
  return ({ app }) => {
    app.post('/api/pet-reception/booking-sessions', async (req, res) => {
      const previousSessionId = String(req.body?.previous_session_id || '').trim()
      if (previousSessionId && !SESSION_ID.test(previousSessionId)) {
        res.status(400).json({
          error: 'invalid_previous_session',
          message: '当前页面会话标识无效，请刷新页面后重试。',
        })
        return
      }
      const sessionId = `booking-${randomUUID()}`
      const context = {
        ownerId: req.identity.ownerId,
        sessionId,
        turnId: `start-${randomUUID()}`,
      }
      try {
        const result = await toolClient.startBookingSession(context, previousSessionId || null)
        res.status(201).json({
          status: 'started',
          session_id: sessionId,
          draft_id: result.draft.id,
          draft_version: result.draft.version,
        })
      } catch (error) {
        res.status(Number(error?.status) >= 400 ? Number(error.status) : 502).json({
          error: error?.code || 'booking_session_start_failed',
          message: '没有开始新的预约，会继续保留当前预约会话。',
        })
      }
    })
  }
}
