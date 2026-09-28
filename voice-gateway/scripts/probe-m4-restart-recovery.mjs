import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'
import {
  BookingSummaryPlaybackRequiredError,
  MerchantCoordinator,
  MerchantToolClient,
} from '../src/merchant-tool.mjs'

const gatewayRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(gatewayRoot, '..')
dotenv.config({ path: resolve(projectRoot, '.env'), quiet: true })

function required(name) {
  const value = String(process.env[name] || '').trim()
  if (!value) throw new Error(`${name} is required for the restart recovery probe`)
  return value
}

function futureTuesday(weeks) {
  const value = new Date()
  value.setUTCDate(value.getUTCDate() + (weeks * 7))
  while (value.getUTCDay() !== 2) value.setUTCDate(value.getUTCDate() + 1)
  return value.toISOString().slice(0, 10)
}

const backendBaseUrl = String(
  process.env.FAULT_PROBE_BACKEND_BASE_URL || 'http://127.0.0.1:8021',
).replace(/\/$/, '')
const suffix = randomUUID().replaceAll('-', '').slice(0, 12)
const baseContext = {
  ownerId: `restart_probe_owner_${suffix}`,
  sessionId: `restart_probe_session_${suffix}`,
}
const context = (turn) => ({ ...baseContext, turnId: `restart_probe_${turn}_${suffix}` })
const client = new MerchantToolClient({
  backendBaseUrl,
  merchantId: required('DEMO_MERCHANT_ID'),
  serviceToken: required('VOICE_GATEWAY_SERVICE_TOKEN'),
})

let confirmPostRequests = 0
const confirmBooking = client.confirmBooking.bind(client)
client.confirmBooking = (...args) => {
  confirmPostRequests += 1
  return confirmBooking(...args)
}

const originalCoordinator = new MerchantCoordinator({ toolClient: client })
let searched
let selectedWeek = 32
await originalCoordinator.runBusinessTool('update_booking_draft', {
  customer_name: '网关重启探针客户',
  contact_phone: '+86-138-6666-6666',
  pet_name: '重启团子',
  pet_species: '犬',
  pet_breed: '贵宾犬',
  pet_age_text: '四个月',
  service_id: 'demo-bath',
  preferred_date: futureTuesday(selectedWeek),
  preferred_time_of_day: 'morning',
  staff_preference_text: 'Lisa',
  staff_id: 'demo-staff-lisa',
}, context('draft'))
for (; selectedWeek <= 75; selectedWeek += 1) {
  if (selectedWeek !== 32) {
    await originalCoordinator.runBusinessTool('update_booking_draft', {
      preferred_date: futureTuesday(selectedWeek),
    }, context(`date_${selectedWeek}`))
  }
  searched = await originalCoordinator.runBusinessTool(
    'search_availability',
    {},
    context(`search_${selectedWeek}`),
  )
  if (searched.output.availability.status === 'available') break
}
assert.equal(searched.output.availability.status, 'available')

const candidate = searched.output.availability.candidates[0]
const prepared = await originalCoordinator.runBusinessTool(
  'prepare_booking',
  { candidate_id: candidate.id },
  context('prepare'),
)
await originalCoordinator.markBookingPresented({
  preparationId: prepared.presentation.preparationId,
  eventId: prepared.presentation.eventId,
}, {
  ...context('original_summary_playback'),
  transcript: prepared.presentation.speech,
})

const restartedCoordinator = new MerchantCoordinator({ toolClient: client })
let replay
await assert.rejects(
  restartedCoordinator.runBusinessTool(
    'confirm_booking',
    { preparation_id: prepared.presentation.preparationId },
    context('recover_after_restart'),
  ),
  (error) => {
    assert.equal(error instanceof BookingSummaryPlaybackRequiredError, true)
    replay = error.presentation
    return true
  },
)
assert.equal(confirmPostRequests, 0)
assert.equal(replay.eventId, prepared.presentation.eventId)

await restartedCoordinator.markBookingPresented({
  preparationId: replay.preparationId,
  eventId: replay.eventId,
}, {
  ...context('replayed_summary'),
  transcript: replay.speech,
})
const submitted = await restartedCoordinator.runBusinessTool(
  'confirm_booking',
  { preparation_id: replay.preparationId },
  context('confirmation_after_replay'),
)
assert.equal(submitted.output.status, 'awaiting_payment')
assert.equal(confirmPostRequests, 1)

const secondRestart = new MerchantCoordinator({ toolClient: client })
const recoveredBooking = await secondRestart.runBusinessTool(
  'confirm_booking',
  { preparation_id: replay.preparationId },
  context('terminal_recovery'),
)
assert.equal(recoveredBooking.output.booking.id, submitted.output.booking.id)
assert.equal(confirmPostRequests, 1)

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
  (booking) => booking.operation_id === submitted.output.operation_id,
)
assert.equal(sameOperation.length, 1)

console.log(JSON.stringify({
  result: 'passed',
  backend: 'independent-test-database',
  recovered_preparation_status: 'presented',
  replay_receipt_written: true,
  confirmation_post_requests: confirmPostRequests,
  recovered_status: recoveredBooking.output.status,
  bookings_for_operation: sameOperation.length,
}, null, 2))
