import { createRequire } from 'node:module'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const SUPPORTED_VERSION = '1.11.0'

const AUDIO_INPUT_BOOTSTRAP = `(() => {
  const mediaDevices = navigator.mediaDevices
  if (!mediaDevices?.getUserMedia || mediaDevices.getUserMedia.__petReceptionPatched) return

  const originalGetUserMedia = mediaDevices.getUserMedia.bind(mediaDevices)
  const normalize = value => String(value || '').trim().toLocaleLowerCase()
  const physicalMatch = (devices, preferredLabel) => {
    const wanted = normalize(preferredLabel)
    const candidates = devices.filter(device => (
      device.kind === 'audioinput'
      && !['default', 'communications'].includes(device.deviceId)
      && normalize(device.label).includes(wanted)
    ))
    const exact = candidates.filter(device => normalize(device.label) === wanted)
    const eligible = exact.length ? exact : candidates
    if (eligible.length > 1) {
      throw new DOMException(
        '配置的麦克风名称匹配到多个物理设备：' + preferredLabel,
        'NotFoundError',
      )
    }
    return eligible[0] || null
  }

  async function configuredAudioInput(requestedAudio) {
    const response = await fetch('/api/health', { cache: 'no-store' })
    if (!response.ok) throw new Error('无法读取麦克风配置')
    const health = await response.json()
    const preferredLabel = String(health.audioInput?.preferredLabel || '').trim()
    if (!preferredLabel) {
      return { preferredLabel, audio: requestedAudio }
    }

    const baseAudio = requestedAudio === true ? {} : { ...requestedAudio }
    let devices = await mediaDevices.enumerateDevices()
    let match = physicalMatch(devices, preferredLabel)
    if (!match && !devices.some(device => device.kind === 'audioinput' && device.label)) {
      const permissionStream = await originalGetUserMedia({ audio: baseAudio })
      permissionStream.getTracks().forEach(track => track.stop())
      devices = await mediaDevices.enumerateDevices()
      match = physicalMatch(devices, preferredLabel)
    }
    if (!match) {
      throw new DOMException(
        '未找到配置的麦克风：' + preferredLabel,
        'NotFoundError',
      )
    }
    return {
      preferredLabel,
      audio: { ...baseAudio, deviceId: { exact: match.deviceId } },
    }
  }

  const patchedGetUserMedia = async constraints => {
    if (!constraints?.audio) return originalGetUserMedia(constraints)
    try {
      const selection = await configuredAudioInput(constraints.audio)
      const stream = await originalGetUserMedia({
        ...constraints,
        audio: selection.audio,
      })
      const track = stream.getAudioTracks()[0]
      window.__petReceptionAudioInput = {
        status: 'selected',
        preferredLabel: selection.preferredLabel || null,
        selectedLabel: track?.label || null,
        selectedDeviceId: track?.getSettings?.().deviceId || null,
      }
      return stream
    } catch (error) {
      window.__petReceptionAudioInput = {
        status: 'failed',
        error: error?.message || String(error),
      }
      throw error
    }
  }
  patchedGetUserMedia.__petReceptionPatched = true
  mediaDevices.getUserMedia = patchedGetUserMedia
})()
`

function replaceOnce(source, original, replacement, label, alreadyApplied = '') {
  if (source.includes(replacement) || (alreadyApplied && source.includes(alreadyApplied))) {
    return source
  }
  const first = source.indexOf(original)
  if (first < 0 || source.indexOf(original, first + original.length) >= 0) {
    throw new Error(`cannot safely apply Pet Reception Qwen compatibility patch: ${label}`)
  }
  return source.replace(original, replacement)
}

function patchFile(filePath, patches) {
  const source = readFileSync(filePath, 'utf8')
  const patched = patches.reduce(
    (current, patch) => replaceOnce(
      current,
      patch.original,
      patch.replacement,
      patch.label,
      patch.alreadyApplied,
    ),
    source,
  )
  if (patched !== source) writeFileSync(filePath, patched, 'utf8')
}

