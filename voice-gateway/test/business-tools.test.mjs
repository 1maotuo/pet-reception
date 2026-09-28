import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import {
  BookingSummaryPlaybackRequiredError,
  MerchantCoordinator,
  presentationTranscriptMatches,
} from '../src/merchant-tool.mjs'

const context = {
  ownerId: 'user_test-owner',
  sessionId: 'session-a',
  turnId: 'turn-a',
}

function draft(overrides = {}) {
  return {
    id: 'draft-id',
    merchant_id: 'demo-grooming-001',
    owner_id: context.ownerId,
    voice_session_id: context.sessionId,
    version: 2,
    availability_revision: 1,
    customer_name: '王女士',
    contact_phone: '+86-138-0000-0000',
    pet_name: '豆包',
    pet_species: '犬',
    service_id: 'demo-bath',
    preferred_date: '2026-10-06',
    selected_candidate_id: null,
    ...overrides,
  }
}

function availability(overrides = {}) {
  return {
    context: {
      merchant_id: 'demo-grooming-001',
      owner_id: context.ownerId,
      voice_session_id: context.sessionId,
      turn_id: context.turnId,
    },
    search: {
      id: 'search-id',
      draft_id: 'draft-id',
      draft_version: 2,
      availability_revision: 1,
      schedule_revision: 1,
      service_id: 'demo-bath',
      staff_id: null,
      preferred_date: '2026-10-06',
      preferred_time_of_day: null,
      status: 'available',
      is_current: true,
      candidates: [],
      alternative_dates: [],
      created_at: '2026-09-19T18:00:00Z',
      ...overrides,
    },
  }
}

function summary() {
  return {
    merchant_id: 'demo-grooming-001',
    merchant_display_name: 'Paws & Bubbles 宠物美容（演示）',
    service_id: 'demo-bath',
    service_name_zh: '基础洗护',
    staff_id: 'demo-staff-lisa',
    staff_display_name: 'Lisa',
    starts_at: '2026-10-06T09:00:00-07:00',
    ends_at: '2026-10-06T10:00:00-07:00',
    merchant_time_zone: 'America/Los_Angeles',
    customer_name: '王女士',
    contact_phone: '+86-138-0000-0000',
    pet_name: '豆包',
    pet_species: '犬',
    pet_breed: '贵宾犬',
    pet_age_years: 3,
    pet_care_notes: '耳朵敏感',
    need_description: '轻微打结',
    source: 'server_booking_snapshot',
  }
}

function preparationResult() {
  return {
    operation_id: '11111111-1111-4111-8111-111111111111',
    status: 'prepared',
    preparation: {
      id: '22222222-2222-4222-8222-222222222222',
      operation_id: '11111111-1111-4111-8111-111111111111',
      draft_version: 2,
      summary: summary(),
      summary_digest: 'trusted-digest',
      status: 'prepared',
      availability_held: true,
      booking_created: false,
    },
    booking: null,
    customer_credential: null,
  }
}

function completeSummaryTranscript() {
  return [
    '请您确认预约摘要。',
    '王女士，为犬豆包预约基础洗护，',
    '2026年10月6日星期二09:00，由Lisa服务。',
    '联系电话是+86-138-0000-0000。',
    '备注：耳朵敏感；轻微打结。',
    '如果以上信息正确，请在我说完后明确确认；现在还没有创建正式预约。',
  ].join('')
}

test('draft update is batched, versioned by the gateway, scoped, and de-duplicated per turn', async () => {
  const calls = []
  const toolClient = {
    getBookingDraft: async (received) => {
      calls.push(['get', received])
      return { draft: draft() }
    },
    updateBookingDraft: async (received, patch) => {
      calls.push(['patch', received, patch])
      return {
        draft: draft({ version: 3, pet_name: '团子', pet_breed: '比熊' }),
        changed_fields: ['pet_name', 'pet_breed'],
        missing_for_availability: [],
        missing_for_booking: ['selected_availability'],
      }
    },
  }
  const coordinator = new MerchantCoordinator({ toolClient })
  const args = {
    pet_name: '团子',
    pet_breed: '比熊',
    merchant_id: 'attacker-merchant',
    owner_id: 'attacker-owner',
  }

  const invocationContext = { ...context, invocationId: 'call-draft-update' }
  const first = await coordinator.runBusinessTool('update_booking_draft', args, invocationContext)
  const second = await coordinator.runBusinessTool('update_booking_draft', args, invocationContext)

  assert.equal(calls.length, 2)
  assert.deepEqual(calls[1][2], {
    expected_version: 2,
    pet_name: '团子',
    pet_breed: '比熊',
  })
  assert.equal(calls[1][1].ownerId, context.ownerId)
  assert.equal(Object.hasOwn(first.output.draft, 'owner_id'), false)
  assert.equal(Object.hasOwn(first.output.draft, 'voice_session_id'), false)
  assert.strictEqual(first, second)
})

