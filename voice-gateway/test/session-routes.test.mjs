import assert from 'node:assert/strict'
import test from 'node:test'

import { createBookingSessionRouteInstaller } from '../src/session-routes.mjs'


test('start-new-booking route issues a new trusted session and preserves the previous binding', async () => {
  let handler
  let backendCall
  const app = {
    post(path, registered) {
      assert.equal(path, '/api/pet-reception/booking-sessions')
      handler = registered
    },
  }
  createBookingSessionRouteInstaller({
    toolClient: {
      startBookingSession: async (context, previousSessionId) => {
        backendCall = { context, previousSessionId }
        return { draft: { id: 'draft-new', version: 0 } }
      },
    },
  })({ app })

  let statusCode
  let payload
  const response = {
    status(value) { statusCode = value; return this },
    json(value) { payload = value },
  }
  await handler(
    {
      identity: { ownerId: 'user_signed-owner' },
      body: { previous_session_id: 'session-old' },
    },
    response,
  )

  assert.equal(statusCode, 201)
  assert.equal(payload.status, 'started')
  assert.match(payload.session_id, /^booking-[0-9a-f-]+$/)
  assert.notEqual(payload.session_id, 'session-old')
  assert.equal(backendCall.previousSessionId, 'session-old')
  assert.equal(backendCall.context.ownerId, 'user_signed-owner')
  assert.equal(backendCall.context.sessionId, payload.session_id)
})
