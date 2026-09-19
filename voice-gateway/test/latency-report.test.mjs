import assert from 'node:assert/strict'
import test from 'node:test'
import { buildLatencyReport } from '../src/latency-report.mjs'

test('summarizes measured tool and voice latency without needing conversation content', () => {
  const log = [
    'INFO gateway business_tool.completed {"toolName":"search_availability","durationMs":400,"status":"ok"}',
    'INFO gateway business_tool.completed {"toolName":"search_availability","durationMs":100,"status":"ok"}',
    'INFO gateway business_tool.failed {"toolName":"search_availability","durationMs":900,"status":"failed"}',
    'INFO gateway voice_turn.first_audio {"sinceInputEndMs":800,"sinceResponseStartMs":300}',
    'INFO gateway voice_turn.first_audio {"sinceInputEndMs":1200,"sinceResponseStartMs":500}',
    'INFO gateway voice_turn.response_done {"sinceInputEndMs":1800,"responseDurationMs":1000}',
    'INFO gateway voice_turn.response_done {"sinceInputEndMs":2300,"responseDurationMs":1100}',
    'INFO gateway business_tool.completed {not-json}',
  ].join('\n')

  const report = buildLatencyReport(log)

  assert.equal(report.status, 'measured')
  assert.deepEqual(report.business_tools.search_availability, {
    samples: 3,
    min_ms: 100,
    p50_ms: 400,
    p95_ms: 900,
    max_ms: 900,
    outcomes: { completed: 2, failed: 1 },
  })
  assert.deepEqual(report.voice_turns.input_end_to_first_audio, {
    samples: 2,
    min_ms: 800,
    p50_ms: 800,
    p95_ms: 1200,
    max_ms: 1200,
  })
  assert.equal(JSON.stringify(report).includes('customer'), false)
})

test('reports no samples instead of inventing latency', () => {
  const report = buildLatencyReport('unrelated log line')
  assert.equal(report.status, 'no_samples')
  assert.equal(report.voice_turns.input_end_to_first_audio, null)
  assert.deepEqual(report.business_tools, {})
})

test('a greeting response duration is still a measured sample', () => {
  const report = buildLatencyReport(
    'INFO gateway voice_turn.response_done {"sinceInputEndMs":null,"responseDurationMs":750}',
  )
  assert.equal(report.status, 'measured')
  assert.equal(report.voice_turns.response_duration.samples, 1)
})