test('controlled summary requires a matching full playback transcript before presentation write', async () => {
  const writes = []
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft() }),
    prepareBooking: async () => preparationResult(),
    getBookingPreparation: async () => {
      throw new Error('preparation is not trusted in this gateway session')
    },
    markBookingPresented: async (...args) => {
      writes.push(args)
      return { ...preparationResult(), status: 'presented' }
    },
  }
  const coordinator = new MerchantCoordinator({ toolClient })
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: '33333333-3333-4333-8333-333333333333' },
    context,
  )

  assert.match(prepared.presentation.speech, /王女士/)
  assert.match(prepared.presentation.speech, /豆包/)
  assert.match(prepared.presentation.speech, /基础洗护/)
  assert.match(prepared.presentation.speech, /现在还没有创建正式预约/)
  assert.equal(
    presentationTranscriptMatches(summary(), '王女士为豆包预约基础洗护'),
    false,
  )

  const rejected = await coordinator.markBookingPresented(
    {
      preparationId: prepared.presentation.preparationId,
      eventId: prepared.presentation.eventId,
    },
    { ...context, transcript: '王女士为豆包预约基础洗护' },
  )
  assert.deepEqual(rejected, {
    status: 'rejected',
    reason: 'summary_transcript_incomplete',
  })
  assert.equal(writes.length, 0)

  const crossSession = await coordinator.markBookingPresented(
    {
      preparationId: prepared.presentation.preparationId,
      eventId: prepared.presentation.eventId,
    },
    {
      ...context,
      sessionId: 'session-attacker',
      transcript: prepared.presentation.speech,
    },
  )
  assert.deepEqual(crossSession, {
    status: 'rejected',
    reason: 'untrusted_presentation',
  })
  assert.equal(writes.length, 0)

  await assert.rejects(
    coordinator.runBusinessTool(
      'confirm_booking',
      { preparation_id: prepared.presentation.preparationId },
      { ...context, sessionId: 'session-attacker', turnId: 'turn-attacker' },
    ),
    /not trusted in this gateway session/,
  )

  const omittedDateTimePhone = '请确认预约：王女士为犬豆包预约基础洗护，由 Lisa 服务，备注耳朵敏感、轻微打结，联系电话已核对。'
  assert.equal(presentationTranscriptMatches(summary(), omittedDateTimePhone), false)
  const wrongFacts = '请确认预约：王女士为犬豆包预约基础洗护，2027年12月31日22:00，由 Lisa 服务，联系电话999999，备注耳朵敏感、轻微打结。'
  assert.equal(presentationTranscriptMatches(summary(), wrongFacts), false)
  const spokenPhone = summary().contact_phone.replace(/\D/g, '').replace(/\d/g, (digit) => '零一二三四五六七八九'[Number(digit)])
  const chineseDigitEquivalent = completeSummaryTranscript().replace(summary().contact_phone, spokenPhone)
  assert.equal(presentationTranscriptMatches(summary(), chineseDigitEquivalent), true)

  const transcript = completeSummaryTranscript()
  const marked = await coordinator.markBookingPresented(
    {
      preparationId: prepared.presentation.preparationId,
      eventId: prepared.presentation.eventId,
    },
    { ...context, transcript },
  )
  assert.equal(marked.status, 'presented')
  assert.equal(writes.length, 1)
  assert.equal(writes[0][0].ownerId, context.ownerId)
  assert.equal(writes[0][1], prepared.presentation.preparationId)
  assert.equal(writes[0][3], prepared.presentation.eventId)
})

test('controlled summary compares exact local time and exact phone instead of substrings', () => {
  assert.equal(
    presentationTranscriptMatches(summary(), completeSummaryTranscript().replace('09:00', '9:30')),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(summary(), completeSummaryTranscript().replace('09:00', '19:00')),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(summary(), completeSummaryTranscript().replace('09:00', '晚上九点')),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(
      { ...summary(), starts_at: '2026-10-06T13:00:00-07:00' },
      completeSummaryTranscript().replace('09:00', '下午一点'),
    ),
    true,
  )
  assert.equal(
    presentationTranscriptMatches(
      { ...summary(), starts_at: '2026-10-06T13:30:00-07:00' },
      completeSummaryTranscript().replace('09:00', '下午1:30'),
    ),
    true,
  )
  assert.equal(
    presentationTranscriptMatches(
      { ...summary(), starts_at: '2026-10-06T13:30:00-07:00' },
      completeSummaryTranscript().replace('09:00', '下午一点半'),
    ),
    true,
  )
  assert.equal(
    presentationTranscriptMatches(
      summary(),
      completeSummaryTranscript().replace('09:00', '上午九点'),
    ),
    true,
  )
  assert.equal(
    presentationTranscriptMatches(
      summary(),
      completeSummaryTranscript().replace('09:00', '晚上9:00'),
    ),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(
      summary(),
      completeSummaryTranscript().replace('09:00', '不是九点，是九点半'),
    ),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(
      { ...summary(), starts_at: '2026-10-06T10:00:00-07:00' },
      completeSummaryTranscript().replace('09:00', '上午九点到十点'),
    ),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(
      { ...summary(), starts_at: '2026-10-06T15:00:00-07:00' },
      completeSummaryTranscript().replace('09:00', '下午3点至5点'),
    ),
    true,
  )
  assert.equal(
    presentationTranscriptMatches(
      { ...summary(), starts_at: '2026-10-06T17:00:00-07:00' },
      completeSummaryTranscript().replace('09:00', '下午3点至5点'),
    ),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(
      summary(),
      completeSummaryTranscript().replace('09:00', '结束时间上午九点'),
    ),
    false,
  )
  assert.equal(
    presentationTranscriptMatches(
      summary(),
      completeSummaryTranscript().replace('+86-138-0000-0000', '+86-138-0000-00001'),
    ),
    false,
  )
})

test('confirmation waits for presentation persistence and hides customer credentials from the model', async () => {
  let releasePresentation
  const presentationPending = new Promise((resolve) => { releasePresentation = resolve })
  let confirmCalls = 0
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft() }),
    prepareBooking: async () => preparationResult(),
    markBookingPresented: async () => {
      await presentationPending
      return { ...preparationResult(), status: 'presented' }
    },
    confirmBooking: async (_received, preparationId, draftVersion, eventId) => {
      confirmCalls += 1
      assert.equal(preparationId, preparationResult().preparation.id)
      assert.equal(draftVersion, 2)
      assert.match(eventId, /^confirmation_[0-9a-f-]+$/)
      return {
        ...preparationResult(),
        status: 'awaiting_payment',
        booking: { customer_reference: 'BK-DEMO', status: 'awaiting_payment' },
        customer_credential: {
          booking_reference: 'BK-DEMO',
          access_token: 'must-not-reach-model',
          credential_url: 'http://example.test/secret-token',
        },
      }
    },
  }
  const coordinator = new MerchantCoordinator({ toolClient })
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: '33333333-3333-4333-8333-333333333333' },
    context,
  )
  const transcript = completeSummaryTranscript()
  const marking = coordinator.markBookingPresented(
    {
      preparationId: prepared.presentation.preparationId,
      eventId: prepared.presentation.eventId,
    },
    { ...context, transcript },
  )
  const confirming = coordinator.runBusinessTool(
    'confirm_booking',
    {
      preparation_id: prepared.presentation.preparationId,
      confirmation_event_id: 'model-forged-event',
    },
    { ...context, turnId: 'turn-b' },
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(confirmCalls, 0)

  releasePresentation()
  await marking
  const confirmed = await confirming
  assert.equal(confirmCalls, 1)
  assert.equal(confirmed.output.status, 'awaiting_payment')
  assert.equal(confirmed.output.customer_action.booking_reference, 'BK-DEMO')
  assert.equal(JSON.stringify(confirmed.output).includes('must-not-reach-model'), false)
  assert.equal(JSON.stringify(confirmed.output).includes('secret-token'), false)
  assert.match(confirmed.clientPresentation.content, /打开客户预约页面/)
  assert.match(confirmed.clientPresentation.content, /secret-token/)
})

