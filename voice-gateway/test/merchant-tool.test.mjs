import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'
import { MerchantCoordinator, MerchantToolClient } from '../src/merchant-tool.mjs'

async function listen(handler) {
  const server = createServer(handler)
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  return {
    origin: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  }
}

test('merchant tool sends server-bound merchant and gateway session context', async () => {
  let receivedHeaders
  const backend = await listen((request, response) => {
    receivedHeaders = request.headers
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({
      merchant: {
        id: 'demo-grooming-001',
        display_name: '演示美容店',
        address: '123 Demo Street',
        phone: '+1-415-555-0142',
        time_zone: 'America/Los_Angeles',
        source: 'm1-static-demo-catalog:demo-grooming-001',
        business_hours: {
          weekly_hours: [{ weekday: 0, opens_at: '09:00:00', closes_at: '17:00:00' }],
          closed_weekdays: [6],
          note: '周一至周六上午 9 点至下午 5 点',
          source: 'test:hours',
        },
        staff: [{
          id: 'demo-staff-lisa',
          display_name: 'Lisa',
          service_ids: ['demo-bath'],
          public_intro: '犬类洗护人员',
          specialties: ['犬类基础洗护'],
          source: 'test:lisa',
        }],
        services: [{
          id: 'demo-bath',
          name_zh: '基础洗护',
          duration_minutes: 60,
          price_note: '$55 起',
          supported_species: ['犬'],
          included_items: ['洗澡', '吹干'],
          recommended_for: ['日常清洁'],
          assessment_required_for: ['毛发打结'],
          not_included: ['医疗诊断'],
          source: 'test:demo-bath',
        }],
      },
      merchant_local_date: '2026-09-19',
      merchant_local_datetime: '2026-09-19T10:00:00-07:00',
      context: {
        merchant_id: 'demo-grooming-001',
        owner_id: request.headers['x-voice-owner-id'],
        voice_session_id: request.headers['x-voice-session-id'],
        turn_id: request.headers['x-voice-turn-id'],
      },
    }))
  })

  try {
    const toolClient = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
    })
    const coordinator = new MerchantCoordinator({ toolClient })
    const result = await coordinator.run({}, {
      ownerId: 'user_signed-owner',
      sessionId: 'session-a',
      turnId: 'turn-a',
    })

    assert.equal(receivedHeaders.authorization, 'Bearer test-service-token')
    assert.equal(receivedHeaders['x-merchant-id'], 'demo-grooming-001')
    assert.equal(receivedHeaders['x-voice-owner-id'], 'user_signed-owner')
    assert.equal(receivedHeaders['x-voice-session-id'], 'session-a')
    assert.equal(receivedHeaders['x-voice-turn-id'], 'turn-a')
    const facts = JSON.parse(result.content)
    assert.equal(facts.type, 'trusted_merchant_public_info')
    assert.equal(facts.merchant.display_name, '演示美容店')
    assert.equal(facts.merchant.phone, '+1-415-555-0142')
    assert.equal(facts.merchant.address, '123 Demo Street')
    assert.equal(facts.merchant.services[0].duration_minutes, 60)
    assert.equal(facts.merchant.services[0].price_note, '$55 起')
    assert.deepEqual(facts.merchant.services[0].supported_species, ['犬'])
    assert.equal(facts.merchant.local_date, '2026-09-19')
    assert.match(facts.merchant.business_hours.note, /下午 5 点/)
    assert.deepEqual(facts.merchant.staff[0].specialties, ['犬类基础洗护'])
    assert.match(facts.relative_date_rule, /merchant\.local_date/)
    assert.equal(Object.hasOwn(facts, 'availability_checked'), false)
    assert.equal(Object.hasOwn(facts, 'booking_created'), false)
    assert.equal(facts.current_state.merchant.local_date, '2026-09-19')
    assert.equal(facts.effect.type, 'merchant_information_read')
    assert.equal(facts.next_step.type, 'answer_customer_question')
    assert.match(result.metadata.presentation.inline.content, /资料边界/)
    assert.match(result.metadata.presentation.inline.content, /约 60 分钟/)
    assert.match(result.metadata.presentation.inline.content, /营业时间/)
    assert.match(result.metadata.presentation.inline.content, /Lisa/)
    assert.equal(result.metadata.source, 'm1-static-demo-catalog:demo-grooming-001')
  } finally {
    await backend.close()
  }
})

test('merchant tool refuses missing trusted gateway context', async () => {
  const client = new MerchantToolClient({
    backendBaseUrl: 'http://127.0.0.1:1',
    merchantId: 'demo-grooming-001',
    serviceToken: 'test-service-token',
  })

  await assert.rejects(
    client.getMerchantInfo({ ownerId: '', sessionId: 'session-a', turnId: 'turn-a' }),
    /trusted ownerId/,
  )
})

test('a pre-cancelled merchant query does not send an HTTP request', async () => {
  let requests = 0
  const backend = await listen((_request, response) => {
    requests += 1
    response.end('{}')
  })
  const controller = new AbortController()
  controller.abort()

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
    })
    await assert.rejects(
      client.getMerchantInfo({
        ownerId: 'user_signed-owner',
        sessionId: 'session-a',
        turnId: 'turn-a',
        signal: controller.signal,
      }),
      { name: 'AbortError' },
    )
    assert.equal(requests, 0)
  } finally {
    await backend.close()
  }
})

