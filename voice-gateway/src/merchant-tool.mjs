import { randomUUID } from 'node:crypto'

const TRUSTED_ID = /^[A-Za-z0-9._:-]{1,128}$/
const DRAFT_FIELDS = new Set([
  'customer_name', 'contact_phone', 'pet_name', 'pet_species', 'pet_breed',
  'pet_age_years', 'pet_age_months', 'pet_age_text', 'pet_care_notes', 'need_description', 'service_id',
  'preferred_date', 'preferred_time_of_day', 'staff_preference_text', 'staff_id',
])
const STATEFUL_TOOL_NAMES = new Set([
  'update_booking_draft',
  'search_availability',
  'prepare_booking',
  'confirm_booking',
])
const COMPLETED_DEDUPE_TOOL_NAMES = new Set([
  'update_booking_draft',
  'prepare_booking',
  'confirm_booking',
])

export const BUSINESS_TOOL_NAMES = Object.freeze([
  'get_merchant_info',
  'update_booking_draft',
  'search_availability',
  'prepare_booking',
  'confirm_booking',
  'get_booking_status',
])

function requireTrustedId(name, value) {
  const normalized = String(value || '').trim()
  if (!TRUSTED_ID.test(normalized)) {
    throw new Error(`trusted ${name} is missing or invalid`)
  }
  return normalized
}

function requestSignal(externalSignal, timeoutMs) {
  const timeoutSignal = AbortSignal.timeout(timeoutMs)
  return externalSignal
    ? AbortSignal.any([externalSignal, timeoutSignal])
    : timeoutSignal
}

const POSSIBLY_COMMITTED_TRANSPORT_CODES = new Set([
  'ECONNRESET',
  'EPIPE',
  'ETIMEDOUT',
  'UND_ERR_ABORTED',
  'UND_ERR_BODY_TIMEOUT',
  'UND_ERR_HEADERS_TIMEOUT',
  'UND_ERR_SOCKET',
])

function writeOutcomeMayBeUnknown(error) {
  if (['AbortError', 'TimeoutError'].includes(error?.name)) return true
  if (error?.name !== 'TypeError') return false
  const causeCode = String(error?.cause?.code || '').toUpperCase()
  return POSSIBLY_COMMITTED_TRANSPORT_CODES.has(causeCode)
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${stableJson(value[key])}`
    )).join(',')}}`
  }
  return JSON.stringify(value)
}

function boundedMapSet(map, key, value, limit = 500) {
  if (map.has(key)) map.delete(key)
  while (map.size >= limit) map.delete(map.keys().next().value)
  map.set(key, value)
}

function trustedContext(options = {}) {
  return {
    ownerId: requireTrustedId('ownerId', options.ownerId),
    sessionId: requireTrustedId('sessionId', options.sessionId),
    turnId: requireTrustedId('turnId', options.turnId),
    signal: options.signal,
  }
}

function coordinatorContext(options = {}) {
  return {
    ...trustedContext(options),
    invocationId: String(options.invocationId || '').trim(),
    turnGeneration: Number.isInteger(options.turnGeneration)
      ? options.turnGeneration
      : null,
    isActive: typeof options.isActive === 'function' ? options.isActive : () => true,
  }
}

export class StaleBusinessTurnError extends Error {
  constructor() {
    super('business tool turn is no longer active')
    this.name = 'StaleBusinessTurnError'
    this.code = 'stale_business_turn'
  }
}

export class BusinessToolResultUnknownError extends Error {
  constructor(operation, cause) {
    super(`${operation} result is unknown after the request connection ended`)
    this.name = 'BusinessToolResultUnknownError'
    this.operation = operation
    this.code = 'result_unknown'
    this.status = 0
    this.cause = cause
  }
}

function requireActiveTurn(context) {
  if (!context.isActive()) throw new StaleBusinessTurnError()
}

export class BusinessToolHttpError extends Error {
  constructor(operation, status, detail) {
    const code = detail && typeof detail === 'object' ? detail.code : ''
    super(`${operation} failed with HTTP ${status}${code ? ` (${code})` : ''}`)
    this.name = 'BusinessToolHttpError'
    this.operation = operation
    this.status = status
    this.detail = detail
    this.code = code || `http_${status}`
  }
}

export class BookingSummaryPlaybackRequiredError extends Error {
  constructor(presentation) {
    super('booking summary must be completely replayed before confirmation')
    this.name = 'BookingSummaryPlaybackRequiredError'
    this.code = 'confirmation_not_ready'
    this.presentation = presentation
  }
}