test('same-turn read after a draft change re-queries current availability', async () => {
  let state = { version: 2, availability_revision: 1, preferred_date: '2026-10-06' }
  let searchCalls = 0
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft(state) }),
    updateBookingDraft: async (_received, patch) => {
      assert.equal(patch.expected_version, state.version)
      state = {
        ...state,
        version: state.version + 1,
        availability_revision: state.availability_revision + 1,
        preferred_date: patch.preferred_date,
      }
      return {
        draft: draft(state),
        changed_fields: ['preferred_date'],
        missing_for_availability: [],
        missing_for_booking: ['selected_availability'],
      }
    },
    searchAvailability: async () => {
      searchCalls += 1
      return availability({
        draft_version: state.version,
        availability_revision: state.availability_revision,
        preferred_date: state.preferred_date,
      })
    },
  }
  const coordinator = new MerchantCoordinator({ toolClient })

  const first = await coordinator.runBusinessTool('search_availability', {}, context)
  await coordinator.runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-10-07' },
    context,
  )
  const second = await coordinator.runBusinessTool('search_availability', {}, context)

  assert.equal(first.output.availability.preferred_date, '2026-10-06')
  assert.equal(second.output.availability.preferred_date, '2026-10-07')
  assert.equal(searchCalls, 2)
})

test('asking an old date again performs a fresh current availability search', async () => {
  let state = draft({ preferred_date: '2026-09-21' })
  let searchCalls = 0
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => ({
        draft: { ...state },
        missing_for_availability: [],
        missing_for_booking: ['selected_availability'],
      }),
      updateBookingDraft: async (_received, patch) => {
        state = {
          ...state,
          ...Object.fromEntries(Object.entries(patch).filter(([key]) => key !== 'expected_version')),
          version: state.version + 1,
          availability_revision: state.availability_revision + 1,
        }
        return {
          draft: { ...state },
          changed_fields: ['preferred_date'],
          missing_for_availability: [],
          missing_for_booking: ['selected_availability'],
        }
      },
      searchAvailability: async () => {
        searchCalls += 1
        return availability({
          draft_version: state.version,
          availability_revision: state.availability_revision,
          preferred_date: state.preferred_date,
        })
      },
    },
  })

  await coordinator.runBusinessTool('search_availability', {}, context)
  await coordinator.runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-09-26', search_availability_after_update: true },
    { ...context, turnId: 'turn-date-26', turnGeneration: 1 },
  )
  const backTo21 = await coordinator.runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-09-21', search_availability_after_update: true },
    { ...context, turnId: 'turn-date-21-again', turnGeneration: 2 },
  )

  assert.equal(searchCalls, 3)
  assert.equal(backTo21.output.current_state.availability.belongs_to_current_draft, true)
  assert.equal(backTo21.output.availability.preferred_date, '2026-09-21')
})

test('rapid partial date corrections inherit the latest explicit time preference', async () => {
  let activeGeneration = 1
  let state = draft({ preferred_date: null, preferred_time_of_day: null })
  let releaseOldSearch
  let markOldSearchStarted
  const oldSearchStarted = new Promise((resolve) => { markOldSearchStarted = resolve })
  const oldSearchGate = new Promise((resolve) => { releaseOldSearch = resolve })
  const patches = []
  const searches = []
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => ({
        draft: { ...state },
        missing_for_availability: state.preferred_date ? [] : ['preferred_date'],
        missing_for_booking: ['customer_name', 'contact_phone', 'pet_name', 'selected_availability'],
      }),
      updateBookingDraft: async (_received, patch) => {
        patches.push({ ...patch })
        const changed = ['preferred_date', 'preferred_time_of_day'].filter(
          (field) => Object.hasOwn(patch, field) && state[field] !== patch[field],
        )
        state = {
          ...state,
          ...Object.fromEntries(Object.entries(patch).filter(([key]) => key !== 'expected_version')),
          version: state.version + 1,
          availability_revision: state.availability_revision + 1,
        }
        return {
          draft: { ...state },
          changed_fields: changed,
          missing_for_availability: [],
          missing_for_booking: ['customer_name', 'contact_phone', 'pet_name', 'selected_availability'],
        }
      },
      searchAvailability: async () => {
        const snapshot = { ...state }
        searches.push({ date: snapshot.preferred_date, time: snapshot.preferred_time_of_day })
        if (snapshot.preferred_date === '2026-09-25') {
          markOldSearchStarted()
          await oldSearchGate
        }
        return availability({
          draft_version: snapshot.version,
          availability_revision: snapshot.availability_revision,
          preferred_date: snapshot.preferred_date,
          preferred_time_of_day: snapshot.preferred_time_of_day,
        })
      },
    },
  })
  const turn = (generation) => ({
    ...context,
    turnId: `turn-intent-${generation}`,
    turnGeneration: generation,
    isActive: () => activeGeneration === generation,
  })

  const first = coordinator.runBusinessTool(
    'update_booking_draft',
    {
      preferred_date: '2026-09-25',
      preferred_time_of_day: 'afternoon',
      search_availability_after_update: true,
    },
    turn(1),
  )
  await oldSearchStarted
  activeGeneration = 2
  const second = coordinator.runBusinessTool(
    'update_booking_draft',
    {
      preferred_date: '2026-09-26',
      preferred_time_of_day: 'morning',
      search_availability_after_update: true,
    },
    turn(2),
  )
  activeGeneration = 3
  const third = coordinator.runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-09-27', search_availability_after_update: true },
    turn(3),
  )
  releaseOldSearch()

  await first
  await assert.rejects(second, (error) => error.code === 'stale_business_turn')
  const final = await third

  assert.equal(patches.at(-1).preferred_date, '2026-09-27')
  assert.equal(patches.at(-1).preferred_time_of_day, 'morning')
  assert.deepEqual(searches.at(-1), { date: '2026-09-27', time: 'morning' })
  assert.equal(final.output.availability.preferred_date, '2026-09-27')
  assert.equal(final.output.availability.preferred_time_of_day, 'morning')
  const latest = coordinator.currentAvailability.get(`${context.ownerId}\u0000${context.sessionId}`)
  assert.equal(latest.preferred_date, '2026-09-27')
})

