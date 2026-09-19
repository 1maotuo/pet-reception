import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const require = createRequire(import.meta.url)
const qwenRoot = dirname(require.resolve('qwen-audio-agent/package.json'))

test('opening greeting is claimed once per owner and logical session', async () => {
  const gatewayModule = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'realtime-gateway.mjs',
  )))
  const registry = gatewayModule.createInitialGreetingRegistry({ maxSessions: 4 })

  assert.equal(registry.claim('owner-a', 'session-a'), true)
  assert.equal(registry.claim('owner-a', 'session-a'), false)
  assert.equal(registry.claim('owner-a', 'session-b'), true)
  assert.equal(registry.claim('owner-b', 'session-a'), true)
})

test('opening greeting registry has a bounded retention window', async () => {
  const gatewayModule = await import(pathToFileURL(resolve(
    qwenRoot,
    'server',
    'src',
    'voice',
    'realtime-gateway.mjs',
  )))
  const registry = gatewayModule.createInitialGreetingRegistry({ maxSessions: 2 })

  assert.equal(registry.claim('owner-a', 'old-session'), true)
  assert.equal(registry.claim('owner-a', 'current-session'), true)
  assert.equal(registry.claim('owner-a', 'current-session'), false)
  assert.equal(registry.claim('owner-a', 'new-session'), true)
  assert.equal(registry.claim('owner-a', 'old-session'), true)
})