export class MerchantToolClient {
  constructor({
    backendBaseUrl,
    merchantId,
    serviceToken,
    fetchImpl = fetch,
    requestTimeoutMs = 5000,
  }) {
    this.backendBaseUrl = String(backendBaseUrl).replace(/\/$/, '')
    this.merchantId = requireTrustedId('merchantId', merchantId)
    this.serviceToken = String(serviceToken || '')
    this.fetch = fetchImpl
    this.requestTimeoutMs = requestTimeoutMs
    if (!this.serviceToken) throw new Error('voice gateway service token is required')
    if (!Number.isInteger(this.requestTimeoutMs) || this.requestTimeoutMs < 1) {
      throw new Error('merchant tool request timeout must be a positive integer')
    }
  }

  async health() {
    try {
      const response = await this.fetch(`${this.backendBaseUrl}/health`, {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(3000),
      })
      return {
        enabled: true,
        ok: response.ok,
        status: response.ok ? 'ready' : 'unavailable',
        code: response.ok ? 'READY' : 'BACKEND_HTTP_ERROR',
      }
    } catch {
      return {
        enabled: true,
        ok: false,
        status: 'unavailable',
        code: 'BACKEND_UNREACHABLE',
      }
    }
  }

  headers(context, extra = {}) {
    const trusted = trustedContext(context)
    return {
      Accept: 'application/json',
      Authorization: `Bearer ${this.serviceToken}`,
      'Content-Type': 'application/json',
      'X-Merchant-ID': this.merchantId,
      'X-Voice-Owner-ID': trusted.ownerId,
      'X-Voice-Session-ID': trusted.sessionId,
      'X-Voice-Turn-ID': trusted.turnId,
      ...extra,
    }
  }

  async request(path, { method = 'GET', body, context, headers = {}, operation }) {
    let response
    try {
      response = await this.fetch(`${this.backendBaseUrl}${path}`, {
        method,
        headers: this.headers(context, headers),
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: requestSignal(context.signal, this.requestTimeoutMs),
      })
    } catch (error) {
      if (method !== 'GET' && writeOutcomeMayBeUnknown(error)) {
        throw new BusinessToolResultUnknownError(operation, error)
      }
      throw error
    }
    let payload
    try {
      payload = await response.json()
    } catch (error) {
      if (response.ok && method !== 'GET') {
        throw new BusinessToolResultUnknownError(operation, error)
      }
      if (response.ok) throw error
      payload = null
    }
    if (!response.ok) {
      throw new BusinessToolHttpError(
        operation,
        response.status,
        payload?.detail ?? payload,
      )
    }
    if (payload === null || typeof payload !== 'object') {
      if (method !== 'GET') {
        throw new BusinessToolResultUnknownError(
          operation,
          new Error('successful write response did not contain a JSON object'),
        )
      }
      throw new Error(`${operation} returned an invalid JSON response`)
    }
    return payload
  }

  getMerchantInfo(context) {
    return this.request('/api/internal/voice/merchant-info', {
      context,
      operation: 'merchant tool request',
    })
  }

  getBookingDraft(context) {
    return this.request('/api/internal/voice/booking-draft', {
      context,
      operation: 'booking draft read',
    })
  }

  startBookingSession(context, previousSessionId) {
    return this.request('/api/internal/voice/booking-sessions/start', {
      method: 'POST',
      context,
      body: { previous_voice_session_id: previousSessionId || null },
      operation: 'booking session start',
    })
  }

  updateBookingDraft(context, body) {
    return this.request('/api/internal/voice/booking-draft', {
      method: 'PATCH', context, body, operation: 'booking draft update',
    })
  }

  searchAvailability(context, body) {
    return this.request('/api/internal/voice/availability-searches', {
      method: 'POST', context, body, operation: 'availability search',
    })
  }

  prepareBooking(context, body) {
    return this.request('/api/internal/voice/booking-preparations', {
      method: 'POST', context, body, operation: 'booking preparation',
    })
  }

  getBookingPreparation(context, preparationId) {
    return this.request(
      `/api/internal/voice/booking-preparations/${encodeURIComponent(preparationId)}`,
      { context, operation: 'booking preparation recovery' },
    )
  }

  markBookingPresented(context, preparationId, draftVersion, eventId) {
    return this.request(
      `/api/internal/voice/booking-preparations/${encodeURIComponent(preparationId)}/presented`,
      {
        method: 'POST',
        context,
        body: { expected_draft_version: draftVersion },
        headers: { 'X-Presentation-Event-ID': eventId },
        operation: 'booking summary presentation',
      },
    )
  }

