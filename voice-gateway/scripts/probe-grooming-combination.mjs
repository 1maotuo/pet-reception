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
  if (!value) throw new Error(`${name} is required in the local .env`)
  return value
}

function futureTuesday(weeks = 10) {
  const value = new Date()
  value.setUTCDate(value.getUTCDate() + (weeks * 7))
  while (value.getUTCDay() !== 2) value.setUTCDate(value.getUTCDate() + 1)
  return value.toISOString().slice(0, 10)
}

async function customerRequest(path, token, options = {}) {
  const response = await fetch(`${backendBaseUrl}${path}`, {
    ...options,
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  })
  const body = await response.json()
  assert.equal(response.ok, true, JSON.stringify(body))
  return body
}

const merchantId = required('DEMO_MERCHANT_ID')
assert.equal(
  merchantId,
  'demo-grooming-combo-001',
  'the live entry must be bound to the combination pricing demo merchant',
)
const backendBaseUrl = (
  process.env.BACKEND_BASE_URL
  || `http://127.0.0.1:${process.env.BACKEND_PORT || '8010'}`
).replace(/\/$/, '')
const suffix = randomUUID().replaceAll('-', '').slice(0, 12)
const baseContext = {
  ownerId: `combo_probe_owner_${suffix}`,
  sessionId: `combo_probe_session_${suffix}`,
}
const context = (turn) => ({ ...baseContext, turnId: `combo_${turn}_${suffix}` })
const client = new MerchantToolClient({
  backendBaseUrl,
  merchantId,
  serviceToken: required('VOICE_GATEWAY_SERVICE_TOKEN'),
})
const coordinator = new MerchantCoordinator({ toolClient: client })

const merchant = await coordinator.runBusinessTool(
  'get_merchant_info',
  {},
  context('merchant'),
)
assert.equal(merchant.output.merchant.id, merchantId)
assert.deepEqual(merchant.output.merchant.grooming_policy.supported_species, ['犬', '猫'])
assert.deepEqual(
  merchant.output.merchant.services.map((service) => service.id).sort(),
  [
    'combo-cat-groom',
    'combo-dog-bath',
    'combo-dog-haircut',
    'combo-standalone-nail-trim',
  ],
)
assert.ok(merchant.output.merchant.services.every((service) => service.combination_options))

async function quote(args, turn) {
  const result = await coordinator.runBusinessTool('quote_service', args, context(turn))
  assert.equal(result.output.effect.draft_mutated, false)
  assert.equal(result.output.status, 'ok')
  return result.output.quote
}

const lightBath = await quote({
  service_id: 'combo-dog-bath',
  pet_species: '犬',
  weight: { value: 15, unit: 'lb' },
}, 'quote-light-bath')
const mediumBath = await quote({
  service_id: 'combo-dog-bath',
  pet_species: '犬',
  weight: { value: 35, unit: 'lb' },
}, 'quote-medium-bath')
const haircut = await quote({
  service_id: 'combo-dog-haircut',
  pet_species: '犬',
  weight: { value: 35, unit: 'lb' },
  selected_addon_ids: ['nail-trim', 'nail-trim', 'teeth-brushing'],
}, 'quote-haircut')
const cat = await quote({
  service_id: 'combo-cat-groom',
  pet_species: '猫',
}, 'quote-cat')
const nailTrim = await quote({
  service_id: 'combo-standalone-nail-trim',
  pet_species: '犬',
}, 'quote-nail')

assert.equal(lightBath.total_min_cents, 5000)
assert.equal(mediumBath.total_min_cents, 6000)
assert.equal(haircut.total_min_cents, 11700)
assert.equal(haircut.scheduling_duration_minutes, 130)
assert.equal(haircut.deposit_amount_cents, 3000)
assert.deepEqual(haircut.duplicate_addon_ids, ['nail-trim'])
const includedNails = haircut.line_items.find((item) => item.item_id === 'nail-trim')
assert.equal(includedNails.amount_min_cents, 0)
assert.equal(includedNails.duration_minutes, 0)
assert.equal(cat.total_semantics, 'range')
assert.deepEqual([cat.total_min_cents, cat.total_max_cents], [7000, 9000])
assert.equal(nailTrim.total_min_cents, 1800)

if (process.env.COMBINATION_PROBE_QUOTE_ONLY === '1') {
  console.log(JSON.stringify({
    result: 'passed',
    mode: 'quote_only',
    merchant_id: merchantId,
    draft_mutated: false,
    quotes: {
      dog_bath_15lb_cents: lightBath.total_min_cents,
      dog_bath_35lb_cents: mediumBath.total_min_cents,
      dog_haircut_35lb_with_brushing_cents: haircut.total_min_cents,
      cat_groom_range_cents: [cat.total_min_cents, cat.total_max_cents],
      standalone_nail_trim_cents: nailTrim.total_min_cents,
    },
  }, null, 2))
  process.exit(0)
}