export function applyQwenM1Patch(packageRoot) {
  const packageJson = JSON.parse(readFileSync(resolve(packageRoot, 'package.json'), 'utf8'))
  if (packageJson.version !== SUPPORTED_VERSION) {
    throw new Error(
      `Pet Reception Qwen compatibility patch supports ${SUPPORTED_VERSION}, found ${packageJson.version}`,
    )
  }

  patchFile(resolve(packageRoot, 'server', 'src', 'app', 'gateway-application.mjs'), [
    {
      label: 'gateway application initialGreeting option',
      alreadyApplied: `  initialGreeting = '',`,
      original: `  realtimeProvider = config.audioProvider,\n} = {}) {`,
      replacement: `  realtimeProvider = config.audioProvider,\n  // Pet Reception M1 compatibility hook (pinned qwen-audio-agent@1.11.0).\n  initialGreeting = '',\n} = {}) {`,
    },
    {
      label: 'pass initialGreeting to realtime gateway',
      original: `  defaultRealtimeProvider: realtimeProvider,\n})`,
      replacement: `  defaultRealtimeProvider: realtimeProvider,\n  initialGreeting,\n})`,
    },
    {
      label: 'gateway preferred audio input option',
      original: `  initialGreeting = '',\n} = {}) {`,
      replacement: `  initialGreeting = '',\n  // Pet Reception M1 selects a configured physical microphone without replacing capture.\n  preferredAudioInputLabel = '',\n} = {}) {`,
    },
    {
      label: 'expose preferred audio input to the same-origin WebUI',
      original: `    realtimeInputSampleRate: realtime.inputSampleRate,`,
      replacement: `    realtimeInputSampleRate: realtime.inputSampleRate,\n    audioInput: {\n      selection: String(preferredAudioInputLabel || '').trim()\n        ? 'required-label'\n        : 'browser-default',\n      preferredLabel: String(preferredAudioInputLabel || '').trim() || null,\n    },`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'realtime-gateway.mjs'), [
    {
      label: 'realtime gateway initialGreeting option',
      original: `  defaultRealtimeProvider = config.audioProvider,\n}) {`,
      replacement: `  defaultRealtimeProvider = config.audioProvider,\n  // Pet Reception M1 compatibility hook (pinned qwen-audio-agent@1.11.0).\n  initialGreeting = '',\n}) {`,
    },
    {
      label: 'initial greeting connection state',
      original: `    let descriptor = clientDescriptor()\n    let responseTurnCandidate = null`,
      replacement: `    let descriptor = clientDescriptor()\n    let initialGreetingDelivered = false\n    let responseTurnCandidate = null`,
    },
    {
      label: 'speak initial greeting after realtime ready',
      alreadyApplied: `            initialGreetingDelivered = true\n            createdFrontend.`,
      original: `          send(ws, {\n            type: 'voice.ready',\n            inputSampleRate: createdFrontend.provider.inputSampleRate,\n            provider: createdFrontend.provider.key,\n            providerLabel: createdFrontend.provider.label,\n          })\n          prepareSleepMode()`,
      replacement: `          send(ws, {\n            type: 'voice.ready',\n            inputSampleRate: createdFrontend.provider.inputSampleRate,\n            provider: createdFrontend.provider.key,\n            providerLabel: createdFrontend.provider.label,\n          })\n          if (\n            outputEnabled\n            && !resumedFromSleep\n            && !initialGreetingDelivered\n            && String(initialGreeting || '').trim()\n          ) {\n            initialGreetingDelivered = true\n            createdFrontend.speak(\n              initialGreeting,\n              'initial-greeting',\n              { turnId: null },\n            ).catch(error => connectionLogger.warn('initial_greeting.failed', {\n              error: error.message,\n            }))\n          }\n          prepareSleepMode()`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'realtime-gateway.mjs'), [
    {
      label: 'inject initial greeting into the fresh conversation',
      original: `            createdFrontend.speak(\n              initialGreeting,\n              'initial-greeting',\n              { turnId: null },\n            ).catch(error => connectionLogger.warn('initial_greeting.failed', {`,
      replacement: `            createdFrontend.injectResult(\n              [\n                '[CALL_CONNECTED]',\n                '这是服务端控制的首次接通问候，只传达下一行内容，不添加其他信息。',\n                initialGreeting,\n              ].join('\\n'),\n              'initial-greeting',\n              { turnId: null },\n              { injectContext: true },\n            ).catch(error => connectionLogger.warn('initial_greeting.failed', {`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'realtime-gateway.mjs'), [
    {
      label: 'logical session greeting registry',
      alreadyApplied: 'export function createInitialGreetingRegistry({ maxSessions = 500 } = {}) {',
      original: `export function acceptsPlaybackReceipt({
  outputEnabled,
  active,
  responseKnown,
}) {
  return outputEnabled === true && active === true && responseKnown === true
}

function clientDescriptor(event = {}) {`,
      replacement: `export function acceptsPlaybackReceipt({
  outputEnabled,
  active,
  responseKnown,
}) {
  return outputEnabled === true && active === true && responseKnown === true
}

// Pet Reception M1 de-duplicates the opening greeting by trusted logical call,
// without replacing the upstream WebSocket or provider reconnect mechanisms.
export function createInitialGreetingRegistry({ maxSessions = 500 } = {}) {
  const claimed = new Map()
  const limit = Math.max(1, Number(maxSessions) || 500)
  return {
    claim(ownerId, sessionId) {
      const key = String(ownerId || '') + '\\u0000' + String(sessionId || '')
      if (claimed.has(key)) {
        claimed.delete(key)
        claimed.set(key, true)
        return false
      }
      while (claimed.size >= limit) claimed.delete(claimed.keys().next().value)
      claimed.set(key, true)
      return true
    },
  }
}

function clientDescriptor(event = {}) {`,
    },
    {
      label: 'create a greeting registry for the gateway lifetime',
      alreadyApplied: 'const initialGreetingRegistry = createInitialGreetingRegistry({',
      original: `  const activeVoiceClients = new ActiveVoiceClients()
  const voiceConnections = new Map()

  // A suspension is global, not per owner:`,
      replacement: `  const activeVoiceClients = new ActiveVoiceClients()
  const voiceConnections = new Map()
  const initialGreetingRegistry = createInitialGreetingRegistry({
    maxSessions: config.maxConversationSessions,
  })

  // A suspension is global, not per owner:`,
    },
    {
      label: 'claim the opening greeting by owner and logical session',
      alreadyApplied: '&& initialGreetingRegistry.claim(ownerId, sessionId)',
      original: `            && !initialGreetingDelivered
            && String(initialGreeting || '').trim()
          ) {`,
      replacement: `            && !initialGreetingDelivered
            && String(initialGreeting || '').trim()
            && initialGreetingRegistry.claim(ownerId, sessionId)
          ) {`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'providers', 'dashscope.mjs'), [
    {
      label: 'configured server-side turn detection',
      alreadyApplied: 'function configuredTurnDetection(profile) {',
      original: `function responseModalities(profile) {\n  const capabilities = profile.modelCapabilities`,
      replacement: `function configuredTurnDetection(profile) {\n  const type = String(\n    process.env.PET_RECEPTION_TURN_DETECTION_TYPE || 'smart_turn',\n  ).trim()\n  if (type !== 'server_vad') return profile.sessionDefaults.turnDetection\n\n  const silenceDurationMs = Number(\n    process.env.PET_RECEPTION_SILENCE_DURATION_MS || 1200,\n  )\n  return {\n    type: 'server_vad',\n    silence_duration_ms: silenceDurationMs,\n  }\n}\n\nfunction responseModalities(profile) {\n  const capabilities = profile.modelCapabilities`,
    },
    {
      label: 'use configured turn detection',
      original: `        ? profile.sessionDefaults.turnDetection\n        : null`,
      replacement: `        ? configuredTurnDetection(profile)\n        : null`,
    },
    {
      label: 'configure noisy-room VAD threshold and slow-pause window',
      original: `  const silenceDurationMs = Number(\n    process.env.PET_RECEPTION_SILENCE_DURATION_MS || 1200,\n  )\n  return {\n    type: 'server_vad',\n    silence_duration_ms: silenceDurationMs,\n  }`,
      replacement: `  const silenceDurationMs = Number(\n    process.env.PET_RECEPTION_SILENCE_DURATION_MS || 1800,\n  )\n  const threshold = Number(\n    process.env.PET_RECEPTION_VAD_THRESHOLD || 0.65,\n  )\n  return {\n    type: 'server_vad',\n    threshold,\n    silence_duration_ms: silenceDurationMs,\n  }`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'frontend-tools.mjs'), [
    {
      label: 'server-controlled M1 frontend tool allowlist and descriptions',
      alreadyApplied: 'const PET_RECEPTION_TOOL_DESCRIPTIONS = new Map([',
      original: `export function frontendTools(agentContext = {}) {
  const states = Array.isArray(agentContext.client?.states)
    ? agentContext.client.states
    : []
  return states.includes('sleeping')
    ? [...TOOLS, enterSleepTool]
    : TOOLS
}`,
      replacement: `// Pet Reception M1 exposes only its bounded read-only business workflow.
const PET_RECEPTION_TOOL_DESCRIPTIONS = new Map([
  [SPAWN_THINKING_TOOL_NAME, '查询服务端绑定的当前演示商家公开资料。只用于回答本店名称、地址、电话、时区、服务、时长和价格说明；返回 accepted 只表示查询已受理，不代表预约成功。'],
  [CANCEL_AGENT_TASK_TOOL_NAME, '取消当前语音会话中仍在进行的只读商家资料查询。只能取消系统返回的当前会话工作，不创建或修改预约。'],
  [GET_AGENT_TASK_STATUS_TOOL_NAME, '查询当前语音会话中此前只读商家资料查询的状态或结果。不能查询其他用户或其他商家的工作。'],
])

function configuredToolAllowlist() {
  const configured = String(
    process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST || '',
  ).trim()
  if (!configured) return null
  return new Set(configured.split(',').map(value => value.trim()).filter(Boolean))
}

function applyPetReceptionDescription(tool) {
  const description = PET_RECEPTION_TOOL_DESCRIPTIONS.get(tool.function?.name)
  if (!description) return tool
  return {
    ...tool,
    function: { ...tool.function, description },
  }
}

export function frontendTools(agentContext = {}) {
  const states = Array.isArray(agentContext.client?.states)
    ? agentContext.client.states
    : []
  const tools = states.includes('sleeping')
    ? [...TOOLS, enterSleepTool]
    : TOOLS
  const allowed = configuredToolAllowlist()
  return allowed
    ? tools.filter(tool => allowed.has(tool.function?.name)).map(applyPetReceptionDescription)
    : tools
}`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'frontend-tools.mjs'), [
    {
      label: 'M4 trusted business tool declarations',
      alreadyApplied: `const PET_RECEPTION_BUSINESS_TOOLS = [`,
      original: `export const TOOLS = [`,
      replacement: `const PET_RECEPTION_DRAFT_PROPERTIES = {
  customer_name: { type: 'string', description: '客户姓名；只在用户明确提供时填写。' },
  contact_phone: { type: 'string', description: '联系电话；只在用户明确提供时填写。' },
  pet_name: { type: 'string', description: '宠物名字。' },
  pet_species: { type: 'string', description: '宠物种类，例如犬或猫。' },
  pet_breed: { type: 'string', description: '宠物品种；不确定时不要猜。' },
  pet_age_years: { type: 'number', minimum: 0, maximum: 50, description: '宠物年龄（年）。' },
  pet_care_notes: { type: 'string', description: '护理注意事项，忠实保留用户说法，不作医疗推断。' },
  need_description: { type: 'string', description: '本次服务需求描述。' },
  service_id: { type: 'string', description: '只能使用 get_merchant_info 返回的服务 ID。' },
  preferred_date: { type: 'string', description: '商家时区中的 YYYY-MM-DD 日期；不确定时先询问。' },
  preferred_time_of_day: { type: 'string', enum: ['morning', 'afternoon', 'evening', 'flexible'], description: '上午、下午、晚上或时间灵活。' },
  staff_preference_text: { type: 'string', description: '用户原话中的员工偏好。' },
  staff_id: { type: 'string', description: '只能使用 get_merchant_info 返回的员工 ID。' },
  clear_fields: { type: 'array', items: { type: 'string', enum: ['customer_name', 'contact_phone', 'pet_name', 'pet_species', 'pet_breed', 'pet_age_years', 'pet_care_notes', 'need_description', 'service_id', 'preferred_date', 'preferred_time_of_day', 'staff_preference_text', 'staff_id'] }, uniqueItems: true, description: '仅在用户明确撤销或纠正字段时使用。' },
}

const PET_RECEPTION_BUSINESS_TOOLS = [
  {
    type: 'function',
    function: {
      name: 'get_merchant_info',
      description: '读取服务端绑定的当前商家公开资料、服务、演示价格和可服务员工。商家和会话由网关注入，不接受相关参数。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'update_booking_draft',
      description: '把用户在当前或最近对话中明确提供的预约信息一次批量写入当前会话草稿。不要猜测，不要重复写入未变化信息。此操作不创建预约。',
      parameters: { type: 'object', properties: PET_RECEPTION_DRAFT_PROPERTIES, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_availability',
      description: '按当前草稿中的服务、日期、时段和员工偏好查询真实演示排班。缺少信息时根据返回值一次只追问一个必要问题。此操作不创建预约。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'prepare_booking',
      description: '用户选定 search_availability 返回的候选后，准备短时占位和服务端摘要。只传候选 ID；工具会安排受控摘要播报，播完前不得确认。',
      parameters: { type: 'object', properties: { candidate_id: { type: 'string', description: '只能使用当前查询返回且用户选定的 candidate_id。' } }, required: ['candidate_id'], additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'confirm_booking',
      description: '仅当受控预约摘要已经完整播报，并且用户在摘要播完后的新一轮明确确认时提交。结果 awaiting_payment 不是正式确认成功。',
      parameters: { type: 'object', properties: { preparation_id: { type: 'string', description: '只能使用 prepare_booking 返回的 preparation.id。' } }, required: ['preparation_id'], additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_booking_status',
      description: '查询当前受信会话中已知预约操作的最终状态；提交期间被打断、超时或结果迟到时使用，绝不通过再次 confirm_booking 猜测。',
      parameters: { type: 'object', properties: { operation_id: { type: 'string', description: '只能使用此前工具返回的 operation_id。' } }, required: ['operation_id'], additionalProperties: false },
    },
  },
]

export const TOOLS = [`,
    },
    {
      label: 'M4 business tools participate in the server allowlist',
      alreadyApplied: `? [...PET_RECEPTION_BUSINESS_TOOLS, ...TOOLS, enterSleepTool]`,
      original: `  const tools = states.includes('sleeping')
    ? [...TOOLS, enterSleepTool]
    : TOOLS`,
      replacement: `  const tools = states.includes('sleeping')
    ? [...PET_RECEPTION_BUSINESS_TOOLS, ...TOOLS, enterSleepTool]
    : [...PET_RECEPTION_BUSINESS_TOOLS, ...TOOLS]`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'tools', 'tool-call-handler.mjs'), [
    {
      label: 'runtime enforcement for the M1 frontend tool allowlist',
      alreadyApplied: 'function configuredPetReceptionToolAllowlist() {',
      original: `const SENSITIVE_MEMORY = /(?:pass(?:word)?|secret|api[_ -]?key|access[_ -]?token|credential|验证码|密码|密钥|令牌|\\bsk-[a-z0-9_-]+)/i`,
      replacement: `const SENSITIVE_MEMORY = /(?:pass(?:word)?|secret|api[_ -]?key|access[_ -]?token|credential|验证码|密码|密钥|令牌|\\bsk-[a-z0-9_-]+)/i

function configuredPetReceptionToolAllowlist() {
  const configured = String(
    process.env.PET_RECEPTION_FRONTEND_TOOL_ALLOWLIST || '',
  ).trim()
  if (!configured) return null
  return new Set(configured.split(',').map(value => value.trim()).filter(Boolean))
}`,
    },
    {
      label: 'reject forced calls outside the M1 tool allowlist',
      alreadyApplied: `failure('tool_not_allowed', '当前前台不提供这个工具能力。')`,
      original: `    if (this.isStale(turnId, generation)) {
      await this.closeStaleCall(callId, turnId)
      return
    }

    if (toolName === GET_CURRENT_TIME_TOOL_NAME) {`,
      replacement: `    if (this.isStale(turnId, generation)) {
      await this.closeStaleCall(callId, turnId)
      return
    }

    const allowedTools = configuredPetReceptionToolAllowlist()
    if (allowedTools && !allowedTools.has(toolName)) {
      await this.sendOutput(
        callId,
        failure('tool_not_allowed', '当前前台不提供这个工具能力。'),
        turnId,
      )
      return
    }

    if (toolName === GET_CURRENT_TIME_TOOL_NAME) {`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'tools', 'tool-call-handler.mjs'), [
    {
      label: 'M4 business tool runtime names',
      alreadyApplied: `const PET_RECEPTION_BUSINESS_TOOL_NAMES = new Set([`,
      original: `function mergeInputParts(...groups) {`,
      replacement: `const PET_RECEPTION_BUSINESS_TOOL_NAMES = new Set([
  'get_merchant_info',
  'update_booking_draft',
  'search_availability',
  'prepare_booking',
  'confirm_booking',
  'get_booking_status',
])

function mergeInputParts(...groups) {`,
    },
    {
      label: 'M4 deferred controlled booking summary state',
      alreadyApplied: `batch.presentation = presentation`,
      original: `  async completeDeferredToolResponse(responseId, { failed = false } = {}) {`,
      replacement: `  setDeferredPresentation(responseId, presentation) {
    const batch = this.deferredToolResponses.get(String(responseId || ''))
    if (batch) batch.presentation = presentation
  }

  async completeDeferredToolResponse(responseId, { failed = false } = {}) {`,
    },
    {
      label: 'M4 controlled summary replaces generic tool response',
      alreadyApplied: `if (batch.presentation) {`,
      original: `    this.deferredToolResponses.delete(responseId)
    if (batch.failed || batch.suppressResponse) return
    await this.getFrontend()?.ensureResponse?.({
      turnId: batch.turnId,
      turnGeneration: batch.turnGeneration,
    })`,
      replacement: `    this.deferredToolResponses.delete(responseId)
    if (batch.failed) return
    if (batch.presentation) {
      await this.getFrontend()?.speak?.(
        batch.presentation.speech,
        'booking-summary',
        {
          turnId: batch.turnId,
          turnGeneration: batch.turnGeneration,
          bookingPresentation: batch.presentation.reference,
        },
      )
      return
    }
    if (batch.suppressResponse) return
    await this.getFrontend()?.ensureResponse?.({
      turnId: batch.turnId,
      turnGeneration: batch.turnGeneration,
    })`,
    },
    {
      label: 'M4 business tool handler',
      alreadyApplied: `async handlePetReceptionBusinessTool(`,
      original: `  async handle(event, callContext = {}) {`,
      replacement: `  async handlePetReceptionBusinessTool({
    callId,
    toolName,
    args,
    turnId,
    generation,
    responseId,
  }) {
    const deferred = this.beginDeferredToolResponse(responseId, {
      turnId,
      turnGeneration: generation,
    })
    try {
      const result = await this.coordinator.runBusinessTool(toolName, args, {
        ownerId: this.ownerId,
        sessionId: this.sessionId,
        turnId,
      })
      if (this.isStale(turnId, generation)) {
        await this.sendOutput(callId, {
          status: 'completed_after_interruption',
          operation_id: result.output?.operation_id,
          result: result.output,
          message: '操作已由服务端完成，但原语音轮次已结束；不要自动播报或重复提交。',
        }, turnId, null, { createResponse: false })
        await this.completeDeferredToolResponse(deferred, { failed: true })
        return
      }
      if (deferred) {
        await this.sendOutput(callId, result.output, turnId, null, {
          createResponse: false,
        })
        if (result.presentation) {
          this.setDeferredPresentation(deferred, {
            speech: result.presentation.speech,
            reference: {
              preparationId: result.presentation.preparationId,
              eventId: result.presentation.eventId,
            },
          })
        }
      } else if (result.presentation) {
        await this.sendOutput(callId, result.output, turnId, null, {
          createResponse: false,
        })
        await this.getFrontend()?.speak?.(
          result.presentation.speech,
          'booking-summary',
          {
            turnId,
            turnGeneration: generation,
            bookingPresentation: {
              preparationId: result.presentation.preparationId,
              eventId: result.presentation.eventId,
            },
          },
        )
      } else {
        await this.sendOutput(callId, result.output, turnId)
      }
      await this.completeDeferredToolResponse(deferred)
    } catch (error) {
      const detail = error?.detail && typeof error.detail === 'object'
        ? error.detail
        : null
      const replay = error?.presentation
      await this.sendOutput(callId, failure(
        error?.code || 'business_tool_failed',
        replay || detail?.code === 'confirmation_not_ready'
          ? '预约摘要尚未完整播报，正在从受控入口重新播报。'
          : detail?.code === 'expired'
            ? '本次占位已过期，需要重新查询时间。'
            : '这项预约操作暂时没有成功，请根据返回状态继续处理。',
        {
          retryable: Number(error?.status || 0) >= 500,
          ...(detail ? { detail } : {}),
        },
      ), turnId, null, deferred || replay ? { createResponse: false } : undefined)
      if (replay && deferred) {
        this.setDeferredPresentation(deferred, {
          speech: replay.speech,
          reference: {
            preparationId: replay.preparationId,
            eventId: replay.eventId,
          },
        })
      } else if (replay) {
        await this.getFrontend()?.speak?.(
          replay.speech,
          'booking-summary',
          {
            turnId,
            turnGeneration: generation,
            bookingPresentation: {
              preparationId: replay.preparationId,
              eventId: replay.eventId,
            },
          },
        )
      }
      await this.completeDeferredToolResponse(deferred)
    }
  }

  async handle(event, callContext = {}) {`,
    },
    {
      label: 'route M4 business tools before generic upstream tools',
      alreadyApplied: `await this.handlePetReceptionBusinessTool({`,
      original: `    if (toolName === GET_CURRENT_TIME_TOOL_NAME) {`,
      replacement: `    if (PET_RECEPTION_BUSINESS_TOOL_NAMES.has(toolName)) {
      await this.handlePetReceptionBusinessTool({
        callId,
        toolName,
        args,
        turnId,
        generation,
        responseId: callContext.responseId || event.response_id || '',
      })
      return
    }

    if (toolName === GET_CURRENT_TIME_TOOL_NAME) {`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'tools', 'tool-call-handler.mjs'), [
    {
      label: 'M4 controlled client-only result presentation callback',
      alreadyApplied: `sendClientEvent = () => {}`,
      original: `    requestClientState = () => {},
    inputAssets = null,`,
      replacement: `    requestClientState = () => {},
    sendClientEvent = () => {},
    inputAssets = null,`,
    },
    {
      label: 'store M4 client-only presentation callback',
      alreadyApplied: `this.sendClientEvent = sendClientEvent`,
      original: `    this.requestClientState = requestClientState
    this.inputAssets = inputAssets`,
      replacement: `    this.requestClientState = requestClientState
    this.sendClientEvent = sendClientEvent
    this.inputAssets = inputAssets`,
    },
    {
      label: 'deliver M4 customer page only to the bound browser session',
      alreadyApplied: `id: \`inline_business_\${callId}\``,
      original: `      const result = await this.coordinator.runBusinessTool(toolName, args, {
        ownerId: this.ownerId,
        sessionId: this.sessionId,
        turnId,
      })
      if (this.isStale(turnId, generation)) {`,
      replacement: `      const result = await this.coordinator.runBusinessTool(toolName, args, {
        ownerId: this.ownerId,
        sessionId: this.sessionId,
        turnId,
      })
      if (result.clientPresentation?.content) {
        this.sendClientEvent({
          type: 'timeline.inline',
          item: {
            id: \`inline_business_\${callId}\`,
            turnId,
            ...result.clientPresentation,
          },
        })
      }
      if (this.isStale(turnId, generation)) {`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'tools', 'tool-call-handler.mjs'), [
    {
      label: 'M4 replay an interrupted controlled booking summary',
      alreadyApplied: `const replay = error?.presentation`,
      original: `      const detail = error?.detail && typeof error.detail === 'object'
        ? error.detail
        : null
      await this.sendOutput(callId, failure(
        error?.code || 'business_tool_failed',
        detail?.code === 'confirmation_not_ready'
          ? '预约摘要尚未完整播报，不能确认。'
          : detail?.code === 'expired'
            ? '本次占位已过期，需要重新查询时间。'
            : '这项预约操作暂时没有成功，请根据返回状态继续处理。',
        {
          retryable: Number(error?.status || 0) >= 500,
          ...(detail ? { detail } : {}),
        },
      ), turnId, null, deferred ? { createResponse: false } : undefined)
      await this.completeDeferredToolResponse(deferred)`,
      replacement: `      const detail = error?.detail && typeof error.detail === 'object'
        ? error.detail
        : null
      const replay = error?.presentation
      await this.sendOutput(callId, failure(
        error?.code || 'business_tool_failed',
        replay || detail?.code === 'confirmation_not_ready'
          ? '预约摘要尚未完整播报，正在从受控入口重新播报。'
          : detail?.code === 'expired'
            ? '本次占位已过期，需要重新查询时间。'
            : '这项预约操作暂时没有成功，请根据返回状态继续处理。',
        {
          retryable: Number(error?.status || 0) >= 500,
          ...(detail ? { detail } : {}),
        },
      ), turnId, null, deferred || replay ? { createResponse: false } : undefined)
      if (replay && deferred) {
        this.setDeferredPresentation(deferred, {
          speech: replay.speech,
          reference: {
            preparationId: replay.preparationId,
            eventId: replay.eventId,
          },
        })
      } else if (replay) {
        await this.getFrontend()?.speak?.(
          replay.speech,
          'booking-summary',
          {
            turnId,
            turnGeneration: generation,
            bookingPresentation: {
              preparationId: replay.preparationId,
              eventId: replay.eventId,
            },
          },
        )
      }
      await this.completeDeferredToolResponse(deferred)`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'response-context.mjs'), [
    {
      label: 'carry trusted M4 presentation reference through response correlation',
      alreadyApplied: `  'bookingPresentation',`,
      original: `  'consumesTaskNotification',
]`,
      replacement: `  'consumesTaskNotification',
  'bookingPresentation',
]`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'realtime-gateway.mjs'), [
    {
      label: 'mark M4 summary presented only after complete client playback',
      alreadyApplied: `coordinator.markBookingPresented(`,
      original: `      playbackTurns.delete(id)
      if (context) {
        context.playbackEnded = true`,
      replacement: `      playbackTurns.delete(id)
      if (
        context?.bookingPresentation
        && context.playbackStarted
        && context.responseDone
        && context.transcriptDone
        && !context.presentationReported
      ) {
        context.presentationReported = true
        Promise.resolve(coordinator.markBookingPresented(
          context.bookingPresentation,
          {
            ownerId,
            sessionId,
            turnId: context.turnId || turnId,
            transcript: context.assistantTranscript || '',
          },
        )).then(result => {
          if (result?.status === 'rejected') {
            connectionLogger.warn('booking_summary.presentation_rejected', {
              reason: result.reason,
            })
          }
        }).catch(error => connectionLogger.warn('booking_summary.presentation_failed', {
          error: error.message,
        }))
      }
      if (context) {
        context.playbackEnded = true`,
    },
  ])

  patchFile(resolve(packageRoot, 'server', 'src', 'voice', 'realtime-gateway.mjs'), [
    {
      label: 'bind M4 client-only presentations to the originating websocket',
      alreadyApplied: `sendClientEvent: event => send(ws, event),`,
      original: `      requestClientState: state => {`,
      replacement: `      sendClientEvent: event => send(ws, event),
      requestClientState: state => {`,
    },
  ])

  patchFile(resolve(packageRoot, 'web', 'dist', 'index.html'), [
    {
      label: 'load preferred audio input bootstrap before the upstream WebUI',
      original: `    <script type="module" crossorigin src="./assets/index-D09JtNnt.js"></script>`,
      replacement: `    <script src="./pet-reception-audio-input.js"></script>\n    <script type="module" crossorigin src="./assets/index-D09JtNnt.js"></script>`,
    },
    {
      label: 'use a product-specific browser title',
      alreadyApplied: `<title>Pet Reception</title>`,
      original: `<title>qwen-audio-agent</title>`,
      replacement: `<title>Pet Reception</title>`,
    },
  ])
  patchFile(resolve(packageRoot, 'web', 'dist', 'assets', 'index-D09JtNnt.js'), [
    {
      label: 'M4 browser brand',
      alreadyApplied: `children:["Pet Reception",q.jsx("small",{children:"REALTIME BOOKING · LIVE"})]`,
      original: `children:["qwen-audio-agent",q.jsx("small",{children:"REALTIME VOICE · LIVE"})]`,
      replacement: `children:["Pet Reception",q.jsx("small",{children:"REALTIME BOOKING · LIVE"})]`,
    },
    {
      label: 'M4 browser hero copy',
      alreadyApplied: `q.jsx("h1",{children:"商家专属语音预约前台"})`,
      original: `q.jsx("h1",{children:L("你说，我来调度。")})`,
      replacement: `q.jsx("h1",{children:"商家专属语音预约前台"})`,
    },
    {
      label: 'M4 browser empty-state example',
      alreadyApplied: `q.jsx("span",{children:"“想了解这家店的服务，并预约下周二上午。”"})`,
      original: `q.jsx("span",{children:L("“帮我查一下今天的 AI 新闻，并整理成三点摘要。”")})`,
      replacement: `q.jsx("span",{children:"“想了解这家店的服务，并预约下周二上午。”"})`,
    },
    {
      label: 'M4 assistant transcript label',
      alreadyApplied: `A.companion?Xk(A):"商家前台"`,
      original: `A.companion?Xk(A):"qwen-audio"`,
      replacement: `A.companion?Xk(A):"商家前台"`,
    },
    {
      label: 'M4 browser frontend eyebrow',
      alreadyApplied: `q.jsx("p",{children:"PET RECEPTION"})`,
      original: `q.jsx("p",{children:"VOICE FRONTEND"})`,
      replacement: `q.jsx("p",{children:"PET RECEPTION"})`,
    },
  ])
  writeFileSync(
    resolve(packageRoot, 'web', 'dist', 'pet-reception-audio-input.js'),
    AUDIO_INPUT_BOOTSTRAP,
    'utf8',
  )
}

const require = createRequire(import.meta.url)
const packageRoot = dirname(require.resolve('qwen-audio-agent/package.json'))
const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === fileURLToPath(import.meta.url)) {
  applyQwenM1Patch(packageRoot)
  console.log(`Applied Pet Reception M1-M4 compatibility patch to qwen-audio-agent@${SUPPORTED_VERSION}`)
}