  confirmBooking(context, preparationId, draftVersion, eventId) {
    return this.request(
      `/api/internal/voice/booking-preparations/${encodeURIComponent(preparationId)}/confirm`,
      {
        method: 'POST',
        context,
        body: { expected_draft_version: draftVersion },
        headers: { 'X-Confirmation-Event-ID': eventId },
        operation: 'booking confirmation',
      },
    )
  }

  getBookingStatus(context, operationId) {
    return this.request(
      `/api/internal/voice/booking-operations/${encodeURIComponent(operationId)}`,
      { context, operation: 'booking status query' },
    )
  }
}

function merchantFacts(result) {
  const merchant = result.merchant
  return {
    status: 'ok',
    type: 'trusted_merchant_public_info',
    merchant: {
      id: merchant.id,
      display_name: merchant.display_name,
      address: merchant.address,
      phone: merchant.phone,
      time_zone: merchant.time_zone,
      is_demo: merchant.is_demo,
      source: merchant.source,
      local_date: result.merchant_local_date,
      local_datetime: result.merchant_local_datetime,
      grooming_policy: merchant.grooming_policy || null,
      business_hours: merchant.business_hours || null,
      staff: merchant.staff || [],
      services: merchant.services.map((service) => ({
        id: service.id,
        name_zh: service.name_zh,
        duration_minutes: service.duration_minutes,
        price_note: service.price_note,
        supported_species: service.supported_species || [],
        included_items: service.included_items || [],
        recommended_for: service.recommended_for || [],
        assessment_required_for: service.assessment_required_for || [],
        not_included: service.not_included || [],
        source: service.source || merchant.source,
      })),
    },
    relative_date_rule: '今天、明天、后天和下周几必须以 merchant.local_date 与 merchant.time_zone 为准。',
    recommendation_rule: '只能根据受信目录的适用宠物、包含项目、推荐条件、需评估条件和不包含项目回答；缺失内容必须说未知。',
    data_boundary: '资料来自受信演示目录；价格是演示参考，不是正式报价，需评估项目不是已承诺可做。',
    availability_checked: false,
    booking_created: false,
  }
}

function merchantSpeech(result) {
  const merchant = result.merchant
  const services = merchant.services
    .map((service) => `${service.name_zh}，${service.price_note}`)
    .join('；')
  return `${merchant.display_name}位于${merchant.address}。当前演示服务包括：${services}。这些是演示参考资料，实际价格和适用性需要由商家确认。`
}

function merchantInline(result) {
  const merchant = result.merchant
  const serviceLines = merchant.services
    .map((service) => `- ${service.name_zh}：约 ${service.duration_minutes} 分钟；${service.price_note}`)
    .join('\n')
  const businessHours = merchant.business_hours?.note
    ? `- 营业时间：${merchant.business_hours.note}`
    : '- 营业时间：公开资料未提供'
  const staffLines = (merchant.staff || [])
    .map((staff) => `- ${staff.display_name}：${staff.public_intro || '公开介绍未提供'}`)
    .join('\n')
  return [
    `## ${merchant.display_name}`, '', `- 地址：${merchant.address}`,
    `- 电话：${merchant.phone}`, `- 时区：${merchant.time_zone}`,
    `- 商家当地日期：${result.merchant_local_date}`,
    businessHours,
    `- 数据来源：${merchant.source}`, '- 资料边界：演示资料和参考价格，不是正式报价',
    '', serviceLines,
    ...(staffLines ? ['', '### 服务人员', staffLines] : []),
  ].join('\n')
}

function publicDraft(result) {
  const { merchant_id, owner_id, voice_session_id, ...draft } = result.draft
  return {
    status: 'ok', draft,
    changed_fields: result.changed_fields || [],
    missing_for_availability: result.missing_for_availability,
    missing_for_booking: result.missing_for_booking,
  }
}

function publicAvailability(result) {
  const { draft_id, ...search } = result.search
  const nextAction = search.status === 'no_availability'
    ? {
        type: 'select_alternative_date',
        instruction: '用户选择替代日期后，必须先调用 update_booking_draft 写入 preferred_date，并设置 search_availability_after_update=true；成功返回具体 candidates 前不得声称已有具体时段。',
      }
    : search.candidates?.length
      ? {
          type: 'select_returned_candidate',
          instruction: '只能从本次 candidates 中选择；向客户播报当地时间，不朗读 candidate_id。',
        }
      : {
          type: 'stop_and_explain',
          instruction: '当前没有可选择的具体候选，不得调用 prepare_booking。',
        }
  return { status: 'ok', search, next_action: nextAction }
}

