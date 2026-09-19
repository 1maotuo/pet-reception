const TRUSTED_ID = /^[A-Za-z0-9._:-]{1,128}$/

function requireTrustedId(name, value) {
  const normalized = String(value || '').trim()
  if (!TRUSTED_ID.test(normalized)) {
    throw new Error(`trusted ${name} is missing or invalid`)
  }
  return normalized
}

function responseError(response, operation) {
  return new Error(`${operation} failed with HTTP ${response.status}`)
}

function requestSignal(externalSignal, timeoutMs) {
  const timeoutSignal = AbortSignal.timeout(timeoutMs)
  return externalSignal
    ? AbortSignal.any([externalSignal, timeoutSignal])
    : timeoutSignal
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

  async getMerchantInfo({ ownerId, sessionId, turnId, signal }) {
    const trustedOwnerId = requireTrustedId('ownerId', ownerId)
    const trustedSessionId = requireTrustedId('sessionId', sessionId)
    const trustedTurnId = requireTrustedId('turnId', turnId)
    const response = await this.fetch(
      `${this.backendBaseUrl}/api/internal/voice/merchant-info`,
      {
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${this.serviceToken}`,
          'X-Merchant-ID': this.merchantId,
          'X-Voice-Owner-ID': trustedOwnerId,
          'X-Voice-Session-ID': trustedSessionId,
          'X-Voice-Turn-ID': trustedTurnId,
        },
        signal: requestSignal(signal, this.requestTimeoutMs),
      },
    )
    if (!response.ok) throw responseError(response, 'merchant tool request')
    return response.json()
  }
}

function merchantSpeech(result) {
  const merchant = result.merchant
  const services = merchant.services
    .map((service) => `${service.name_zh}，${service.price_note}`)
    .join('；')
  return `${merchant.display_name}位于${merchant.address}，当地时区是${merchant.time_zone}。当前演示服务包括：${services}。这些资料来自明确标注的演示目录，尚未查询正式预约空档。公开目录只列出服务名称和价格说明，未说明包含步骤、适用宠物或性格处理方式；不能据此判断具体适用性，需由商家确认。`
}

function merchantFacts(result) {
  const merchant = result.merchant
  return JSON.stringify({
    type: 'trusted_merchant_public_info',
    merchant: {
      id: merchant.id,
      display_name: merchant.display_name,
      address: merchant.address,
      phone: merchant.phone,
      time_zone: merchant.time_zone,
      is_demo: merchant.is_demo,
      source: merchant.source,
      services: merchant.services.map((service) => ({
        id: service.id,
        name_zh: service.name_zh,
        duration_minutes: service.duration_minutes,
        price_note: service.price_note,
      })),
    },
    data_boundary: '公开目录只列出服务名称、时长和价格说明；未提供服务步骤、适用宠物或性格处理方式。',
    availability_checked: false,
    booking_created: false,
  }, null, 2)
}

function merchantInline(result) {
  const merchant = result.merchant
  const serviceLines = merchant.services
    .map((service) => `- ${service.name_zh}：约 ${service.duration_minutes} 分钟；${service.price_note}`)
    .join('\n')
  return [
    `## ${merchant.display_name}`,
    '',
    `- 地址：${merchant.address}`,
    `- 电话：${merchant.phone}`,
    `- 时区：${merchant.time_zone}`,
    `- 数据来源：${merchant.source}`,
    '- 资料边界：只列出服务名称和价格说明；未提供服务步骤、适用宠物或性格处理方式',
    '',
    serviceLines,
  ].join('\n')
}

export class MerchantCoordinator {
  constructor({ toolClient }) {
    this.toolClient = toolClient
  }

  async run(_input, options = {}) {
    const result = await this.toolClient.getMerchantInfo({
      ownerId: options.ownerId,
      sessionId: options.sessionId,
      turnId: options.turnId,
      signal: options.signal,
    })
    const speech = merchantSpeech(result)
    return {
      content: merchantFacts(result),
      metadata: {
        presentation: {
          speech,
          inline: {
            title: '演示商家资料',
            format: 'markdown',
            content: merchantInline(result),
          },
        },
        merchantId: result.merchant.id,
        source: result.merchant.source,
      },
    }
  }
}

export class MerchantToolAgent {
  constructor({ toolClient, coordinator }) {
    this.toolClient = toolClient
    this.coordinator = coordinator
    this.enabled = true
    this.protocol = 'pet-reception-http'
    this.label = 'Pet Reception merchant HTTP tool'
  }

  describe() {
    return {
      enabled: true,
      protocol: this.protocol,
      kind: 'deterministic-http-tool',
      label: this.label,
      status: 'configured',
      capabilities: { backendUi: false },
    }
  }

  health() {
    return this.toolClient.health()
  }

  status() {
    return {
      enabled: true,
      ok: true,
      status: 'configured',
      code: 'CONFIGURED',
    }
  }

  runCoordinator(input, options = {}) {
    return this.coordinator.run(input, options)
  }

  respondPermission() {
    return Promise.reject(new Error('the read-only merchant tool does not request permissions'))
  }

  canRecoverDelegatedWork() {
    return false
  }

  uiUrl() {
    return Promise.resolve(null)
  }

  close() {
    return Promise.resolve()
  }
}

export function createMerchantIntegration(config) {
  const toolClient = new MerchantToolClient(config)
  const coordinator = new MerchantCoordinator({ toolClient })
  const agent = new MerchantToolAgent({ toolClient, coordinator })
  return { agent, coordinator, toolClient }
}
