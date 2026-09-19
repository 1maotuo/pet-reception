import { configureEnvironment } from '../src/config.mjs'
import { createMerchantIntegration } from '../src/merchant-tool.mjs'

const configuration = configureEnvironment()
const { coordinator } = createMerchantIntegration(configuration)
const result = await coordinator.run(
  { originalRequest: '请介绍这家店和服务价格', objective: '查询当前商家公开资料' },
  {
    ownerId: 'user_m1_probe',
    sessionId: 'm1-probe-session',
    turnId: 'm1-probe-turn',
  },
)

console.log(JSON.stringify({
  facts: JSON.parse(result.content),
  speech: result.metadata.presentation.speech,
  merchantId: result.metadata.merchantId,
  source: result.metadata.source,
}, null, 2))
