import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'
import { loadMerchantContext, prepareFrontendPrompt } from './merchant-context.mjs'

const moduleDirectory = dirname(fileURLToPath(import.meta.url))
const gatewayRoot = resolve(moduleDirectory, '..')
const projectRoot = resolve(gatewayRoot, '..')
const runtimeRoot = resolve(gatewayRoot, 'runtime')

dotenv.config({ path: resolve(projectRoot, '.env'), quiet: true })

function requiredSecret(name) {
  const value = String(process.env[name] || '').trim()
  if (!value || /^(GENERATE_|CHANGE_|REPLACE_)/i.test(value)) {
    throw new Error(`${name} is required; run scripts/init-env.ps1 and keep the generated .env local`)
  }
  return value
}

export function configureEnvironment() {
  mkdirSync(runtimeRoot, { recursive: true })

  const serviceToken = requiredSecret('VOICE_GATEWAY_SERVICE_TOKEN')
  const authSecret = requiredSecret('QWEN_AUDIO_AGENT_AUTH_SECRET')
  if (authSecret.length < 32) {
    throw new Error('QWEN_AUDIO_AGENT_AUTH_SECRET must contain at least 32 characters')
  }

  const merchant = loadMerchantContext(
    process.env.MERCHANT_CONFIG_PATH
      ? resolve(projectRoot, process.env.MERCHANT_CONFIG_PATH)
      : undefined,
  )
  const configuredMerchantId = process.env.DEMO_MERCHANT_ID || merchant.merchantId
  if (configuredMerchantId !== merchant.merchantId) {
    throw new Error('DEMO_MERCHANT_ID must match the trusted merchant configuration')
  }
  const promptDirectory = prepareFrontendPrompt({ merchant, runtimeRoot })

  const turnDetectionType = process.env.VOICE_TURN_DETECTION_TYPE || 'server_vad'
  if (!['server_vad', 'smart_turn'].includes(turnDetectionType)) {
    throw new Error('VOICE_TURN_DETECTION_TYPE must be server_vad or smart_turn')
  }
  const silenceDurationMs = Number(process.env.VOICE_SILENCE_DURATION_MS || '1800')
  if (!Number.isInteger(silenceDurationMs) || silenceDurationMs < 200 || silenceDurationMs > 6000) {
    throw new Error('VOICE_SILENCE_DURATION_MS must be an integer from 200 to 6000')
  }
  const vadThreshold = Number(process.env.VOICE_VAD_THRESHOLD || '0.65')
  if (!Number.isFinite(vadThreshold) || vadThreshold < -1 || vadThreshold > 1) {
    throw new Error('VOICE_VAD_THRESHOLD must be a number from -1 to 1')
  }
  const preferredAudioInputLabel = String(process.env.VOICE_AUDIO_INPUT_LABEL || '').trim()
  if (preferredAudioInputLabel.length > 128) {
    throw new Error('VOICE_AUDIO_INPUT_LABEL must contain at most 128 characters')
  }

  process.env.HOST = process.env.VOICE_GATEWAY_HOST || '127.0.0.1'
  process.env.PORT = process.env.VOICE_GATEWAY_PORT || '3101'
  process.env.QWEN_AUDIO_AGENT_AUTH_SECRET = authSecret
  process.env.QWEN_AUDIO_AGENT_IDENTITY_MODE = 'browser'
  process.env.QWEN_AUDIO_AGENT_ALLOWED_ORIGINS = process.env.WEB_ORIGIN || 'http://127.0.0.1:5173'
  process.env.QWEN_AUDIO_AGENT_FRONTEND_PROMPT_DIR = promptDirectory
  process.env.QWEN_AUDIO_AGENT_ASSISTANT_PROFILE_PATH = resolve(gatewayRoot, 'config', 'ASSISTANT.md')
  process.env.QWEN_AUDIO_AGENT_TASK_STATE_PATH = resolve(runtimeRoot, 'tasks.json')
  process.env.QWEN_AUDIO_AGENT_MEMORY_PATH = resolve(runtimeRoot, 'memory')
  process.env.QWEN_AUDIO_AGENT_FRONTEND_NOTES_PATH = resolve(runtimeRoot, 'notes.json')
  process.env.QWEN_AUDIO_MEMORY_AUTO = 'off'
  process.env.QWEN_AUDIO_AGENT_REMINDER_SCHEDULER = 'false'
  process.env.QWEN_AUDIO_WAKE_WORD_ENABLED = 'false'
  process.env.PET_RECEPTION_TURN_DETECTION_TYPE = turnDetectionType
  process.env.PET_RECEPTION_SILENCE_DURATION_MS = String(silenceDurationMs)
  process.env.PET_RECEPTION_VAD_THRESHOLD = String(vadThreshold)
  process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST = [
    'spawn_thinking',
    'cancel_agent_task',
    'get_agent_task_status',
  ].join(',')
  delete process.env.AGENT_PROTOCOL

  const backendPort = process.env.BACKEND_PORT || '8010'

  return {
    backendBaseUrl: process.env.BACKEND_BASE_URL || `http://127.0.0.1:${backendPort}`,
    merchantId: merchant.merchantId,
    merchant,
    initialGreeting: merchant.openingGreeting,
    turnDetectionType,
    silenceDurationMs,
    vadThreshold,
    preferredAudioInputLabel,
    serviceToken,
  }
}