test('draft update can continue directly to availability without a user nudge', async () => {
  const calls = []
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft() }),
    updateBookingDraft: async (_received, patch) => {
      calls.push(['update', patch])
      return {
        draft: draft({
          version: 3,
          availability_revision: 2,
          preferred_date: '2026-10-07',
        }),
        changed_fields: ['preferred_date'],
        missing_for_availability: [],
        missing_for_booking: ['selected_availability'],
      }
    },
    searchAvailability: async (_received, body) => {
      calls.push(['search', body])
      return availability({
        draft_version: 3,
        availability_revision: 2,
        preferred_date: '2026-10-07',
      })
    },
  }
  const result = await new MerchantCoordinator({ toolClient }).runBusinessTool(
    'update_booking_draft',
    {
      preferred_date: '2026-10-07',
      search_availability_after_update: true,
    },
    context,
  )

  assert.deepEqual(calls, [
    ['update', { expected_version: 2, preferred_date: '2026-10-07' }],
    ['search', { expected_availability_revision: 2 }],
  ])
  assert.equal(result.output.availability.preferred_date, '2026-10-07')
})

test('a saved draft remains truthful when the chained availability read fails', async () => {
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft() }),
    updateBookingDraft: async () => ({
      draft: draft({ version: 3, preferred_date: '2026-10-07' }),
      changed_fields: ['preferred_date'],
      missing_for_availability: [],
      missing_for_booking: ['selected_availability'],
    }),
    searchAvailability: async () => {
      const error = new Error('backend unavailable')
      error.code = 'http_503'
      error.status = 503
      throw error
    },
  }

  const result = await new MerchantCoordinator({ toolClient }).runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-10-07', search_availability_after_update: true },
    context,
  )

  assert.deepEqual(result.output.changed_fields, ['preferred_date'])
  assert.equal(result.output.draft.preferred_date, '2026-10-07')
  assert.equal(result.output.status, 'partial_success')
  assert.equal(result.output.effect.applied, true)
  assert.equal(result.output.effect.availability_checked, false)
  assert.equal(result.output.next_step.action, 'retry_availability')
  assert.equal(JSON.stringify(result.output).includes('http_503'), false)
})

test('alternative-date results require a concrete re-query before prepare', async () => {
  let prepareCalls = 0
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => ({ draft: draft() }),
      searchAvailability: async () => availability({
        status: 'no_availability',
        preferred_date: '2026-10-06',
        candidates: [],
        alternative_dates: [{ date: '2026-10-07' }],
      }),
      prepareBooking: async () => {
        prepareCalls += 1
        return preparationResult()
      },
    },
  })

  const searched = await coordinator.runBusinessTool('search_availability', {}, context)
  assert.equal(searched.output.next_step.action, 'present_alternative_dates')
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: 'invented-candidate' },
    context,
  )

  assert.equal(prepared.output.status, 'needs_availability_selection')
  assert.equal(prepared.output.error_code, 'specific_availability_not_queried')
  assert.equal(prepareCalls, 0)
})

test('prepare accepts only a candidate from the current concrete search', async () => {
  let prepareCalls = 0
  const candidateId = '33333333-3333-4333-8333-333333333333'
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => ({ draft: draft() }),
      searchAvailability: async () => availability({
        candidates: [{ id: candidateId }],
      }),
      prepareBooking: async () => {
        prepareCalls += 1
        return preparationResult()
      },
    },
  })

  await coordinator.runBusinessTool('search_availability', {}, context)
  const rejected = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: '44444444-4444-4444-8444-444444444444' },
    context,
  )
  assert.equal(rejected.output.error_code, 'candidate_not_in_current_results')
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: candidateId },
    { ...context, invocationId: 'prepare-current-candidate' },
  )
  assert.equal(prepared.output.status, 'prepared')
  assert.equal(prepareCalls, 1)
})

test('changing the date without re-querying blocks preparation at the gateway', async () => {
  let prepareCalls = 0
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => ({ draft: draft() }),
      updateBookingDraft: async () => ({
        draft: draft({ version: 3, preferred_date: '2026-10-07' }),
        changed_fields: ['preferred_date'],
        missing_for_availability: [],
        missing_for_booking: ['selected_availability'],
      }),
      prepareBooking: async () => {
        prepareCalls += 1
        return preparationResult()
      },
    },
  })

  await coordinator.runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-10-07' },
    context,
  )
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: 'invented-after-date-change' },
    context,
  )

  assert.equal(prepared.output.error_code, 'specific_availability_not_queried')
  assert.equal(prepareCalls, 0)
})

test('a confirmed draft failure blocks repeated prepare until a new session is used', async () => {
  let draftReads = 0
  const terminal = new Error('already submitted')
  terminal.code = 'booking_already_submitted'
  terminal.detail = {
    code: 'booking_already_submitted',
    status: 'confirmed',
    operation_id: '11111111-1111-4111-8111-111111111111',
    recovery: { action: 'use_start_new_booking_control' },
  }
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getBookingDraft: async () => {
        draftReads += 1
        throw terminal
      },
    },
  })

  await assert.rejects(
    coordinator.runBusinessTool('prepare_booking', { candidate_id: 'candidate' }, context),
    { code: 'booking_already_submitted' },
  )
  const repeated = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: 'candidate' },
    { ...context, turnId: 'turn-b' },
  )
  assert.equal(repeated.output.status, 'already_submitted')
  assert.equal(repeated.output.operation_id, terminal.detail.operation_id)
  assert.equal(draftReads, 1)
})