function publicOperation(result) {
  const safe = {
    operation_id: result.operation_id,
    status: result.status,
    preparation: result.preparation,
    booking: result.booking,
  }
  if (result.customer_credential) {
    safe.customer_action = {
      payment_required: result.status === 'awaiting_payment',
      booking_reference: result.customer_credential.booking_reference,
      customer_page_available: true,
      security_note: '访问令牌和链接只交给受控界面，不提供给模型，也不要朗读。',
    }
  }
  return safe
}

function customerPagePresentation(result) {
  const credential = result.customer_credential
  if (!credential?.credential_url) return null
  const statusLine = result.status === 'confirmed'
    ? `预约已正式确认${result.booking?.confirmation_code ? `；确认码：${result.booking.confirmation_code}` : ''}。`
    : result.status === 'payment_expired'
      ? '付款占位已过期；没有形成正式预约，请重新查询可用时间。'
      : '当前为等待本地模拟支付；完成前不是正式确认。'
  return {
    title: '客户预约页面',
    format: 'markdown',
    content: [
      `预约参考号：${credential.booking_reference}`,
      '',
      `[打开客户预约页面](${credential.credential_url})`,
      '',
      `${statusLine}请勿转发此专属链接。`,
    ].join('\n'),
  }
}

function draftPatch(args, expectedVersion) {
  const patch = { expected_version: expectedVersion }
  for (const name of DRAFT_FIELDS) {
    if (Object.hasOwn(args, name)) patch[name] = args[name]
  }
  if (Object.hasOwn(args, 'clear_fields')) {
    patch.clear_fields = Array.isArray(args.clear_fields)
      ? args.clear_fields.filter((name) => DRAFT_FIELDS.has(name))
      : []
  }
  if (Object.keys(patch).length === 1) {
    throw new Error('update_booking_draft requires at least one supported change')
  }
  return patch
}

function summarySpeech(summary) {
  const date = new Intl.DateTimeFormat('zh-CN', {
    timeZone: summary.merchant_time_zone,
    year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
  }).format(new Date(summary.starts_at))
  const time = new Intl.DateTimeFormat('zh-CN', {
    timeZone: summary.merchant_time_zone,
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(summary.starts_at))
  const notes = [summary.pet_care_notes, summary.need_description].filter(Boolean).join('；')
  const age = summary.pet_age_text ? `，年龄${summary.pet_age_text}` : ''
  return [
    '请您确认预约摘要。',
    `${summary.customer_name}，为${summary.pet_species}${summary.pet_name}${age}预约${summary.service_name_zh}，`,
    `${date}${time}，由${summary.staff_display_name}服务。`,
    `联系电话是${summary.contact_phone}。`,
    notes ? `备注：${notes}。` : '',
    '如果以上信息正确，请在我说完后明确确认；现在还没有创建正式预约。',
  ].filter(Boolean).join('')
}

const SPOKEN_DIGITS = Object.freeze({
  零: '0', 〇: '0', 一: '1', 二: '2', 两: '2', 三: '3', 四: '4',
  五: '5', 六: '6', 七: '7', 八: '8', 九: '9',
})

function replaceSpokenDigits(value) {
  return String(value || '').replace(/[零〇一二两三四五六七八九]/g, (digit) => SPOKEN_DIGITS[digit])
}

function normalizedTranscript(value) {
  return replaceSpokenDigits(value)
    .toLocaleLowerCase()
    .replace(/[\s，。！？、：；,.!?;:'"“”‘’（）()\-+]/g, '')
}

function digitsOnly(value) {
  return replaceSpokenDigits(value).replace(/\D/g, '')
}

function parseChineseInteger(value) {
  const source = String(value || '')
  if (/^\d+$/.test(source)) return Number(source)
  if (/^[零〇一二两三四五六七八九]+$/.test(source)) {
    return Number(replaceSpokenDigits(source))
  }
  let total = 0
  let current = 0
  for (const character of source) {
    if (Object.hasOwn(SPOKEN_DIGITS, character)) current = Number(SPOKEN_DIGITS[character])
    else if (character === '十') { total += (current || 1) * 10; current = 0 }
    else return Number.NaN
  }
  return total + current
}

function periodAdjustedHour(period, rawHour) {
  let hour = rawHour
  if (period === '凌晨' && hour === 12) hour = 0
  if (period === '上午' && hour === 12) hour = 0
  if (['下午', '晚上', '中午'].includes(period) && hour < 12) hour += 12
  return hour
}

function timeMentionRole(source, index) {
  const before = source.slice(Math.max(0, index - 10), index).replace(/\s/g, '')
  return /(?:结束时间?|截止时间?|截至|到|至)$/.test(before) ? 'end' : 'start'
}

function mentionedTimes(value) {
  const source = String(value || '')
  const mentions = []
  const colon = /(上午|下午|晚上|中午|凌晨)?\s*([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)/g
  for (const match of source.matchAll(colon)) {
    const hour = periodAdjustedHour(match[1] || '', Number(match[2]))
    if (hour > 23) continue
    mentions.push({
      index: match.index,
      role: timeMentionRole(source, match.index),
      value: `${String(hour).padStart(2, '0')}:${match[3]}`,
    })
  }
  const spoken = /(上午|下午|晚上|中午|凌晨)?([0-9]{1,2}|[零〇一二两三四五六七八九十]+)(?:点|时)(半|([0-9]{1,2}|[零〇一二两三四五六七八九十]+)分?)?/g
  for (const match of source.matchAll(spoken)) {
    let hour = parseChineseInteger(match[2])
    const minute = match[3] === '半' ? 30 : (match[4] ? parseChineseInteger(match[4]) : 0)
    const period = match[1] || ''
    if (!Number.isInteger(hour) || !Number.isInteger(minute) || minute > 59) continue
    hour = periodAdjustedHour(period, hour)
    if (hour <= 23) {
      mentions.push({
        index: match.index,
        role: timeMentionRole(source, match.index),
        value: `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`,
      })
    }
  }
  return mentions.sort((left, right) => left.index - right.index)
}

function containsTimeCorrection(value) {
  return /(?:不是|不对|改成|改为|应该是|而是).{0,12}(?:[0-2]?\d[:：][0-5]\d|[0-9零〇一二两三四五六七八九十]{1,3}(?:点|时))/.test(
    String(value || ''),
  )
}

function mentionedPhones(value) {
  const phones = new Set()
  const pattern = /(?:联系电话|联系号码|电话号码|电话)(?:是|为|号码是|号码为|[:：])?\s*([+零〇一二两三四五六七八九0-9\s().-]{7,40})/g
  for (const match of String(value || '').matchAll(pattern)) {
    const digits = digitsOnly(match[1])
    if (digits.length >= 7) phones.add(digits)
  }
  return phones
}

function chineseNumber(value) {
  if (value < 10) return '零一二三四五六七八九'[value]
  if (value === 10) return '十'
  if (value < 20) return `十${chineseNumber(value - 10)}`
  const tens = Math.floor(value / 10)
  const ones = value % 10
  return `${chineseNumber(tens)}十${ones ? chineseNumber(ones) : ''}`
}

function summaryLocalParts(summary) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: summary.merchant_time_zone,
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(summary.starts_at)).map((part) => [part.type, part.value]))
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour),
    minute: Number(parts.minute),
  }
}

