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
    throw new Error(`cannot safely apply Qwen M1 patch: ${label}`)
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
      `Qwen M1 patch supports ${SUPPORTED_VERSION}, found ${packageJson.version}`,
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

  patchFile(resolve(packageRoot, 'web', 'dist', 'index.html'), [
    {
      label: 'load preferred audio input bootstrap before the upstream WebUI',
      original: `    <script type="module" crossorigin src="./assets/index-D09JtNnt.js"></script>`,
      replacement: `    <script src="./pet-reception-audio-input.js"></script>\n    <script type="module" crossorigin src="./assets/index-D09JtNnt.js"></script>`,
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
  console.log(`Applied Pet Reception M1 compatibility patch to qwen-audio-agent@${SUPPORTED_VERSION}`)
}
