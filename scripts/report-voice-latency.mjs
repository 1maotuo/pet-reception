import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { buildLatencyReport } from '../voice-gateway/src/latency-report.mjs'

const logPath = resolve(process.argv[2] || 'logs/voice-current.stdout.log')

try {
  const report = buildLatencyReport(await readFile(logPath, 'utf8'))
  console.log(JSON.stringify({ log_path: logPath, ...report }, null, 2))
} catch (error) {
  console.error(JSON.stringify({
    log_path: logPath,
    status: 'unavailable',
    error: error?.code || 'read_failed',
  }, null, 2))
  process.exitCode = 1
}
