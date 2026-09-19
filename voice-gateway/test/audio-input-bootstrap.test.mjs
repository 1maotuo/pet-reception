import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'

const require = createRequire(import.meta.url)
const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))

test('selects the configured physical headset microphone by exact device id', async () => {
  const requests = []
  const usbTrack = {
    label: '麦克风 (USB Audio Device)',
    getSettings: () => ({ deviceId: 'usb-physical-id' }),
  }
  const mediaDevices = {
    enumerateDevices: async () => [
      { kind: 'audioinput', deviceId: 'array-id', label: '麦克风阵列 (Senary Audio)' },
      { kind: 'audioinput', deviceId: 'usb-physical-id', label: '麦克风 (USB Audio Device)' },
      { kind: 'audioinput', deviceId: 'default', label: '默认 - 麦克风 (USB Audio Device)' },
    ],
    getUserMedia: async (constraints) => {
      requests.push(constraints)
      return { getAudioTracks: () => [usbTrack] }
    },
  }
  const context = {
    DOMException,
    fetch: async () => ({
      ok: true,
      json: async () => ({ audioInput: { preferredLabel: 'USB Audio Device' } }),
    }),
    navigator: { mediaDevices },
    window: {},
  }
  vm.runInNewContext(
    readFileSync(resolve(qwenRoot, 'web', 'dist', 'pet-reception-audio-input.js'), 'utf8'),
    context,
  )

  await mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true },
  })

  assert.equal(requests.length, 1)
  assert.equal(requests[0].audio.deviceId.exact, 'usb-physical-id')
  assert.equal(requests[0].audio.echoCancellation, true)
  assert.equal(context.window.__petReceptionAudioInput.status, 'selected')
  assert.equal(
    context.window.__petReceptionAudioInput.selectedLabel,
    '麦克风 (USB Audio Device)',
  )
})

test('fails instead of silently falling back when the configured microphone is absent', async () => {
  const requests = []
  const mediaDevices = {
    enumerateDevices: async () => [
      { kind: 'audioinput', deviceId: 'array-id', label: '麦克风阵列 (Senary Audio)' },
    ],
    getUserMedia: async (constraints) => {
      requests.push(constraints)
      return { getAudioTracks: () => [] }
    },
  }
  const context = {
    DOMException,
    fetch: async () => ({
      ok: true,
      json: async () => ({ audioInput: { preferredLabel: 'USB Audio Device' } }),
    }),
    navigator: { mediaDevices },
    window: {},
  }
  vm.runInNewContext(
    readFileSync(resolve(qwenRoot, 'web', 'dist', 'pet-reception-audio-input.js'), 'utf8'),
    context,
  )

  await assert.rejects(
    mediaDevices.getUserMedia({ audio: true }),
    /未找到配置的麦克风：USB Audio Device/,
  )
  assert.equal(requests.length, 0)
  assert.equal(context.window.__petReceptionAudioInput.status, 'failed')
})
