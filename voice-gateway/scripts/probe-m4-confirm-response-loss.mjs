import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { createServer } from 'node:http'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'
import { MerchantCoordinator, MerchantToolClient } from '../src/merchant-tool.mjs'

const gatewayRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(gatewayRoot, '..')
dotenv.config({ path: resolve(projectRoot, '.env'), quiet: true })

function required(name) {
  const value = String(process.env[name] || '').trim()
  if (!value) throw new Error(`${name} is required for the response-loss probe`)
  return value
}

function futureTuesday(weeks) {
  const value = new Date()
  value.setUTCDate(value.getUTCDate() + (weeks * 7))
  while (value.getUTCDay() !== 2) value.setUTCDate(value.getUTCDate() + 1)
  return value.toISOString().slice(0, 10)
}

function proxyHeaders(headers) {
  const result = new Headers()
  for (const [name, value] of Object.entries(headers)) {
    if (['connection', 'content-length', 'host', 'transfer-encoding'].includes(name)) continue
    if (Array.isArray(value)) value.forEach((item) => result.append(name, item))
    else if (value !== undefined) result.set(name, value)
  }
  return result
}

async function readBody(request) {
  const chunks = []
  for await (const chunk of request) chunks.push(chunk)
  return chunks.length ? Buffer.concat(chunks) : undefined
}

const backendBaseUrl = String(
  process.env.FAULT_PROBE_BACKEND_BASE_URL || 'http://127.0.0.1:8021',
).replace(/\/$/, '')
let droppedConfirmResponses = 0
let confirmRequests = 0
let statusRequests = 0

const proxy = createServer(async (request, response) => {
  try {
    const body = await readBody(request)
    const isConfirmation = request.method === 'POST'
      && /^\/api\/internal\/voice\/booking-preparations\/[^/]+\/confirm$/.test(request.url)
    const isStatus = request.method === 'GET'
      && /^\/api\/internal\/voice\/booking-operations\/[^/]+$/.test(request.url)
    if (isConfirmation) confirmRequests += 1
    if (isStatus) statusRequests += 1

    const upstream = await fetch(new URL(request.url, backendBaseUrl), {
      method: request.method,
      headers: proxyHeaders(request.headers),
      ...(body === undefined ? {} : { body }),
    })
    const payload = Buffer.from(await upstream.arrayBuffer())
    if (isConfirmation && droppedConfirmResponses === 0 && upstream.ok) {
      droppedConfirmResponses += 1
      request.socket.destroy()
      return
    }
    response.writeHead(upstream.status, Object.fromEntries(upstream.headers.entries()))
    response.end(payload)
  } catch (error) {
    if (!response.headersSent) response.writeHead(502, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ error: error?.name || 'proxy_error' }))
  }
})

await new Promise((resolvePromise) => proxy.listen(0, '127.0.0.1', resolvePromise))
const proxyAddress = proxy.address()
const proxyBaseUrl = `http://127.0.0.1:${proxyAddress.port}`

try {
  const suffix = randomUUID().replaceAll('-', '').slice(0, 12)
  const baseContext = {
    ownerId: `loss_probe_owner_${suffix}`,
    sessionId: `loss_probe_session_${suffix}`,
  }
  const context = (turn) => ({ ...baseContext, turnId: `loss_probe_${turn}_${suffix}` })
  const client = new MerchantToolClient({
    backendBaseUrl: proxyBaseUrl,
    merchantId: required('DEMO_MERCHANT_ID'),
    serviceToken: required('VOICE_GATEWAY_SERVICE_TOKEN'),
  })
  const coordinator = new MerchantCoordinator({ toolClient: client })

  let searched
  let selectedWeek = 18
  await coordinator.runBusinessTool('update_booking_draft', {
    customer_name: '响应丢失探针客户',
    contact_phone: '+86-138-5555-5555',
    pet_name: '探针团子',
    pet_species: '犬',
    pet_breed: '贵宾犬',
    pet_age_text: '四个月',
    service_id: 'demo-bath',
    preferred_date: futureTuesday(selectedWeek),
    preferred_time_of_day: 'morning',
    staff_preference_text: 'Lisa',
    staff_id: 'demo-staff-lisa',
  }, context('draft'))
  for (; selectedWeek <= 60; selectedWeek += 1) {
    if (selectedWeek !== 18) {
      await coordinator.runBusinessTool('update_booking_draft', {
        preferred_date: futureTuesday(selectedWeek),
      }, context(`date_${selectedWeek}`))
    }
    searched = await coordinator.runBusinessTool(
      'search_availability',
      {},
      context(`search_${selectedWeek}`),
    )
    if (searched.output.search.status === 'available') break
  }
  assert.equal(searched.output.search.status, 'available')
  const candidate = searched.output.search.candidates[0]
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: candidate.id },
    context('prepare'),
  )
  await coordinator.markBookingPresented({
    preparationId: prepared.presentation.preparationId,
    eventId: prepared.presentation.eventId,
  }, {
    ...context('prepare'),
    transcript: prepared.presentation.speech,
  })

  const recovered = await coordinator.runBusinessTool(
    'confirm_booking',
    { preparation_id: prepared.presentation.preparationId },
    { ...context('confirm'), invocationId: `loss_probe_confirm_${suffix}` },
  )
  assert.equal(recovered.output.status, 'awaiting_payment')
  assert.equal(confirmRequests, 1)
  assert.equal(droppedConfirmResponses, 1)
  assert.equal(statusRequests, 1)

  const listResponse = await fetch(`${backendBaseUrl}/api/internal/merchant/bookings`, {
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${required('MERCHANT_ADMIN_TOKEN')}`,
      'X-Merchant-ID': required('DEMO_MERCHANT_ID'),
    },
  })
  assert.equal(listResponse.ok, true)
  const list = await listResponse.json()
  const sameOperation = list.bookings.filter(
    (booking) => booking.operation_id === recovered.output.operation_id,
  )
  assert.equal(sameOperation.length, 1)
  assert.equal(sameOperation[0].id, recovered.output.booking.id)

  console.log(JSON.stringify({
    result: 'passed',
    backend: 'independent-test-database',
    dropped_confirm_responses: droppedConfirmResponses,
    confirm_post_requests: confirmRequests,
    recovery_status_queries: statusRequests,
    recovered_status: recovered.output.status,
    bookings_for_operation: sameOperation.length,
    operation_reused: true,
  }, null, 2))
} finally {
  await new Promise((resolvePromise, reject) => proxy.close((error) => (
    error ? reject(error) : resolvePromise()
  )))
}