let updated = await coordinator.runBusinessTool('update_booking_draft', {
  customer_name: '组合演示探针客户',
  contact_phone: '+86-138-5555-6600',
  pet_name: '演示元宝',
  pet_species: '犬',
  pet_breed: '金毛',
  pet_age_text: '四岁',
  pet_weight: { value: 35, unit: 'lb', estimated: false },
  need_description: '犬类理发套餐并加刷牙；剪指甲为套餐已包含项目',
  service_id: 'combo-dog-haircut',
  selected_addon_ids: ['nail-trim', 'teeth-brushing'],
  preferred_date: futureTuesday(10),
  preferred_time_of_day: 'morning',
  staff_preference_text: 'Lisa',
  search_availability_after_update: true,
}, context('draft-10'))
assert.deepEqual(updated.output.missing_for_availability, [])

let search = updated.output.availability
let selectedWeek = 10
for (; selectedWeek <= 30 && search?.status !== 'available'; selectedWeek += 1) {
  updated = await coordinator.runBusinessTool('update_booking_draft', {
    preferred_date: futureTuesday(selectedWeek + 1),
    search_availability_after_update: true,
  }, context(`draft-${selectedWeek + 1}`))
  search = updated.output.availability
}
assert.equal(search.status, 'available')
assert.ok(search.candidates.length > 0)
const candidate = search.candidates[0]

const prepared = await coordinator.runBusinessTool(
  'prepare_booking',
  { candidate_id: candidate.id },
  context('prepare'),
)
assert.equal(prepared.output.status, 'prepared')
assert.equal(prepared.presentation.summary.service_quote.total_min_cents, 11700)
assert.equal(prepared.presentation.summary.service_quote.deposit_amount_cents, 3000)
assert.equal(prepared.presentation.summary.service_quote.scheduling_duration_minutes, 130)

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
assert.equal(confirmed.output.booking.deposit_amount_cents, 3000)
assert.equal(JSON.stringify(confirmed.output).includes('access_token'), false)

const urlMatch = confirmed.clientPresentation.content.match(/\((http[^)]+)\)/)
assert.ok(urlMatch, 'bound client presentation must include a customer page URL')
const credentialUrl = new URL(urlMatch[1])
const bookingReference = credentialUrl.pathname.split('/').at(-1)
const token = new URLSearchParams(credentialUrl.hash.slice(1)).get('token')
assert.ok(bookingReference)
assert.ok(token)

const beforePayment = await customerRequest(
  `/api/customer/bookings/${encodeURIComponent(bookingReference)}`,
  token,
)
assert.equal(beforePayment.booking.status, 'awaiting_payment')
assert.equal(beforePayment.booking.deposit_amount_cents, 3000)

const paid = await customerRequest(
  `/api/customer/bookings/${encodeURIComponent(bookingReference)}/simulate-payment`,
  token,
  {
    method: 'POST',
    headers: { 'X-Payment-Event-ID': `combo-probe-payment-${suffix}` },
  },
)
assert.equal(paid.booking.status, 'confirmed')
assert.equal(paid.booking.payment_amount_cents, 3000)
assert.equal(paid.booking.payment_currency, 'USD')

const finalStatus = await coordinator.runBusinessTool('get_booking_status', {
  operation_id: confirmed.output.operation_id,
}, context('status'))
assert.equal(finalStatus.output.status, 'confirmed')
assert.equal(finalStatus.output.booking.id, confirmed.output.booking.id)

console.log(JSON.stringify({
  result: 'passed',
  merchant: {
    id: merchant.output.merchant.id,
    display_name: merchant.output.merchant.display_name,
    services: merchant.output.merchant.services.map((service) => service.name_zh),
  },
  quotes: {
    dog_bath_15lb: { total_cents: lightBath.total_min_cents, duration_minutes: lightBath.scheduling_duration_minutes },
    dog_bath_35lb: { total_cents: mediumBath.total_min_cents, duration_minutes: mediumBath.scheduling_duration_minutes },
    dog_haircut_35lb_with_brushing: { total_cents: haircut.total_min_cents, duration_minutes: haircut.scheduling_duration_minutes, deposit_cents: haircut.deposit_amount_cents },
    cat_groom: { minimum_cents: cat.total_min_cents, maximum_cents: cat.total_max_cents, duration_minutes: cat.scheduling_duration_minutes },
    standalone_nail_trim: { total_cents: nailTrim.total_min_cents, duration_minutes: nailTrim.scheduling_duration_minutes },
  },
  included_nail_trim: { extra_cents: includedNails.amount_min_cents, extra_minutes: includedNails.duration_minutes },
  booking: {
    reference: bookingReference,
    starts_at: candidate.starts_at,
    ends_at: candidate.ends_at,
    staff: candidate.staff_display_name,
    status_before_payment: beforePayment.booking.status,
    status_after_payment: paid.booking.status,
    deposit_paid_cents: paid.booking.payment_amount_cents,
    confirmation_code_present: Boolean(paid.booking.confirmation_code),
  },
  credentials_exposed_to_model: false,
}, null, 2))