test('an interrupted old mutation cannot overwrite a newer user turn', async () => {
  let state = { version: 1, preferred_date: '2026-10-06' }
  let activeGeneration = 1
  let releaseOldRead
  let markOldReadStarted
  const oldReadStarted = new Promise((resolve) => { markOldReadStarted = resolve })
  const oldReadPending = new Promise((resolve) => { releaseOldRead = resolve })
  const history = []
  const toolClient = {
    getBookingDraft: async (received) => {
      if (received.turnId === 'old-turn') {
        markOldReadStarted()
        await oldReadPending
      }
      return { draft: draft(state) }
    },
    updateBookingDraft: async (received, patch) => {
      assert.equal(patch.expected_version, state.version)
      state = { version: state.version + 1, preferred_date: patch.preferred_date }
      history.push([received.turnId, state.preferred_date])
      return {
        draft: draft(state),
        changed_fields: ['preferred_date'],
        missing_for_availability: [],
        missing_for_booking: ['selected_availability'],
      }
    },
  }
  const coordinator = new MerchantCoordinator({ toolClient })
  const older = coordinator.runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-10-07' },
    {
      ...context,
      turnId: 'old-turn',
      turnGeneration: 1,
      invocationId: 'old-call',
      isActive: () => activeGeneration === 1,
    },
  )
  await oldReadStarted
  activeGeneration = 2
  const newer = coordinator.runBusinessTool(
    'update_booking_draft',
    { preferred_date: '2026-10-08' },
    {
      ...context,
      turnId: 'new-turn',
      turnGeneration: 2,
      invocationId: 'new-call',
      isActive: () => activeGeneration === 2,
    },
  )
  releaseOldRead()

  await assert.rejects(older, { code: 'stale_business_turn' })
  await newer
  assert.deepEqual(history, [['new-turn', '2026-10-08']])
  assert.equal(state.preferred_date, '2026-10-08')
})

test('transient read failures are not cached and can be retried', async () => {
  let attempts = 0
  const coordinator = new MerchantCoordinator({
    toolClient: {
      getMerchantInfo: async () => {
        attempts += 1
        if (attempts === 1) throw new Error('temporary failure')
        return {
          merchant: {
            id: 'demo-grooming-001', display_name: 'Demo', address: 'Demo',
            phone: '+1-415-555-0100', time_zone: 'America/Los_Angeles',
            is_demo: true, source: 'test', services: [], staff: [],
          },
          merchant_local_date: '2026-09-19',
          merchant_local_datetime: '2026-09-19T10:00:00-07:00',
        }
      },
    },
  })

  await assert.rejects(coordinator.runBusinessTool('get_merchant_info', {}, context))
  const retried = await coordinator.runBusinessTool('get_merchant_info', {}, context)
  assert.equal(retried.output.status, 'ok')
  assert.equal(attempts, 2)
})

test('booking status is recoverable after coordinator restart and cards match persisted status', async () => {
  const statusReads = []
  const credential = {
    booking_reference: 'BK-DEMO',
    access_token: 'client-only-token',
    credential_url: 'http://example.test/#token=client-only-token',
  }
  let status = 'confirmed'
  const toolClient = {
    getBookingStatus: async (received, operationId) => {
      statusReads.push([received.ownerId, received.sessionId, operationId])
      if (received.ownerId !== context.ownerId || received.sessionId !== context.sessionId) {
        throw new Error('backend rejected untrusted operation context')
      }
      return {
        operation_id: preparationResult().operation_id,
        status,
        booking: {
          status,
          confirmation_code: status === 'confirmed' ? 'PR-DEMO' : null,
        },
        customer_credential: credential,
      }
    },
  }
  const restarted = new MerchantCoordinator({ toolClient })
  const confirmed = await restarted.runBusinessTool(
    'get_booking_status',
    { operation_id: preparationResult().operation_id },
    { ...context, turnId: 'status-after-restart' },
  )
  assert.equal(confirmed.output.status, 'confirmed')
  assert.match(confirmed.clientPresentation.content, /预约已确认，预约金已支付/)
  assert.match(confirmed.clientPresentation.content, /不表示全部服务费用已结清/)
  assert.doesNotMatch(confirmed.clientPresentation.content, /当前为等待本地模拟支付/)

  status = 'payment_expired'
  const expired = await restarted.runBusinessTool(
    'get_booking_status',
    { operation_id: preparationResult().operation_id },
    { ...context, turnId: 'status-expired' },
  )
  assert.match(expired.clientPresentation.content, /付款占位已过期/)
  assert.equal(JSON.stringify(expired.output).includes('client-only-token'), false)

  await assert.rejects(
    restarted.runBusinessTool(
      'get_booking_status',
      { operation_id: preparationResult().operation_id },
      { ...context, ownerId: 'attacker', turnId: 'status-attacker' },
    ),
    /backend rejected untrusted operation context/,
  )
  assert.equal(statusReads.length, 3)
})

test('an uncertain confirmation response is verified by operation id without resubmitting', async () => {
  let confirmCalls = 0
  let statusCalls = 0
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft() }),
    prepareBooking: async () => preparationResult(),
    markBookingPresented: async () => ({ ...preparationResult(), status: 'presented' }),
    confirmBooking: async () => {
      confirmCalls += 1
      const error = new Error('connection ended after write')
      error.code = 'result_unknown'
      throw error
    },
    getBookingStatus: async (_received, operationId) => {
      statusCalls += 1
      assert.equal(operationId, preparationResult().operation_id)
      return {
        operation_id: operationId,
        status: 'awaiting_payment',
        booking: { status: 'awaiting_payment', customer_reference: 'BK-RECOVERED' },
        customer_credential: null,
      }
    },
  }
  const coordinator = new MerchantCoordinator({ toolClient })
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: '33333333-3333-4333-8333-333333333333' },
    context,
  )
  await coordinator.markBookingPresented(
    {
      preparationId: prepared.presentation.preparationId,
      eventId: prepared.presentation.eventId,
    },
    { ...context, transcript: completeSummaryTranscript() },
  )
  const result = await coordinator.runBusinessTool(
    'confirm_booking',
    { preparation_id: prepared.presentation.preparationId },
    { ...context, turnId: 'turn-confirm-unknown', invocationId: 'confirm-unknown' },
  )

  assert.equal(result.output.status, 'awaiting_payment')
  assert.equal(confirmCalls, 1)
  assert.equal(statusCalls, 1)
})

