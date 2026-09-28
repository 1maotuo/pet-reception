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
  process.env.DEMO_MERCHANT_ID = 'demo-grooming-001'
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
  assert.match(instructions, /不得生成假的预约编号、[\s\S]*确认码、金额或支付结果/)
  assert.match(instructions, /七个业务工具/)
  assert.match(instructions, /`quote_service`/)
  assert.match(instructions, /`missing_from_customer` 才是客户/)
  assert.match(instructions, /`merchant_configuration_issues` 是商家配置问题/)
  assert.match(instructions, /下一轮只补充体重/)
  assert.match(instructions, /不能把只读询价偷偷变成预约选择/)
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
  assert.match(instructions, /`status` → `current_goal` → `ready_for_current_goal`/)
  assert.match(instructions, /`missing_for_current_goal` → `missing_for_booking_later`/)
  assert.match(instructions, /`changed_fields=\[\]` 只表示/)
  assert.match(instructions, /`for_goal=search_availability`/)
  assert.match(instructions, /`next_step\.action=ask_one_field`/)
  assert.match(instructions, /不要自行填写或朗读\s*`staff_id`/)
  assert.match(instructions, /Availability 是实时业务事实，不从聊天历史复述/)
  assert.match(instructions, /新意图是“27号上午”/)
  assert.match(instructions, /`belongs_to_current_draft=true`/)
  assert.match(instructions, /已知资料不得重复追问/)
  assert.match(instructions, /不因为有预约工具就强行进入/)
  assert.doesNotMatch(instructions, /只有工具明确返回成功和 `changed_fields`/)
  assert.match(instructions, /不能说“预约已经准备好了”/)
  assert.match(instructions, /没有字段\s*依据就明确说资料未提供/)
  assert.match(instructions, /`pet_age_text`/)
  assert.match(instructions, /不得换算为一位小数年/)
  assert.match(instructions, /`booking_already_submitted` 表示当前会话已经有预约/)
  assert.match(instructions, /点击网页\s*“开始新预约”/)
  assert.match(instructions, /本阶段暂缓无日期搜索/)
  assert.match(instructions, /替代日期只表示那天可能有空/)
  assert.match(instructions, /不能停在“稍等”/)
  assert.match(instructions, /不要承诺未经监控证实/)
  assert.match(instructions, /宠物名字属于身份字段/)
})

test('renders the runtime combination pricing demo into the actual frontend prompt', () => {
  process.env.VOICE_GATEWAY_SERVICE_TOKEN = 'test-service-token'
  process.env.QWEN_AUDIO_AGENT_AUTH_SECRET = 'test-auth-secret-at-least-32-characters'
  process.env.MERCHANT_CONFIG_PATH = 'config/demo-grooming-combination-merchant.json'
  process.env.DEMO_MERCHANT_ID = 'demo-grooming-combo-001'

  const configuration = configureEnvironment()
  const instructions = readFileSync(
    resolve(process.env.QWEN_AUDIO_AGENT_FRONTEND_PROMPT_DIR, 'PROMPT.md'),
    'utf8',
  )

  assert.equal(configuration.merchantId, 'demo-grooming-combo-001')
  assert.match(configuration.initialGreeting, /犬猫美容组合计价演示店/)
  assert.match(instructions, /Paws & Whiskers 犬猫美容组合计价演示店/)
  assert.match(instructions, /"merchant_id": "demo-grooming-combo-001"/)
})
