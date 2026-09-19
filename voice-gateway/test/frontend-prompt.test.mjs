import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { configureEnvironment } from '../src/config.mjs'

test('renders a store-specific M4 booking front desk prompt from trusted configuration', async () => {
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
  assert.match(instructions, /"merchant_time_zone": "America\/Los_Angeles"/)
  assert.match(instructions, /"merchant_local_date_at_gateway_start": "\d{4}-\d{2}-\d{2}"/)
  assert.match(instructions, /当前商家自己的中文语音预约前台/)
  assert.match(instructions, /商家、客户、会话、轮次、呈现事件和确认事件都由网关注入/)
  assert.match(instructions, /`get_merchant_info`/)
  assert.match(instructions, /一次 `update_booking_draft`/)
  assert.match(instructions, /`search_availability`/)
  assert.match(instructions, /摘要完整播完/)
  assert.match(instructions, /之后的新一轮明确表达/)
  assert.match(instructions, /`awaiting_payment` 表示已/)
  assert.match(instructions, /仍不是正式确认/)
  assert.match(instructions, /不得生成假的预约编号、确认码或支付结果/)
  assert.doesNotMatch(instructions, /推进用户\s*电脑上的实际工作/)
  assert.doesNotMatch(instructions, /\{\{TRUSTED_MERCHANT_CONTEXT\}\}/)
  assert.equal(configuration.merchantId, 'demo-grooming-001')
  assert.match(configuration.initialGreeting, /Paws & Bubbles/)
  assert.equal(configuration.turnDetectionType, 'server_vad')
  assert.equal(configuration.silenceDurationMs, 1800)
  assert.equal(configuration.vadThreshold, 0.65)
  assert.equal(configuration.preferredAudioInputLabel, 'USB Audio Device')
  assert.match(instructions, /明显未说完的片段不/)
  assert.match(instructions, /`merchant_local_date`/)
  assert.match(instructions, /不能填写 `incoming_phone`/)
  assert.match(instructions, /宠物名字未提供时必须追问/)
  assert.match(instructions, /`search_availability_after_update`/)
  assert.match(instructions, /不能说“预约已经准备好了”/)
  assert.match(instructions, /没有字段\s*依据就明确说资料未提供/)
})
