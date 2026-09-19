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

  const first = await coordinator.runBusinessTool('update_booking_draft', args, context)
  const second = await coordinator.runBusinessTool('update_booking_draft', args, context)

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

  const transcript = '请确认预约：王女士为犬豆包预约基础洗护，由 Lisa 服务，联系电话已核对。'
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
  const transcript = '请确认预约：王女士为犬豆包预约基础洗护，由 Lisa 服务，联系电话已核对。'
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
        assert.deepEqual(received, context)
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