test('an uncertain confirmation stays unknown while the same operation is only presented', async () => {
  let confirmCalls = 0
  let statusCalls = 0
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft() }),
    prepareBooking: async () => preparationResult(),
    markBookingPresented: async () => ({ ...preparationResult(), status: 'presented' }),
    confirmBooking: async () => {
      confirmCalls += 1
      const error = new Error('connection ended after write')
      error.code = 'result_unknown'
      throw error
    },
    getBookingStatus: async (_received, operationId) => {
      statusCalls += 1
      return {
        ...preparationResult(),
        operation_id: operationId,
        status: 'presented',
        preparation: { ...preparationResult().preparation, status: 'presented' },
      }
    },
  }
  const coordinator = new MerchantCoordinator({ toolClient })
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: '33333333-3333-4333-8333-333333333333' },
    context,
  )
  await coordinator.markBookingPresented({
    preparationId: prepared.presentation.preparationId,
    eventId: prepared.presentation.eventId,
  }, { ...context, transcript: completeSummaryTranscript() })

  await assert.rejects(
    coordinator.runBusinessTool(
      'confirm_booking',
      { preparation_id: prepared.presentation.preparationId },
      { ...context, turnId: 'turn-confirm-unknown', invocationId: 'confirm-unknown-presented' },
    ),
    (error) => (
      error.code === 'result_unknown'
      && error.operationId === preparationResult().operation_id
      && error.observedStatus === 'presented'
    ),
  )
  assert.equal(confirmCalls, 1)
  assert.equal(statusCalls, 1)
})

test('an uncertain confirmation keeps its operation id when status lookup also fails', async () => {
  const toolClient = {
    getBookingDraft: async () => ({ draft: draft() }),
    prepareBooking: async () => preparationResult(),
    markBookingPresented: async () => ({ ...preparationResult(), status: 'presented' }),
    confirmBooking: async () => {
      const error = new Error('connection ended after write')
      error.code = 'result_unknown'
      throw error
    },
    getBookingStatus: async () => { throw new TypeError('status connection failed') },
  }
  const coordinator = new MerchantCoordinator({ toolClient })
  const prepared = await coordinator.runBusinessTool(
    'prepare_booking',
    { candidate_id: '33333333-3333-4333-8333-333333333333' },
    context,
  )
  await coordinator.markBookingPresented({
    preparationId: prepared.presentation.preparationId,
    eventId: prepared.presentation.eventId,
  }, { ...context, transcript: completeSummaryTranscript() })

  await assert.rejects(
    coordinator.runBusinessTool(
      'confirm_booking',
      { preparation_id: prepared.presentation.preparationId },
      { ...context, turnId: 'turn-confirm-query-fails', invocationId: 'confirm-query-fails' },
    ),
    (error) => (
      error.code === 'result_unknown'
      && error.operationId === preparationResult().operation_id
      && error.detail?.recovery === 'query_same_operation'
    ),
  )
})

test('a restarted coordinator replays a persisted presented summary before confirming', async () => {
  let presentationWrites = 0
  let confirmCalls = 0
  const persistedPresentationId = 'presentation_persisted_restart'
  const persisted = {
    ...preparationResult(),
    status: 'presented',
    preparation: {
      ...preparationResult().preparation,
      status: 'presented',
      presentation_id: persistedPresentationId,
    },
  }
  const toolClient = {
    getBookingPreparation: async (_received, preparationId) => {
      assert.equal(preparationId, preparationResult().preparation.id)
      return persisted
    },
    markBookingPresented: async (_received, preparationId, draftVersion, eventId) => {
      presentationWrites += 1
      assert.equal(preparationId, preparationResult().preparation.id)
      assert.equal(draftVersion, 2)
      assert.equal(eventId, persistedPresentationId)
      return persisted
    },
    confirmBooking: async (_received, preparationId, draftVersion) => {
      confirmCalls += 1
      assert.equal(preparationId, preparationResult().preparation.id)
      assert.equal(draftVersion, 2)
      return {
        ...persisted,
        status: 'awaiting_payment',
        booking: { id: 'booking-recovered', status: 'awaiting_payment' },
        customer_credential: null,
      }
    },
  }
  const restarted = new MerchantCoordinator({ toolClient })
  let replay
  await assert.rejects(
    restarted.runBusinessTool(
      'confirm_booking',
      { preparation_id: preparationResult().preparation.id },
      { ...context, turnId: 'turn-after-restart' },
    ),
    (error) => {
      assert.equal(error instanceof BookingSummaryPlaybackRequiredError, true)
      replay = error.presentation
      return true
    },
  )
  assert.equal(confirmCalls, 0)
  assert.equal(presentationWrites, 0)
  assert.equal(replay.eventId, persistedPresentationId)
  assert.match(replay.speech, /现在还没有创建正式预约/)

  const replayed = await restarted.markBookingPresented({
    preparationId: replay.preparationId,
    eventId: replay.eventId,
  }, {
    ...context,
    turnId: 'turn-summary-replay',
    transcript: replay.speech,
  })
  assert.equal(replayed.status, 'presented')
  assert.equal(presentationWrites, 1)

  const confirmed = await restarted.runBusinessTool(
    'confirm_booking',
    { preparation_id: replay.preparationId },
    { ...context, turnId: 'turn-confirm-after-replay' },
  )
  assert.equal(confirmed.output.status, 'awaiting_payment')
  assert.equal(confirmCalls, 1)
})

