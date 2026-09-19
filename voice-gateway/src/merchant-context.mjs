import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const moduleDirectory = dirname(fileURLToPath(import.meta.url))
const gatewayRoot = resolve(moduleDirectory, '..')
const projectRoot = resolve(gatewayRoot, '..')
const supportedMerchantTypes = new Set(['grooming', 'hospital', 'daycare'])
const PROMPT_TOKEN = '{{TRUSTED_MERCHANT_CONTEXT}}'

function requiredText(object, property) {
  const value = String(object?.[property] || '').trim()
  if (!value) throw new Error(`merchant configuration is missing ${property}`)
  return value
}

function loadCapabilities(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('merchant configuration is missing front_desk.capabilities')
  }
  const capabilities = {}
  for (const name of [
    'merchant_public_info',
    'availability_lookup',
    'booking_write',
    'booking_confirmation',
  ]) {
    if (typeof value[name] !== 'boolean') {
      throw new Error(`merchant capability ${name} must be boolean`)
    }
    capabilities[name] = value[name]
  }
  return capabilities
}

export function loadMerchantContext(
  filePath = resolve(projectRoot, 'config', 'demo-merchant.json'),
) {
  const source = JSON.parse(readFileSync(filePath, 'utf8'))
  const merchantType = requiredText(source, 'merchant_type')
  if (!supportedMerchantTypes.has(merchantType)) {
    throw new Error(`unsupported merchant_type: ${merchantType}`)
  }
  const frontDesk = source.front_desk
  if (!frontDesk || typeof frontDesk !== 'object' || Array.isArray(frontDesk)) {
    throw new Error('merchant configuration is missing front_desk')
  }

  return Object.freeze({
    merchantId: requiredText(source, 'id'),
    displayName: requiredText(source, 'display_name'),
    merchantType,
    timeZone: requiredText(source, 'time_zone'),
    source: requiredText(source, 'source'),
    roleLabel: requiredText(frontDesk, 'role_label'),
    conversationFocus: requiredText(frontDesk, 'conversation_focus'),
    openingGreeting: requiredText(frontDesk, 'opening_greeting'),
    offTopicRedirect: requiredText(frontDesk, 'off_topic_redirect'),
    petHealthGuidance: requiredText(frontDesk, 'pet_health_guidance'),
    humanHealthGuidance: requiredText(frontDesk, 'human_health_guidance'),
    capabilities: Object.freeze(loadCapabilities(frontDesk.capabilities)),
  })
}

function localDateInTimeZone(timeZone, now = new Date()) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(now).map((part) => [part.type, part.value]),
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}

export function trustedPromptContext(merchant, now = new Date()) {
  return {
    merchant_id: merchant.merchantId,
    display_name: merchant.displayName,
    merchant_type: merchant.merchantType,
    merchant_time_zone: merchant.timeZone,
    merchant_local_date_at_gateway_start: localDateInTimeZone(merchant.timeZone, now),
    role_label: merchant.roleLabel,
    conversation_focus: merchant.conversationFocus,
    off_topic_redirect: merchant.offTopicRedirect,
    pet_health_guidance: merchant.petHealthGuidance,
    human_health_guidance: merchant.humanHealthGuidance,
    capabilities: merchant.capabilities,
  }
}

export function prepareFrontendPrompt({ merchant, runtimeRoot }) {
  const templatePath = resolve(
    gatewayRoot,
    'config',
    'frontend-agent',
    'PROMPT.template.md',
  )
  const template = readFileSync(templatePath, 'utf8')
  const tokenCount = template.split(PROMPT_TOKEN).length - 1
  if (tokenCount !== 1) {
    throw new Error(`frontend prompt must contain exactly one ${PROMPT_TOKEN}`)
  }

  const promptDirectory = resolve(runtimeRoot, 'frontend-agent')
  mkdirSync(promptDirectory, { recursive: true })
  const prompt = template.replace(
    PROMPT_TOKEN,
    JSON.stringify(trustedPromptContext(merchant), null, 2),
  )
  writeFileSync(resolve(promptDirectory, 'PROMPT.md'), prompt, 'utf8')
  return promptDirectory
}
