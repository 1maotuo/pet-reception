function nearestRank(values, percentile) {
  if (!values.length) return null
  const ordered = [...values].sort((left, right) => left - right)
  const rank = Math.max(1, Math.ceil(percentile * ordered.length))
  return ordered[rank - 1]
}

function summary(values) {
  if (!values.length) return null
  return {
    samples: values.length,
    min_ms: Math.min(...values),
    p50_ms: nearestRank(values, 0.5),
    p95_ms: nearestRank(values, 0.95),
    max_ms: Math.max(...values),
  }
}

function eventFromLine(line) {
  const match = line.match(/\b(business_tool\.(?:completed|failed|ignored)|voice_turn\.(?:first_audio|response_done))\s+(\{.*\})\s*$/)
  if (!match) return null
  try {
    return { name: match[1], payload: JSON.parse(match[2]) }
  } catch {
    return null
  }
}

export function buildLatencyReport(logText) {
  const tools = new Map()
  const inputToFirstAudio = []
  const inputToResponseDone = []
  const responseDurations = []

  for (const line of String(logText || '').split(/\r?\n/)) {
    const event = eventFromLine(line)
    if (!event) continue
    const { name, payload } = event
    if (name.startsWith('business_tool.')) {
      if (!Number.isFinite(payload.durationMs) || !payload.toolName) continue
      const key = String(payload.toolName)
      const record = tools.get(key) || { durations: [], outcomes: {} }
      record.durations.push(payload.durationMs)
      const outcome = name.slice('business_tool.'.length)
      record.outcomes[outcome] = (record.outcomes[outcome] || 0) + 1
      tools.set(key, record)
      continue
    }
    if (name === 'voice_turn.first_audio' && Number.isFinite(payload.sinceInputEndMs)) {
      inputToFirstAudio.push(payload.sinceInputEndMs)
    }
    if (name === 'voice_turn.response_done') {
      if (Number.isFinite(payload.sinceInputEndMs)) {
        inputToResponseDone.push(payload.sinceInputEndMs)
      }
      if (Number.isFinite(payload.responseDurationMs)) {
        responseDurations.push(payload.responseDurationMs)
      }
    }
  }

  return {
    status: tools.size || inputToFirstAudio.length || inputToResponseDone.length || responseDurations.length
      ? 'measured'
      : 'no_samples',
    generated_from: 'privacy-safe gateway timing events',
    voice_turns: {
      input_end_to_first_audio: summary(inputToFirstAudio),
      input_end_to_response_done: summary(inputToResponseDone),
      response_duration: summary(responseDurations),
    },
    business_tools: Object.fromEntries(
      [...tools.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([name, record]) => [name, {
          ...summary(record.durations),
          outcomes: record.outcomes,
        }]),
    ),
  }
}