function includesAny(value, variants) {
  return variants.some((variant) => value.includes(normalizedTranscript(variant)))
}

export function presentationTranscriptMatches(summary, transcript) {
  const normalized = normalizedTranscript(transcript)
  if (!normalized) return false
  const required = [
    summary.customer_name, summary.pet_name, summary.pet_species,
    summary.service_name_zh, summary.staff_display_name,
    summary.pet_age_text,
    summary.pet_care_notes, summary.need_description,
  ].map(normalizedTranscript).filter(Boolean)
  if (!required.every((value) => normalized.includes(value))) return false

  const { year, month, day, hour, minute } = summaryLocalParts(summary)
  const chineseYear = String(year).split('').map((digit) => chineseNumber(Number(digit))).join('')
  const dateMatches = includesAny(normalized, [
    `${year}年${month}月${day}日`,
    `${chineseYear}年${chineseNumber(month)}月${chineseNumber(day)}日`,
  ])
  const expectedTime = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  const mentionedStartTimes = new Set(
    mentionedTimes(transcript)
      .filter((mention) => mention.role === 'start')
      .map((mention) => mention.value),
  )
  const timeMatches = !containsTimeCorrection(transcript)
    && mentionedStartTimes.size === 1
    && mentionedStartTimes.has(expectedTime)
  const expectedPhone = digitsOnly(summary.contact_phone)
  const phoneMatches = expectedPhone.length >= 7 && mentionedPhones(transcript).has(expectedPhone)

  return dateMatches
    && timeMatches
    && phoneMatches
    && (normalized.includes('确认') || normalized.includes('预约'))
}

export class MerchantCoordinator {
  constructor({ toolClient }) {
    this.toolClient = toolClient
    this.inflightCalls = new Map()
    this.completedMutations = new Map()
    this.sessionQueues = new Map()
    this.presentations = new Map()
    this.presentationWrites = new Map()
    this.operations = new Map()
    this.completedSessions = new Map()
    this.currentAvailability = new Map()
  }

