import { configureEnvironment } from './config.mjs'
import { createMerchantIntegration } from './merchant-tool.mjs'

async function main() {
  const configuration = configureEnvironment()
  const { createGatewayApplication } = await import('qwen-audio-agent/gateway-application')
  const integration = createMerchantIntegration(configuration)
  const gateway = createGatewayApplication({
    agent: integration.agent,
    coordinator: integration.coordinator,
    initialGreeting: configuration.initialGreeting,
    preferredAudioInputLabel: configuration.preferredAudioInputLabel,
    autoStart: false,
  })

  gateway.start({ host: process.env.HOST, port: Number(process.env.PORT) })

  const close = async () => {
    await gateway.close()
    await integration.agent.close()
  }
  process.once('SIGINT', () => void close().finally(() => process.exit(0)))
  process.once('SIGTERM', () => void close().finally(() => process.exit(0)))
}

main().catch((error) => {
  console.error(`voice gateway failed to start: ${error.message}`)
  process.exitCode = 1
})
