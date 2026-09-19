import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { configureEnvironment } from '../src/config.mjs'

test('renders a store-specific M1 front desk prompt from trusted configuration', async () => {
  process.env.VOICE_GATEWAY_SERVICE_TOKEN = 'test-service-token'
  process.env.QWEN_AUDIO_AGENT_AUTH_SECRET = 'test-auth-secret-at-least-32-characters'
  delete process.env.MERCHANT_CONFIG_PATH
  delete process.env.VOICE_TURN_DETECTION_TYPE
  delete process.env.VOICE_SILENCE_DURATION_MS
  delete process.env.VOICE_VAD_THRESHOLD
  process.env.VOICE_AUDIO_INPUT_LABEL = 'USB Audio Device'

  const configuration = configureEnvironment()

  const expectedDirectory = resolve(
    dirname(fileURLToPath(import.meta.url)),
    '..',
    'runtime',
    'frontend-agent',
  )
  assert.equal(
    process.env.QWEN_AUDIO_AGENT_FRONTEND_PROMPT_DIR,
    expectedDirectory,
  )

  const instructions = readFileSync(
    resolve(expectedDirectory, 'PROMPT.md'),
    'utf8',
  )

  assert.match(instructions, /Paws & Bubbles 宠物美容（演示）/)
  assert.match(instructions, /"merchant_type": "grooming"/)
  assert.match(instructions, /当前商家自己的中文语音预约前台/)
  assert.match(instructions, /不要反问\s*商家名称、商家 ID、客户会话标识/)
  assert.match(instructions, /必须立即调用 `spawn_thinking`/)
  assert.match(instructions, /要求推荐，也属于当前商家\s*服务查询/)
  assert.match(instructions, /不能凭“基础”“全套”等名称或行业常识补充、推断或保证/)
  assert.match(instructions, /不得替用户选定或宣称某项能解决其问题/)
  assert.match(instructions, /不生成预约编号、确认码或成功凭证/)
  assert.doesNotMatch(instructions, /推进用户\s*电脑上的实际工作/)
  assert.doesNotMatch(instructions, /\{\{TRUSTED_MERCHANT_CONTEXT\}\}/)
  assert.equal(configuration.merchantId, 'demo-grooming-001')
  assert.match(configuration.initialGreeting, /Paws & Bubbles/)
  assert.equal(configuration.turnDetectionType, 'server_vad')
  assert.equal(configuration.silenceDurationMs, 1800)
  assert.equal(configuration.vadThreshold, 0.65)
  assert.equal(configuration.preferredAudioInputLabel, 'USB Audio Device')
  assert.match(instructions, /用户可能慢慢说、分段说/)
})