  async run(_input, options = {}) {
    const result = await this.toolClient.getMerchantInfo(trustedContext(options))
    const speech = merchantSpeech(result)
    return {
      content: JSON.stringify(merchantFacts(result), null, 2),
      metadata: {
        presentation: {
          speech,
          inline: { title: '演示商家资料', format: 'markdown', content: merchantInline(result) },
        },
        merchantId: result.merchant.id,
        source: result.merchant.source,
      },
    }
  }

  async runBusinessTool(toolName, args = {}, options = {}) {
    if (!BUSINESS_TOOL_NAMES.includes(toolName)) {
      throw new Error(`unsupported pet reception business tool: ${toolName}`)
    }
    const context = coordinatorContext(options)
    const sessionKey = `${context.ownerId}\u0000${context.sessionId}`
    const completed = this.completedSessions.get(sessionKey)
    if (completed && ['update_booking_draft', 'search_availability', 'prepare_booking'].includes(toolName)) {
      return {
        output: {
          ...completed,
          booking_status: completed.status,
          status: 'already_submitted',
        },
      }
    }
    const invocationKey = context.invocationId
      ? [context.ownerId, context.sessionId, context.invocationId, toolName].join('\u0000')
      : ''
    if (invocationKey && this.completedMutations.has(invocationKey)) {
      return this.completedMutations.get(invocationKey)
    }
    const inflightKey = invocationKey || [
      context.ownerId,
      context.sessionId,
      context.turnId,
      toolName,
      stableJson(args),
    ].join('\u0000')
    if (this.inflightCalls.has(inflightKey)) return this.inflightCalls.get(inflightKey)

    const execute = () => {
      if (STATEFUL_TOOL_NAMES.has(toolName)) requireActiveTurn(context)
      return this.executeBusinessTool(toolName, args, context)
    }
    const pending = STATEFUL_TOOL_NAMES.has(toolName)
      ? this.enqueueSessionOperation(context, execute)
      : execute()
    boundedMapSet(this.inflightCalls, inflightKey, pending)
    try {
      const result = await pending
      if (
        invocationKey
        && COMPLETED_DEDUPE_TOOL_NAMES.has(toolName)
      ) {
        boundedMapSet(this.completedMutations, invocationKey, result)
      }
      return result
    } catch (error) {
      if (error?.code === 'booking_already_submitted' && error?.detail?.status === 'confirmed') {
        boundedMapSet(this.completedSessions, sessionKey, error.detail)
      }
      throw error
    } finally {
      if (this.inflightCalls.get(inflightKey) === pending) {
        this.inflightCalls.delete(inflightKey)
      }
    }
  }

  enqueueSessionOperation(context, execute) {
    const key = `${context.ownerId}\u0000${context.sessionId}`
    const previous = this.sessionQueues.get(key) || Promise.resolve()
    const running = previous.catch(() => {}).then(execute)
    const tail = running.catch(() => {})
    this.sessionQueues.set(key, tail)
    tail.finally(() => {
      if (this.sessionQueues.get(key) === tail) this.sessionQueues.delete(key)
    })
    return running
  }

