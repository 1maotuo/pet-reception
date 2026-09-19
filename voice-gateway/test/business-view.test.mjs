import assert from 'node:assert/strict'
import test from 'node:test'
import {
  projectAvailabilityReceipt,
  projectDraftReceipt,
  projectOperationReceipt,
  projectRejectedReceipt,
} from '../src/business-view.mjs'
import {
  BusinessToolHttpError,
  MerchantCoordinator,
} from '../src/merchant-tool.mjs'

function draftResult(overrides = {}) {
  const draft = {
    id: 'draft-1',
    merchant_id: 'merchant-1',
    owner_id: 'owner-1',
    voice_session_id: 'session-1',
    version: 4,
    availability_revision: 3,
    customer_name: '王',
    contact_phone: '13800000000',
    pet_name: '元宝',
    pet_species: '犬',
    pet_breed: '金毛',
    pet_age_text: '四个月',
    pet_age_months: 4,
    service_id: 'service-full-grooming',
    preferred_date: '2026-09-21',
    preferred_time_of_day: 'afternoon',
    staff_preference_text: 'Lisa',
    staff_id: 'staff-lisa',
    selected_candidate_id: null,
    booking_id: null,
    ...overrides,
  }
  return {
    draft,
    changed_fields: ['preferred_date'],
    missing_for_availability: [],
    missing_for_booking: ['selected_availability'],
  }
}

function availabilityResult(overrides = {}) {
  return {
    search: {
      id: 'search-1',
      draft_id: 'draft-1',
      draft_version: 4,
      availability_revision: 3,
      service_id: 'service-full-grooming',
      staff_id: 'staff-lisa',
      preferred_date: '2026-09-21',
      preferred_time_of_day: 'afternoon',
      status: 'available',
      is_current: true,
      candidates: [{
        id: 'candidate-1300',
        service_id: 'service-full-grooming',
        staff_id: 'staff-lisa',
        staff_display_name: 'Lisa',
        starts_at: '2026-09-21T13:00:00-07:00',
        ends_at: '2026-09-21T15:00:00-07:00',
      }],
      alternative_dates: [],
      ...overrides,
    },
  }
}

test('draft receipt distinguishes a changed update from a successful same-value update', () => {
  const changed = projectDraftReceipt(draftResult())
  assert.equal(changed.status, 'ok')
  assert.equal(changed.effect.type, 'draft_updated')
  assert.equal(changed.effect.applied, true)
  assert.deepEqual(changed.effect.changed_fields, ['preferred_date'])
  assert.equal(changed.current_state.draft.version, 4)
  assert.equal(changed.current_state.draft.saved_fields.customer_name, '王')
  assert.equal(changed.current_state.draft.saved_fields.pet_age_text, '四个月')
  assert.equal(changed.current_state.draft.internal_references.draft_id, 'draft-1')
  assert.equal(changed.current_state.draft.internal_references.service_id, 'service-full-grooming')
  assert.equal(Object.hasOwn(changed.current_state, 'booking'), false)

  const unchanged = projectDraftReceipt({ ...draftResult(), changed_fields: [] })
  assert.equal(unchanged.status, 'ok')
  assert.equal(unchanged.effect.type, 'draft_already_current')
  assert.equal(unchanged.effect.applied, true)
  assert.deepEqual(unchanged.effect.changed_fields, [])
})

test('missing date receipt keeps the unfinished availability goal', () => {
  const input = draftResult({ preferred_date: null })
  input.missing_for_availability = ['preferred_date']
  const receipt = projectDraftReceipt(input, { searchRequested: true })

  assert.equal(receipt.status, 'ok')
  assert.equal(receipt.next_step.type, 'need_input')
  assert.equal(receipt.next_step.for_goal, 'search_availability')
  assert.deepEqual(receipt.next_step.missing, ['preferred_date'])
})

test('standalone and chained availability use the same authoritative current state', () => {
  const current = draftResult()
  const availability = availabilityResult()
  const standalone = projectAvailabilityReceipt(current, availability)
  const chained = projectDraftReceipt(current, {
    searchRequested: true,
    availabilityResult: availability,
  })

  assert.deepEqual(chained.current_state, standalone.current_state)
  assert.deepEqual(chained.availability, standalone.availability)
  assert.equal(standalone.current_state.availability.belongs_to_current_draft, true)
  assert.equal(standalone.next_step.type, 'present_availability')
  assert.equal(standalone.current_state.draft.missing_for_booking.includes('customer_name'), false)
})