test('restart recovery returns terminal preparation state without creating another booking', async () => {
  let confirmCalls = 0
  for (const recovered of [
    {
      ...preparationResult(),
      status: 'expired',
      preparation: { ...preparationResult().preparation, status: 'expired' },
    },
    {
      ...preparationResult(),
      status: 'cancelled',
      preparation: { ...preparationResult().preparation, status: 'cancelled' },
    },
    {
      ...preparationResult(),
      status: 'confirmed',
      preparation: { ...preparationResult().preparation, status: 'confirmed' },
      booking: { id: 'existing-booking', status: 'confirmed' },
      customer_credential: null,
    },
  ]) {
    const restarted = new MerchantCoordinator({
      toolClient: {
        getBookingPreparation: async () => recovered,
        confirmBooking: async () => { confirmCalls += 1 },
      },
    })
    const result = await restarted.runBusinessTool(
      'confirm_booking',
      { preparation_id: preparationResult().preparation.id },
      { ...context, turnId: `turn-recover-${recovered.status}` },
    )
    assert.equal(result.output.status, recovered.status)
    if (recovered.status === 'confirmed') {
      assert.equal(result.output.booking.id, 'existing-booking')
    }
  }
  assert.equal(confirmCalls, 0)
})

test('late business result is attached without causing speech or a second submit', async () => {
  process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST = 'get_merchant_info'
  const require = createRequire(import.meta.url)
  const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))
  const { ToolCallHandler } = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'tools',
    'tool-call-handler.mjs',
  )))
  let resolveTool
  const pending = new Promise((resolvePromise) => { resolveTool = resolvePromise })
  let generation = 0
  let currentTurn = 'turn-a'
  const outputs = []
  const responses = []
  const clientEvents = []
  const handler = new ToolCallHandler({
    taskManager: {},
    ownerId: context.ownerId,
    sessionId: context.sessionId,
    transcripts: {},
    getFrontend: () => ({
      sendFunctionOutput: async (_callId, output, _context, options) => {
        outputs.push(output)
        responses.push(options?.createResponse !== false)
      },
    }),
    getTurnId: () => currentTurn,
    getTurnGeneration: () => generation,
    sendClientEvent: (event) => clientEvents.push(event),
    coordinator: {
      runBusinessTool: async (_name, _args, received) => {
        assert.equal(received.ownerId, context.ownerId)
        assert.equal(received.sessionId, context.sessionId)
        assert.equal(received.turnId, context.turnId)
        assert.equal(received.turnGeneration, 0)
        assert.equal(received.invocationId, 'call-a')
        assert.equal(received.isActive(), true)
        return pending
      },
    },
  })

  const handling = handler.handle({
    call_id: 'call-a',
    name: 'get_merchant_info',
    arguments: '{"merchant_id":"attacker"}',
  }, { turnId: 'turn-a', turnGeneration: 0 })
  generation = 1
  currentTurn = 'turn-b'
  resolveTool({
    output: { status: 'ok', merchant: { id: 'trusted' } },
    clientPresentation: {
      title: '客户预约页面',
      format: 'markdown',
      content: '[打开客户预约页面](http://trusted.example/#token=client-only)',
    },
  })
  await handling

  assert.equal(outputs.length, 1)
  assert.equal(outputs[0].status, 'completed_after_interruption')
  assert.equal(outputs[0].result.merchant.id, 'trusted')
  assert.deepEqual(responses, [false])
  assert.equal(clientEvents.length, 1)
  assert.match(clientEvents[0].item.content, /client-only/)
  assert.equal(JSON.stringify(outputs).includes('client-only'), false)
})

test('a stale business failure is silent just like a stale success', async () => {
  process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST = 'search_availability'
  const require = createRequire(import.meta.url)
  const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))
  const { ToolCallHandler } = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'tools',
    'tool-call-handler.mjs',
  )))
  let rejectTool
  const pending = new Promise((_resolve, reject) => { rejectTool = reject })
  let generation = 0
  const outputs = []
  const handler = new ToolCallHandler({
    taskManager: {},
    ownerId: context.ownerId,
    sessionId: context.sessionId,
    transcripts: {},
    getFrontend: () => ({
      sendFunctionOutput: async (_callId, output, _context, options) => {
        outputs.push([output, options])
      },
    }),
    getTurnId: () => generation ? 'turn-b' : 'turn-a',
    getTurnGeneration: () => generation,
    coordinator: { runBusinessTool: async () => pending },
  })

  const handling = handler.handle({
    call_id: 'call-stale-failure',
    name: 'search_availability',
    arguments: '{}',
  }, { turnId: 'turn-a', turnGeneration: 0 })
  generation = 1
  rejectTool(new Error('old request failed'))
  await handling

  assert.equal(outputs.length, 1)
  assert.equal(outputs[0][0].status, 'ignored_after_interruption')
  assert.equal(outputs[0][1].createResponse, false)
})

test('a spoken wait preamble still receives one post-tool continuation with timing', async () => {
  process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST = 'search_availability'
  const require = createRequire(import.meta.url)
  const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))
  const { ToolCallHandler } = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'tools',
    'tool-call-handler.mjs',
  )))
  const outputs = []
  const events = []
  let continuations = 0
  const handler = new ToolCallHandler({
    taskManager: {},
    ownerId: context.ownerId,
    sessionId: context.sessionId,
    transcripts: {},
    getFrontend: () => ({
      sendFunctionOutput: async (_callId, output, _context, options) => {
        outputs.push([output, options])
      },
      ensureResponse: async () => { continuations += 1 },
    }),
    getTurnId: () => 'turn-wait',
    getTurnGeneration: () => 5,
    onBusinessToolEvent: (event) => events.push(event),
    coordinator: {
      runBusinessTool: async () => ({
        output: { status: 'ok', search: { status: 'available', candidates: [] } },
      }),
    },
  })

  await handler.handle({
    call_id: 'call-wait',
    name: 'search_availability',
    arguments: JSON.stringify({ contact_phone: '123456789' }),
  }, {
    turnId: 'turn-wait',
    turnGeneration: 5,
    responseId: 'response-with-spoken-wait',
  })
  assert.equal(continuations, 0)

  await handler.finishToolResponse(
    'response-with-spoken-wait',
    { suppressResponse: true },
  )
  await handler.finishToolResponse(
    'response-with-spoken-wait',
    { suppressResponse: true },
  )

  assert.equal(continuations, 1)
  assert.equal(outputs[0][1].createResponse, false)
  assert.deepEqual(events.map((event) => event.phase), ['started', 'completed'])
  assert.equal(Number.isInteger(events[1].durationMs), true)
  assert.equal(JSON.stringify(events).includes('123456789'), false)
})

