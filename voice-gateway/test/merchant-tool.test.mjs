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
        services: [{ name_zh: '基础洗护', price_note: '$55 起' }],
      },
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
    assert.match(result.content, /演示美容店/)
    assert.match(result.content, /不能据此判断具体适用性，需由商家确认/)
    assert.match(result.metadata.presentation.inline.content, /资料边界/)
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