test('a saved update followed by a failed search remains an applied partial success', () => {
  const receipt = projectDraftReceipt(draftResult(), {
    searchRequested: true,
    availabilityError: new Error('socket exploded with database internals'),
  })

  assert.equal(receipt.status, 'partial_success')
  assert.equal(receipt.effect.applied, true)
  assert.equal(receipt.effect.availability_checked, false)
  assert.equal(receipt.next_step.type, 'retry_availability')
  assert.equal(receipt.next_step.for_goal, 'search_availability')
  assert.equal(JSON.stringify(receipt).includes('socket exploded'), false)
})

test('a stale availability result is not exposed as current candidates', () => {
  const receipt = projectAvailabilityReceipt(
    draftResult({ preferred_date: '2026-09-22', availability_revision: 4 }),
    availabilityResult(),
  )

  assert.equal(receipt.status, 'stale')
  assert.equal(receipt.current_state.availability.belongs_to_current_draft, false)
  assert.deepEqual(receipt.availability.candidates, [])
  assert.equal(receipt.next_step.type, 'refresh_availability')
})

test('validation rejection is business-safe and proves no fields were applied', () => {
  const error = {
    status: 422,
    code: 'http_422',
    detail: [{ loc: ['body', 'contact_phone'], msg: 'String too short', type: 'string_too_short' }],
  }
  const receipt = projectRejectedReceipt('update_booking_draft', error, draftResult())

  assert.equal(receipt.status, 'update_rejected')
  assert.equal(receipt.effect.applied, false)
  assert.deepEqual(receipt.invalid_fields, ['联系电话'])
  assert.equal(receipt.next_step.type, 'clarify_field')
  const serialized = JSON.stringify({
    invalid_fields: receipt.invalid_fields,
    reason: receipt.reason,
    next_step: receipt.next_step,
  })
  assert.equal(serialized.includes('string_too_short'), false)
  assert.equal(serialized.includes('422'), false)
})

test('operation receipt only states preparation or booking state actually read', () => {
  const prepared = projectOperationReceipt({
    operation_id: 'operation-1',
    status: 'presented',
    preparation: { id: 'preparation-1', status: 'presented', draft_version: 4 },
    booking: null,
    customer_credential: null,
  })
  assert.equal(prepared.current_state.preparation.status, 'presented')
  assert.equal(Object.hasOwn(prepared.current_state, 'booking'), false)

  const paid = projectOperationReceipt({
    operation_id: 'operation-1',
    status: 'confirmed',
    preparation: { id: 'preparation-1', status: 'confirmed', draft_version: 4 },
    booking: { id: 'booking-1', status: 'confirmed', confirmation_code: 'PR-123' },
    customer_credential: {
      booking_reference: 'BK-123',
      access_token: 'do-not-leak',
      credential_url: 'http://example.test/#token=do-not-leak',
    },
  })
  assert.equal(paid.status, 'confirmed')
  assert.equal(paid.current_state.booking.status, 'confirmed')
  assert.equal(JSON.stringify(paid).includes('do-not-leak'), false)
})

test('a supplied date completes the retained availability goal in the same tool receipt', async () => {
  let state = draftResult({ preferred_date: null }).draft
  let searchCalls = 0
  const resultFor = (changed_fields = []) => ({
    draft: { ...state },
    changed_fields,
    missing_for_availability: state.preferred_date ? [] : ['preferred_date'],
    missing_for_booking: ['selected_availability'],
  })
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => resultFor(),
      updateBookingDraft: async (_context, patch) => {
        const changed = Object.keys(patch).filter((key) => (
          key !== 'expected_version' && state[key] !== patch[key]
        ))
        state = {
          ...state,
          ...Object.fromEntries(Object.entries(patch).filter(([key]) => key !== 'expected_version')),
          version: state.version + 1,
          availability_revision: state.availability_revision + (changed.some((field) => (
            ['service_id', 'preferred_date', 'preferred_time_of_day', 'staff_id'].includes(field)
          )) ? 1 : 0),
        }
        return resultFor(changed)
      },
      searchAvailability: async () => {
        searchCalls += 1
        return availabilityResult({
          draft_version: state.version,
          availability_revision: state.availability_revision,
          preferred_date: state.preferred_date,
          preferred_time_of_day: state.preferred_time_of_day,
        })
      },
    },
  })
  const context = { ownerId: 'owner-1', sessionId: 'session-1', turnId: 'turn-1' }

  const waiting = await coordinator.runBusinessTool(
    'update_booking_draft',
    { service_id: 'service-full-grooming', search_availability_after_update: true },
    context,
  )
  assert.equal(waiting.output.next_step.type, 'need_input')
  assert.equal(waiting.output.next_step.for_goal, 'search_availability')
  assert.equal(searchCalls, 0)

  const completed = await coordinator.runBusinessTool(
    'update_booking_draft',
    {
      preferred_date: '2026-09-21',
      preferred_time_of_day: 'afternoon',
      search_availability_after_update: true,
    },
    { ...context, turnId: 'turn-2' },
  )
  assert.equal(searchCalls, 1)
  assert.equal(completed.output.next_step.type, 'present_availability')
  assert.equal(completed.output.current_state.draft.saved_fields.customer_name, '王')
  assert.equal(completed.output.current_state.draft.saved_fields.pet_name, '元宝')
  assert.equal(completed.output.current_state.availability.belongs_to_current_draft, true)
})

