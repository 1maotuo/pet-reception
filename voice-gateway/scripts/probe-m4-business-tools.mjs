import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'
import { MerchantCoordinator, MerchantToolClient } from '../src/merchant-tool.mjs'

const gatewayRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(gatewayRoot, '..')
dotenv.config({ path: resolve(projectRoot, '.env'), quiet: true })

function required(name) {
  const value = String(process.env[name] || '').trim()
  if (!value) throw new Error(`${name} is required in ${resolve(projectRoot, '.env')}`)
  return value
}

function futureTuesday(weeks = 9) {
  const value = new Date()
  value.setUTCDate(value.getUTCDate() + (weeks * 7))
  while (value.getUTCDay() !== 2) value.setUTCDate(value.getUTCDate() + 1)
  return value.toISOString().slice(0, 10)
}

const suffix = randomUUID().replaceAll('-', '').slice(0, 12)
const baseContext = {
  ownerId: `probe_owner_${suffix}`,
  sessionId: `probe_session_${suffix}`,
}
const context = (turn) => ({ ...baseContext, turnId: `turn_${turn}_${suffix}` })
const client = new MerchantToolClient({
  backendBaseUrl: process.env.BACKEND_BASE_URL
    || `http://127.0.0.1:${process.env.BACKEND_PORT || '8010'}`,
  merchantId: required('DEMO_MERCHANT_ID'),
  serviceToken: required('VOICE_GATEWAY_SERVICE_TOKEN'),
})
const coordinator = new MerchantCoordinator({ toolClient: client })

const merchant = await coordinator.runBusinessTool('get_merchant_info', {}, context('merchant'))
assert.equal(merchant.output.merchant.id, process.env.DEMO_MERCHANT_ID)

let updated = await coordinator.runBusinessTool('update_booking_draft', {
  customer_name: 'M4 HTTP 探针客户',
  contact_phone: '+86-138-4444-4444',
  pet_name: '探针豆包',
  pet_species: '犬',
  pet_breed: '贵宾犬',
  pet_age_years: 3,
  pet_care_notes: '耳朵敏感，请轻柔处理',
  need_description: '基础洗护并留意轻微打结',
  service_id: 'demo-bath',
  preferred_date: futureTuesday(12),
  preferred_time_of_day: 'morning',
  staff_preference_text: 'Lisa',
  staff_id: 'demo-staff-lisa',
}, context('draft-12'))
assert.deepEqual(updated.output.missing_for_availability, [])

let searched
let selectedWeek = 12
for (; selectedWeek <= 52; selectedWeek += 1) {
  if (selectedWeek !== 12) {
    updated = await coordinator.runBusinessTool('update_booking_draft', {
      preferred_date: futureTuesday(selectedWeek),
    }, context(`draft-${selectedWeek}`))
  }
  searched = await coordinator.runBusinessTool(
    'search_availability',
    {},
    context(`search-${selectedWeek}`),
  )
  if (searched.output.search.status === 'available') break
}
assert.ok(searched, 'availability search did not run')
assert.equal(searched.output.search.status, 'available')
assert.ok(searched.output.search.candidates.length > 0)
const candidate = searched.output.search.candidates[0]

const prepared = await coordinator.runBusinessTool('prepare_booking', {
  candidate_id: candidate.id,
}, context('prepare'))
assert.equal(prepared.output.status, 'prepared')
assert.equal(prepared.output.preparation.booking_created, false)

let prePresentationStatus = 0
try {
  await client.confirmBooking(
    context('premature-confirm'),
    prepared.presentation.preparationId,
    prepared.presentation.expectedDraftVersion,
    `probe_confirmation_${suffix}`,
  )
} catch (error) {
  prePresentationStatus = error.status
  assert.equal(error.code, 'confirmation_not_ready')
}
assert.equal(prePresentationStatus, 409)

const presented = await coordinator.markBookingPresented({
  preparationId: prepared.presentation.preparationId,
  eventId: prepared.presentation.eventId,
}, {
  ...context('prepare'),
  transcript: prepared.presentation.speech,
})
assert.equal(presented.status, 'presented')

const confirmed = await coordinator.runBusinessTool('confirm_booking', {
  preparation_id: prepared.presentation.preparationId,
}, context('confirm'))
assert.equal(confirmed.output.status, 'awaiting_payment')
assert.equal(confirmed.output.customer_action.payment_required, true)
assert.equal(JSON.stringify(confirmed.output).includes('access_token'), false)
assert.equal(JSON.stringify(confirmed.output).includes('credential_url'), false)
assert.match(confirmed.clientPresentation.content, /打开客户预约页面/)

const status = await coordinator.runBusinessTool('get_booking_status', {
  operation_id: confirmed.output.operation_id,
}, context('status'))
assert.equal(status.output.status, 'awaiting_payment')
assert.equal(status.output.booking.id, confirmed.output.booking.id)

console.log(JSON.stringify({
  result: 'passed',
  merchant: merchant.output.merchant.display_name,
  candidate: {
    selected_week_offset: selectedWeek,
    starts_at: candidate.starts_at,
    staff: candidate.staff_display_name,
  },
  preparation_status: prepared.output.status,
  pre_presentation_confirmation_http_status: prePresentationStatus,
  presentation_status: presented.status,
  confirmation_status: confirmed.output.status,
  status_query: status.output.status,
  credentials_exposed_to_model: false,
  customer_page_presented_to_bound_client: true,
}, null, 2))