test('an interrupted summary confirmation replays through the controlled speak path', async () => {
  process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST = 'confirm_booking'
  const require = createRequire(import.meta.url)
  const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))
  const { ToolCallHandler } = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'tools',
    'tool-call-handler.mjs',
  )))
  const outputs = []
  const spoken = []
  const replay = {
    preparationId: preparationResult().preparation.id,
    eventId: 'presentation_trusted-replay',
    speech: '受控预约摘要全文',
  }
  const handler = new ToolCallHandler({
    taskManager: {},
    ownerId: context.ownerId,
    sessionId: context.sessionId,
    transcripts: {},
    getFrontend: () => ({
      sendFunctionOutput: async (_callId, output, _context, options) => {
        outputs.push([output, options])
      },
      speak: async (...args) => spoken.push(args),
      ensureResponse: async () => assert.fail('generic model response must not replace replay'),
    }),
    getTurnId: () => 'turn-confirm',
    getTurnGeneration: () => 4,
    coordinator: {
      runBusinessTool: async () => {
        throw new BookingSummaryPlaybackRequiredError(replay)
      },
    },
  })

  await handler.handle({
    call_id: 'call-confirm',
    name: 'confirm_booking',
    arguments: JSON.stringify({ preparation_id: replay.preparationId }),
  }, {
    turnId: 'turn-confirm',
    turnGeneration: 4,
    responseId: 'response-with-confirm-tool',
  })
  await handler.finishToolResponse('response-with-confirm-tool')

  assert.equal(outputs.length, 1)
  assert.equal(outputs[0][0].error_code, 'confirmation_not_ready')
  assert.equal(outputs[0][1].createResponse, false)
  assert.equal(spoken.length, 1)
  assert.deepEqual(spoken[0], [
    replay.speech,
    'booking-summary',
    {
      turnId: 'turn-confirm',
      turnGeneration: 4,
      bookingPresentation: {
        preparationId: replay.preparationId,
        eventId: replay.eventId,
      },
    },
  ])
})

test('quote_service is read-only and reports only the current quote goal', async () => {
  const calls = []
  const coordinator = new MerchantCoordinator({
    toolClient: {
      quoteService: async (_context, body) => {
        calls.push(body)
        return {
          quote: {
            can_quote: false,
            missing_for_quote: ['weight'],
            missing_for_booking: ['weight', 'selected_availability'],
            service_id: 'synthetic-dog-bath',
          },
        }
      },
    },
  })
  const result = await coordinator.runBusinessTool('quote_service', {
    service_id: 'synthetic-dog-bath',
    pet_species: '犬',
  }, context)

  assert.deepEqual(calls, [{
    service_id: 'synthetic-dog-bath', pet_species: '犬', selected_addon_ids: [],
  }])
  assert.equal(result.output.current_goal, 'quote_service')
  assert.deepEqual(result.output.missing_for_current_goal, ['weight'])
  assert.equal(result.output.effect.draft_mutated, false)
})

test('quote_service separates customer input from merchant configuration gaps', async () => {
  const quotes = [
    {
      can_quote: false,
      missing_for_quote: ['service_pricing_configuration'],
      missing_for_booking: ['service_pricing_configuration', 'deposit_configuration'],
      service_id: 'legacy-service',
    },
    {
      can_quote: false,
      missing_for_quote: ['weight'],
      missing_for_booking: ['weight', 'deposit_configuration'],
      service_id: 'configured-service',
    },
  ]
  const coordinator = new MerchantCoordinator({
    toolClient: {
      quoteService: async () => ({ quote: quotes.shift() }),
    },
  })

  const unavailable = await coordinator.runBusinessTool(
    'quote_service',
    { service_id: 'legacy-service' },
    context,
  )
  assert.equal(unavailable.output.status, 'unavailable')
  assert.deepEqual(unavailable.output.missing_from_customer, [])
  assert.deepEqual(unavailable.output.merchant_configuration_issues, [
    'service_pricing_configuration',
    'deposit_configuration',
  ])
  assert.equal(unavailable.output.next_step.action, 'explain_quote_unavailable')

  const needsWeight = await coordinator.runBusinessTool(
    'quote_service',
    { service_id: 'configured-service', pet_species: '犬' },
    context,
  )
  assert.equal(needsWeight.output.status, 'needs_input')
  assert.deepEqual(needsWeight.output.missing_from_customer, ['weight'])
  assert.deepEqual(needsWeight.output.merchant_configuration_issues, [
    'deposit_configuration',
  ])
  assert.equal(needsWeight.output.next_step.action, 'ask_one_field')
  assert.equal(needsWeight.output.next_step.field, 'weight')
})

test('controlled summary rejects a wrong amount or missing paid add-on', () => {
  const priced = {
    ...summary(),
    service_quote: {
      total_semantics: 'fixed',
      total_min_cents: 11700,
      total_max_cents: 11700,
      currency: 'USD',
      deposit_amount_cents: 3000,
      deposit_currency: 'USD',
      line_items: [{ kind: 'addon', display_name: '刷牙' }],
    },
  }
  const correct = completeSummaryTranscript().replace(
    '备注：',
    '收费附加项目：刷牙。服务报价117美元。本次应付预约金30美元。备注：',
  )
  assert.equal(presentationTranscriptMatches(priced, correct), true)
  assert.equal(presentationTranscriptMatches(priced, correct.replace('117美元', '118美元')), false)
  assert.equal(presentationTranscriptMatches(priced, correct.replace('收费附加项目：刷牙。', '')), false)
  assert.equal(presentationTranscriptMatches(priced, correct.replace('30美元', '20美元')), false)
})