  async executeBusinessTool(toolName, args, context) {
    const sessionKey = `${context.ownerId}\u0000${context.sessionId}`
    if (toolName === 'get_merchant_info') {
      return { output: merchantFacts(await this.toolClient.getMerchantInfo(context)) }
    }
    if (toolName === 'update_booking_draft') {
      const current = await this.toolClient.getBookingDraft(context)
      requireActiveTurn(context)
      const updated = await this.toolClient.updateBookingDraft(
        context,
        draftPatch(args, current.draft.version),
      )
      const output = publicDraft(updated)
      if ((updated.changed_fields || []).some((field) => (
        [
          'service_id',
          'preferred_date',
          'preferred_time_of_day',
          'staff_preference_text',
          'staff_id',
        ].includes(field)
      ))) {
        boundedMapSet(this.currentAvailability, sessionKey, {
          status: 'needs_search',
          candidates: [],
          preferred_date: updated.draft.preferred_date,
        })
      }
      if (args.search_availability_after_update === true) {
        if (updated.missing_for_availability?.length) {
          output.follow_up = {
            status: 'needs_input',
            missing_fields: updated.missing_for_availability,
          }
        } else {
          requireActiveTurn(context)
          try {
            const availability = await this.toolClient.searchAvailability(context, {
              expected_availability_revision: updated.draft.availability_revision,
            })
            const publicResult = publicAvailability(availability)
            output.availability = publicResult.search
            output.next_action = publicResult.next_action
            boundedMapSet(this.currentAvailability, sessionKey, publicResult.search)
          } catch (error) {
            if (error?.code === 'stale_business_turn') throw error
            output.follow_up = {
              status: 'availability_failed',
              error_code: error?.code || 'availability_query_failed',
              retryable: error?.code !== 'booking_already_submitted',
              message: '预约资料已保存，但排班查询没有成功。',
              ...(error?.detail ? { detail: error.detail } : {}),
            }
          }
        }
      }
      return { output }
    }
    if (toolName === 'search_availability') {
      const current = await this.toolClient.getBookingDraft(context)
      requireActiveTurn(context)
      const result = await this.toolClient.searchAvailability(context, {
        expected_availability_revision: current.draft.availability_revision,
      })
      const output = publicAvailability(result)
      boundedMapSet(this.currentAvailability, sessionKey, output.search)
      return { output }
    }
    if (toolName === 'prepare_booking') {
      const latest = this.currentAvailability.get(sessionKey)
      const candidateId = String(args.candidate_id || '')
      if (latest) {
        const matchingCandidate = (latest.candidates || []).find(
          (candidate) => String(candidate.id) === candidateId,
        )
        if (!matchingCandidate) {
          const hasCandidates = Boolean(latest.candidates?.length)
          return {
            output: {
              status: 'needs_availability_selection',
              error: true,
              error_code: hasCandidates
                ? 'candidate_not_in_current_results'
                : 'specific_availability_not_queried',
              user_message: hasCandidates
                ? '这个时间不是当前排班结果中的可选时段，请从刚刚返回的具体时间中重新选择。'
                : '当前还没有查询到所选日期的具体可用时段，请先更新日期并重新查询排班。',
              retryable: false,
              recovery: {
                action: hasCandidates
                  ? 'ask_user_to_select_current_candidate'
                  : 'update_date_and_search_availability',
              },
            },
          }
        }
      }
      const current = await this.toolClient.getBookingDraft(context)
      requireActiveTurn(context)
      const result = await this.toolClient.prepareBooking(context, {
        expected_version: current.draft.version,
        candidate_id: candidateId,
      })
      const preparation = result.preparation
      const presentation = {
        ownerId: context.ownerId,
        sessionId: context.sessionId,
        preparationId: preparation.id,
        operationId: result.operation_id,
        expectedDraftVersion: preparation.draft_version,
        eventId: `presentation_${randomUUID()}`,
        confirmationEventId: `confirmation_${randomUUID()}`,
        summaryDigest: preparation.summary_digest,
        summary: preparation.summary,
        speech: summarySpeech(preparation.summary),
      }
      boundedMapSet(this.presentations, preparation.id, presentation)
      this.presentationWrites.delete(preparation.id)
      boundedMapSet(this.operations, result.operation_id, {
        ownerId: context.ownerId, sessionId: context.sessionId,
      })
      return { output: publicOperation(result), presentation }
    }
    if (toolName === 'confirm_booking') {
      const preparationId = String(args.preparation_id || '')
      let presentation = this.presentations.get(preparationId)
      if (
        !presentation
        || presentation.ownerId !== context.ownerId
        || presentation.sessionId !== context.sessionId
      ) {
        const recovered = await this.toolClient.getBookingPreparation(context, preparationId)
        const recoveredPreparation = recovered?.preparation
        if (!recoveredPreparation || String(recoveredPreparation.id) !== preparationId) {
          throw new Error('booking preparation recovery returned an invalid record')
        }
        boundedMapSet(this.operations, recovered.operation_id, {
          ownerId: context.ownerId, sessionId: context.sessionId,
        })
        if (
          ['awaiting_payment', 'confirmed', 'payment_expired'].includes(recovered.status)
          && recovered.booking
        ) {
          return {
            output: publicOperation(recovered),
            committed: true,
            clientPresentation: customerPagePresentation(recovered),
          }
        }
        if (['expired', 'cancelled'].includes(recovered.status)) {
          return { output: publicOperation(recovered), committed: false }
        }
        if (!['prepared', 'presented'].includes(recovered.status)) {
          throw new Error(`booking preparation cannot be recovered from ${recovered.status}`)
        }
        const presentationEventId = recovered.status === 'presented'
          ? recoveredPreparation.presentation_id
          : `presentation_${randomUUID()}`
        if (!presentationEventId) {
          throw new Error('presented booking preparation has no presentation event')
        }
        presentation = {
          ownerId: context.ownerId,
          sessionId: context.sessionId,
          preparationId,
          operationId: recovered.operation_id,
          expectedDraftVersion: recoveredPreparation.draft_version,
          eventId: presentationEventId,
          confirmationEventId: `confirmation_${randomUUID()}`,
          summaryDigest: recoveredPreparation.summary_digest,
          summary: recoveredPreparation.summary,
          speech: summarySpeech(recoveredPreparation.summary),
        }
        boundedMapSet(this.presentations, preparationId, presentation)
        this.presentationWrites.delete(preparationId)
      }
      if (!this.presentationWrites.has(preparationId)) {
        throw new BookingSummaryPlaybackRequiredError(presentation)
      }
      try {
        await this.presentationWrites.get(preparationId)
      } catch {
        this.presentationWrites.delete(preparationId)
        throw new BookingSummaryPlaybackRequiredError(presentation)
      }
      requireActiveTurn(context)
      let result
      try {
        result = await this.toolClient.confirmBooking(
          context,
          preparationId,
          presentation.expectedDraftVersion,
          presentation.confirmationEventId,
        )
      } catch (error) {
        if (error?.code !== 'result_unknown') throw error
        try {
          result = await this.toolClient.getBookingStatus(context, presentation.operationId)
          if (
            !['awaiting_payment', 'confirmed', 'payment_expired'].includes(result.status)
            || !result.booking
          ) {
            error.operationId = presentation.operationId
            error.observedStatus = result.status
            error.detail = {
              code: 'result_unknown',
              operation_id: presentation.operationId,
              observed_status: result.status,
              recovery: 'query_same_operation',
            }
            throw error
          }
        } catch (statusError) {
          if (statusError === error) throw error
          error.operationId = presentation.operationId
          error.detail = {
            code: 'result_unknown',
            operation_id: presentation.operationId,
            recovery: 'query_same_operation',
          }
          throw error
        }
      }
      boundedMapSet(this.operations, result.operation_id, {
        ownerId: context.ownerId, sessionId: context.sessionId,
      })
      return {
        output: publicOperation(result),
        committed: true,
        clientPresentation: customerPagePresentation(result),
      }
    }
    const operationId = String(args.operation_id || '')
    const result = await this.toolClient.getBookingStatus(context, operationId)
    boundedMapSet(this.operations, result.operation_id || operationId, {
      ownerId: context.ownerId, sessionId: context.sessionId,
    })
    return {
      output: publicOperation(result),
      clientPresentation: customerPagePresentation(result),
    }
  }

