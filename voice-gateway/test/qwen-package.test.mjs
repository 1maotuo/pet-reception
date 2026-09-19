import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import qwenPackage from 'qwen-audio-agent/package.json' with { type: 'json' }

const require = createRequire(import.meta.url)
const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))

test('pins the verified Qwen Audio Agent release and embedding export', async () => {
  assert.equal(qwenPackage.version, '1.11.0')
  assert.equal(qwenPackage.engines.node, '^22.22.2 || ^24.15.0 || >=26.0.0')

  const gatewayModule = await import('qwen-audio-agent/gateway-application')
  assert.equal(typeof gatewayModule.createGatewayApplication, 'function')
})

test('applies the guarded M1 greeting and server VAD compatibility hooks', () => {
  const application = readFileSync(
    resolve(qwenRoot, 'server', 'src', 'app', 'gateway-application.mjs'),
    'utf8',
  )
  const gateway = readFileSync(
    resolve(qwenRoot, 'server', 'src', 'voice', 'realtime-gateway.mjs'),
    'utf8',
  )
  const provider = readFileSync(
    resolve(qwenRoot, 'server', 'src', 'voice', 'providers', 'dashscope.mjs'),
    'utf8',
  )
  const webIndex = readFileSync(resolve(qwenRoot, 'web', 'dist', 'index.html'), 'utf8')
  const audioInputBootstrap = readFileSync(
    resolve(qwenRoot, 'web', 'dist', 'pet-reception-audio-input.js'),
    'utf8',
  )

  assert.match(application, /initialGreeting = ''/)
  assert.match(application, /defaultRealtimeProvider: realtimeProvider,\s+initialGreeting/)
  assert.match(gateway, /createdFrontend\.injectResult\([\s\S]+\[CALL_CONNECTED\][\s\S]+initialGreeting/)
  assert.match(gateway, /\{ injectContext: true \}/)
  assert.match(gateway, /!resumedFromSleep/)
  assert.match(provider, /PET_RECEPTION_TURN_DETECTION_TYPE/)
  assert.match(provider, /PET_RECEPTION_VAD_THRESHOLD/)
  assert.match(provider, /type: 'server_vad'/)
  assert.match(provider, /threshold,/)
  assert.match(provider, /silence_duration_ms: silenceDurationMs/)
  assert.match(application, /preferredAudioInputLabel = ''/)
  assert.match(application, /selection: String\(preferredAudioInputLabel/)
  assert.ok(
    webIndex.indexOf('pet-reception-audio-input.js')
      < webIndex.indexOf('assets/index-D09JtNnt.js'),
  )
  assert.match(audioInputBootstrap, /deviceId: \{ exact: match\.deviceId \}/)
  assert.match(audioInputBootstrap, /未找到配置的麦克风/)
  assert.match(audioInputBootstrap, /__petReceptionAudioInput/)
})

test('builds the Qwen session with the noisy-room server VAD profile', async () => {
  process.env.PET_RECEPTION_TURN_DETECTION_TYPE = 'server_vad'
  process.env.PET_RECEPTION_SILENCE_DURATION_MS = '1800'
  process.env.PET_RECEPTION_VAD_THRESHOLD = '0.65'
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

  assert.deepEqual(session.turn_detection, {
    type: 'server_vad',
    threshold: 0.65,
    silence_duration_ms: 1800,
  })
})