test('cancelling an in-flight merchant query aborts response reading', async () => {
  let requestStarted
  const started = new Promise((resolve) => { requestStarted = resolve })
  const backend = await listen((_request, response) => {
    requestStarted()
    setTimeout(() => {
      if (!response.destroyed) response.end('{}')
    }, 250)
  })
  const controller = new AbortController()

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
    })
    const query = client.getMerchantInfo({
      ownerId: 'user_signed-owner',
      sessionId: 'session-a',
      turnId: 'turn-a',
      signal: controller.signal,
    })
    await started
    controller.abort()
    await assert.rejects(query, { name: 'AbortError' })
  } finally {
    await backend.close()
  }
})

test('merchant HTTP errors are reported instead of returning fake facts', async () => {
  const backend = await listen((_request, response) => {
    response.statusCode = 500
    response.end('backend failed')
  })

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
    })
    await assert.rejects(
      client.getMerchantInfo({
        ownerId: 'user_signed-owner',
        sessionId: 'session-a',
        turnId: 'turn-a',
      }),
      /merchant tool request failed with HTTP 500/,
    )
  } finally {
    await backend.close()
  }
})

test('a slow merchant HTTP request stops at the configured timeout', async () => {
  const backend = await listen(() => {})

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
      requestTimeoutMs: 25,
    })
    await assert.rejects(
      client.getMerchantInfo({
        ownerId: 'user_signed-owner',
        sessionId: 'session-a',
        turnId: 'turn-a',
      }),
      { name: 'TimeoutError' },
    )
  } finally {
    await backend.close()
  }
})

test('a timed-out write is reported as result_unknown instead of definite failure', async () => {
  const backend = await listen(() => {})

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
      requestTimeoutMs: 25,
    })
    await assert.rejects(
      client.updateBookingDraft({
        ownerId: 'user_signed-owner',
        sessionId: 'session-a',
        turnId: 'turn-a',
      }, { expected_version: 0, pet_name: '豆包' }),
      { name: 'BusinessToolResultUnknownError', code: 'result_unknown' },
    )
  } finally {
    await backend.close()
  }
})

test('write response timeout after HTTP 200 is still reported as result_unknown', async () => {
  const backend = await listen((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.write('{"draft":')
    setTimeout(() => response.end('{}'), 250)
  })

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
      requestTimeoutMs: 25,
    })
    await assert.rejects(
      client.updateBookingDraft({
        ownerId: 'user_signed-owner', sessionId: 'session-a', turnId: 'turn-a',
      }, { expected_version: 0, pet_name: '豆包' }),
      { name: 'BusinessToolResultUnknownError', code: 'result_unknown' },
    )
  } finally {
    await backend.close()
  }
})

test('truncated successful write response is not treated as a successful null result', async () => {
  const backend = await listen((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end('{"draft":')
  })

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
    })
    await assert.rejects(
      client.updateBookingDraft({
        ownerId: 'user_signed-owner', sessionId: 'session-a', turnId: 'turn-a',
      }, { expected_version: 0, pet_name: '豆包' }),
      { name: 'BusinessToolResultUnknownError', code: 'result_unknown' },
    )
  } finally {
    await backend.close()
  }
})

test('a socket reset after a confirmation write is reported as result_unknown', async () => {
  const backend = await listen((request) => {
    request.resume()
    request.on('end', () => request.socket.destroy())
  })

  try {
    const client = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
    })
    await assert.rejects(
      client.confirmBooking({
        ownerId: 'user_signed-owner', sessionId: 'session-a', turnId: 'turn-confirm',
      }, '22222222-2222-4222-8222-222222222222', 2, 'confirmation_event_1'),
      { name: 'BusinessToolResultUnknownError', code: 'result_unknown' },
    )
  } finally {
    await backend.close()
  }
})

test('programmer TypeError and explicit business conflict are not transport unknowns', async () => {
  const typeError = new TypeError('invalid request arguments')
  const invalidClient = new MerchantToolClient({
    backendBaseUrl: 'http://127.0.0.1:1',
    merchantId: 'demo-grooming-001',
    serviceToken: 'test-service-token',
    fetchImpl: async () => { throw typeError },
  })
  await assert.rejects(
    invalidClient.confirmBooking({
      ownerId: 'user_signed-owner', sessionId: 'session-a', turnId: 'turn-confirm',
    }, '22222222-2222-4222-8222-222222222222', 2, 'confirmation_event_1'),
    (error) => error === typeError,
  )

  const backend = await listen((_request, response) => {
    response.writeHead(409, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ detail: { code: 'conflict', reason: 'test conflict' } }))
  })
  try {
    const conflictClient = new MerchantToolClient({
      backendBaseUrl: backend.origin,
      merchantId: 'demo-grooming-001',
      serviceToken: 'test-service-token',
    })
    await assert.rejects(
      conflictClient.confirmBooking({
        ownerId: 'user_signed-owner', sessionId: 'session-a', turnId: 'turn-confirm',
      }, '22222222-2222-4222-8222-222222222222', 2, 'confirmation_event_1'),
      { name: 'BusinessToolHttpError', status: 409, code: 'conflict' },
    )
  } finally {
    await backend.close()
  }
})
