import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const require = createRequire(import.meta.url)
const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))
const allowedTools = [
  'get_merchant_info',
  'update_booking_draft',
  'search_availability',
  'prepare_booking',
  'confirm_booking',
  'get_booking_status',
]

test('actual Qwen session exposes only the six bounded M4 business tools', async () => {
  process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST = allowedTools.join(',')
  const providerModule = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'providers',
    'dashscope.mjs',
  )))
  const session = providerModule.dashscopeProvider.buildSession({
    configured: false,
    agentContext: {},
  })

  assert.deepEqual(
    session.tools.map((tool) => tool.function.name),
    allowedTools,
  )
  const update = session.tools.find((tool) => tool.function.name === 'update_booking_draft')
  const confirm = session.tools.find((tool) => tool.function.name === 'confirm_booking')
  assert.match(update.function.description, /一次批量写入/)
  assert.equal(update.function.parameters.additionalProperties, false)
  assert.equal(Object.hasOwn(update.function.parameters.properties, 'merchant_id'), false)
  assert.equal(Object.hasOwn(update.function.parameters.properties, 'owner_id'), false)
  assert.equal(
    update.function.parameters.properties.search_availability_after_update.type,
    'boolean',
  )
  const merchantInfo = session.tools.find((tool) => tool.function.name === 'get_merchant_info')
  assert.match(merchantInfo.function.description, /商家当地日期/)
  assert.match(merchantInfo.function.description, /推荐边界/)
  assert.match(confirm.function.description, /新一轮明确确认/)
  assert.doesNotMatch(session.tools.map((tool) => tool.function.name).join(','), /spawn_thinking|memory|notes|schedule/)
})

test('forced disabled tool calls are rejected before any side effect', async () => {
  process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST = allowedTools.join(',')
  const handlerModule = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'tools',
    'tool-call-handler.mjs',
  )))
  const outputs = []
  const sideEffects = []
  const handler = new handlerModule.ToolCallHandler({
    taskManager: {
      createScheduled: () => sideEffects.push('scheduled'),
    },
    ownerId: 'user_test',
    sessionId: 'session-a',
    transcripts: {},
    getFrontend: () => ({
      sendFunctionOutput: async (_callId, output) => outputs.push(output),
    }),
    getTurnId: () => 'turn-a',
    getTurnGeneration: () => 0,
    coordinator: { runBusinessTool: () => sideEffects.push('business') },
    memoryService: {
      append: () => sideEffects.push('memory'),
      replace: () => sideEffects.push('memory'),
      list: () => [],
    },
    notesStore: {
      mutate: () => sideEffects.push('notes'),
    },
    respondPermission: () => sideEffects.push('permission'),
  })

  for (const [index, name] of ['memory', 'notes', 'schedule_reminder'].entries()) {
    await handler.handle({
      call_id: `blocked-${index}`,
      name,
      arguments: '{}',
    }, {
      turnId: 'turn-a',
      turnGeneration: 0,
    })
  }

  assert.deepEqual(sideEffects, [])
  assert.equal(outputs.length, 3)
  outputs.forEach((output) => {
    assert.equal(output.status, 'failed')
    assert.equal(output.error, true)
    assert.equal(output.error_code, 'tool_not_allowed')
  })
})