test('coordinator returns a safe rejected receipt instead of a Pydantic payload', async () => {
  const current = draftResult()
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => current,
      updateBookingDraft: async () => {
        throw new BusinessToolHttpError('booking draft update', 422, [{
          loc: ['body', 'pet_age_months'],
          msg: 'Input should be greater than zero',
          type: 'greater_than',
        }])
      },
    },
  })
  const response = await coordinator.runBusinessTool(
    'update_booking_draft',
    { pet_age_months: -1 },
    { ownerId: 'owner-1', sessionId: 'session-1', turnId: 'turn-rejected' },
  )

  assert.equal(response.output.status, 'update_rejected')
  assert.equal(response.output.effect.applied, false)
  assert.deepEqual(response.output.invalid_fields, ['宠物年龄'])
  assert.equal(JSON.stringify(response.output).includes('greater_than'), false)
})

test('coordinator reports a successful same-value update without claiming failure', async () => {
  const current = draftResult()
  const response = await new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => current,
      updateBookingDraft: async () => ({ ...current, changed_fields: [] }),
    },
  }).runBusinessTool(
    'update_booking_draft',
    { customer_name: '王' },
    { ownerId: 'owner-1', sessionId: 'session-1', turnId: 'turn-same-value' },
  )

  assert.equal(response.output.status, 'ok')
  assert.equal(response.output.effect.type, 'draft_already_current')
  assert.equal(response.output.effect.applied, true)
  assert.equal(response.output.current_state.draft.saved_fields.customer_name, '王')
})

test('a 1 PM Lisa candidate remains usable after collecting unrelated identity fields', async () => {
  let state = draftResult({
    customer_name: null,
    contact_phone: null,
    selected_candidate_id: null,
  }).draft
  let prepareCalls = 0
  const currentResult = (changed_fields = []) => ({
    draft: { ...state },
    changed_fields,
    missing_for_availability: [],
    missing_for_booking: [
      ...(state.customer_name ? [] : ['customer_name']),
      ...(state.contact_phone ? [] : ['contact_phone']),
      'selected_availability',
    ],
  })
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => currentResult(),
      searchAvailability: async () => availabilityResult(),
      updateBookingDraft: async (_context, patch) => {
        state = {
          ...state,
          customer_name: patch.customer_name,
          contact_phone: patch.contact_phone,
          version: state.version + 1,
        }
        return currentResult(['customer_name', 'contact_phone'])
      },
      prepareBooking: async (_context, body) => {
        prepareCalls += 1
        assert.equal(body.candidate_id, 'candidate-1300')
        return {
          operation_id: 'operation-1',
          status: 'prepared',
          preparation: {
            id: 'preparation-1',
            draft_version: state.version,
            summary_digest: 'digest-1',
            status: 'prepared',
            summary: {
              merchant_display_name: 'Paws & Bubbles',
              service_name_zh: '全套美容',
              staff_display_name: 'Lisa',
              starts_at: '2026-09-21T13:00:00-07:00',
              ends_at: '2026-09-21T15:00:00-07:00',
              merchant_time_zone: 'America/Los_Angeles',
              customer_name: '王',
              contact_phone: '13800000000',
              pet_name: '元宝',
              pet_species: '犬',
            },
          },
          booking: null,
          customer_credential: null,
        }
      },
    },
  })
  const context = { ownerId: 'owner-1', sessionId: 'session-1', turnId: 'turn-search' }

  const searched = await coordinator.runBusinessTool('search_availability', {}, context)
  assert.equal(searched.output.availability.candidates[0].starts_at.includes('13:00'), true)
  assert.equal(searched.output.current_state.draft.saved_fields.staff_preference_text, 'Lisa')

  const identified = await coordinator.runBusinessTool(
    'update_booking_draft',
    { customer_name: '王', contact_phone: '13800000000' },
    { ...context, turnId: 'turn-identity' },
  )
  assert.equal(identified.output.current_state.draft.saved_fields.preferred_date, '2026-09-21')
  assert.equal(identified.output.current_state.draft.saved_fields.staff_preference_text, 'Lisa')
  assert.equal(identified.output.current_state.draft.saved_fields.customer_name, '王')

  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: 'candidate-1300' },
    { ...context, turnId: 'turn-prepare' },
  )
  assert.equal(prepared.output.status, 'prepared')
  assert.equal(prepareCalls, 1)
})
