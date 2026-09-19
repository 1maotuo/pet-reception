import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const require = createRequire(import.meta.url)
const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))
const allowedTools = [
  'spawn_thinking',
  'cancel_agent_task',
  'get_agent_task_status',
]

test('actual Qwen session exposes only bounded M1 tools with accurate descriptions', async () => {
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
  const spawn = session.tools.find((tool) => tool.function.name === 'spawn_thinking')
  assert.match(spawn.function.description, /当前演示商家公开资料/)
  assert.doesNotMatch(spawn.function.description, /搜索|文件|屏幕|应用|代码|图片生成/)
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
    coordinator: {},
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