  async markBookingPresented(reference = {}, options = {}) {
    const preparationId = String(reference.preparationId || '')
    const presentation = this.presentations.get(preparationId)
    const context = trustedContext(options)
    if (
      !presentation
      || presentation.eventId !== reference.eventId
      || presentation.ownerId !== context.ownerId
      || presentation.sessionId !== context.sessionId
    ) {
      return { status: 'rejected', reason: 'untrusted_presentation' }
    }
    if (!presentationTranscriptMatches(presentation.summary, options.transcript)) {
      return { status: 'rejected', reason: 'summary_transcript_incomplete' }
    }
    if (this.presentationWrites.has(preparationId)) {
      return this.presentationWrites.get(preparationId)
    }
    let pending
    pending = this.toolClient.markBookingPresented(
      context,
      preparationId,
      presentation.expectedDraftVersion,
      presentation.eventId,
    ).then(publicOperation).catch((error) => {
      if (this.presentationWrites.get(preparationId) === pending) {
        this.presentationWrites.delete(preparationId)
      }
      throw error
    })
    boundedMapSet(this.presentationWrites, preparationId, pending)
    return pending
  }
}

export class MerchantToolAgent {
  constructor({ toolClient, coordinator }) {
    this.toolClient = toolClient
    this.coordinator = coordinator
    this.enabled = true
    this.protocol = 'pet-reception-http'
    this.label = 'Pet Reception trusted business HTTP tools'
  }

  describe() {
    return {
      enabled: true, protocol: this.protocol, kind: 'deterministic-http-tool',
      label: this.label, status: 'configured', capabilities: { backendUi: false },
    }
  }

  health() { return this.toolClient.health() }
  status() { return { enabled: true, ok: true, status: 'configured', code: 'CONFIGURED' } }
  runCoordinator(input, options = {}) { return this.coordinator.run(input, options) }
  respondPermission() { return Promise.reject(new Error('business tools do not request permissions')) }
  canRecoverDelegatedWork() { return false }
  uiUrl() { return Promise.resolve(null) }
  close() { return Promise.resolve() }
}

export function createMerchantIntegration(config) {
  const toolClient = new MerchantToolClient(config)
  const coordinator = new MerchantCoordinator({ toolClient })
  const agent = new MerchantToolAgent({ toolClient, coordinator })
  return { agent, coordinator, toolClient }
}
