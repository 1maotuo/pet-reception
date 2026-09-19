# Qwen Audio Agent 接入依据

核对日期：2026-09-19。

## 固定版本

- 官方仓库：<https://github.com/QwenAudio/qwen-audio-agent>
- 稳定发布：`v1.11.0`
- Git 标签提交：`c9fe9d02cd0f2d6b8dc54c75ad8e1d81b70549d2`
- npm 包：`qwen-audio-agent@1.11.0`
- npm 锁文件完整性：见根目录 `package-lock.json`
- Node 要求：`^22.22.2 || ^24.15.0 || >=26.0.0`
- 本项目验证运行时：Node `24.15.0`

选择 1.11.0 的依据：它是核对时最新稳定发布；官方把 2.0.0 标记为开发中。1.11.0 明确新增嵌入式 Gateway 和 Realtime Provider 扩展。本项目继续完整复用上游录音、音频传输、播放、打断和重连状态机，不另建浏览器端 VAD 或轮次提交定时器。

## 实际使用的公开接口

安装包 `package.json` 公开导出：

```text
qwen-audio-agent/gateway-application
qwen-audio-agent/realtime-provider
qwen-audio-agent/gateway-protocol
```

本项目调用 `createGatewayApplication()`，并注入一个确定性 HTTP 协调器。实时模型仍使用上游自带的 `spawn_thinking` 调度入口，但项目把模型可见工具收敛为只读商家查询、该查询的状态读取和取消，并在运行时再次拒绝未许可工具。协调器不包含另一个模型、Coding Agent、多 Agent 或通用 Workflow。

上游基础 Prompt 会把 `ASSISTANT.md` 包装为 `persona_only`，因此产品工具路由不能放在该文件。项目从受信的 `config/demo-merchant.json` 读取当前商家、场景分流和能力开关，将其服务端渲染进 `voice-gateway/config/frontend-agent/PROMPT.template.md`，运行产物写入被忽略的 `voice-gateway/runtime/frontend-agent/PROMPT.md`，再通过 `QWEN_AUDIO_AGENT_FRONTEND_PROMPT_DIR` 加载。服务和价格仍必须通过 HTTP 工具获得，不能由配置 Prompt 冒充工具结果。

1.11.0 没有公开“Realtime 首次就绪后由宿主主动播报”的钩子，Audio 模型目录也把 `turn_detection` 固定为 `smart_turn`。因此根目录 `scripts/apply-qwen-m1-patch.mjs` 在 `npm ci/install` 后对**恰好 1.11.0**应用守卫式兼容补丁：

- 将宿主提供的首次问候交给上游 `injectResult()`，使用同一响应队列、播放与打断机制；按可信 `ownerId + sessionId` 逻辑通话去重，同一会话重建 WebSocket 不重复，新 session 仍会问候。
- 让 DashScope Session 使用其原生 `server_vad`，并读取 `VOICE_VAD_THRESHOLD` 与 `VOICE_SILENCE_DURATION_MS`；当前噪声环境起始配置为 0.65/1800 ms。没有新增第二套 VAD、音频缓冲、提交计时器或重连逻辑。
- 在 Session 构造时过滤上游通用记忆、清单、提醒、时间和权限工具，并在 `ToolCallHandler` 分发前执行同一服务端许可检查；保留的工具 schema 改成与本项目真实只读能力一致。
- 补丁先校验 npm 包版本和唯一源码锚点；版本或源码形状不符会安装失败，而不是静默修改未知版本。`node_modules` 仍被忽略，持久来源是补丁脚本和锁文件。

[阿里云官方 Qwen Audio Realtime 参数文档](https://help.aliyun.com/en/model-studio/qwen-audio-realtime-user-guides)说明：`server_vad` 与 `smart_turn` 都是支持的轮次检测模式；`threshold` 越高越不容易被背景噪声触发，`silence_duration_ms` 越长越能容忍句中停顿，这两个参数只对 `server_vad` 生效。0.65/1800 ms 是针对“嘈杂环境、慢速分段表达”的第一组实验值，不是普适最优值，仍需在实际麦克风上验收；如安静环境听不清小声表达，应先检查输入设备和增益，再小幅降低阈值。

可信边界：上游 `IdentityManager` 使用签名 HttpOnly Cookie 解析 `ownerId`；Gateway 将 `ownerId`、语音 `sessionId` 和 `turnId` 作为服务端调用上下文交给协调器。模型工具参数只有工作目标，不含商家或身份授权字段。商家 ID 由网关环境固定，FastAPI 还要求服务端令牌。

## 原版运行和缺失条件

官方启动需要 `DASHSCOPE_API_KEY`，默认模型为 `qwen-audio-3.0-realtime-plus`。没有 Key 时，项目只为诊断显式设置官方支持的 `QWEN_AUDIO_ALLOW_UNCONFIGURED=1`；此时 Gateway 可以启动健康检查和 WebUI，但 `/api/health` 必须显示 `voiceConfigured=false`，页面必须显示缺少 Key，不能当作真实语音通过。

## 打断、会话和结果播报入口

在 1.11.0 安装源码中核实到：

- `server/src/voice/realtime-gateway.mjs` 处理 `response.interrupted`，保留上游打断机制。
- `server/src/voice/providers/dashscope.mjs` 提供 `buildResultInjection()`；工具最终结果通过同一实时上下文回注。
- `server/src/voice/frontend-tools.mjs` 提供 `resultResponseInstructions` 和 `speakResponseInstructions()`，用于受控的结果呈现与播报。
- `docs/architecture.md` 规定结果在播放完成后才标记已交付；用户打断、正在说话或有其他响应时延后重试，而不是把业务任务视为取消。

这些是上游代码与文档能力，不等于本项目自动获得了业务验收。当前本地 Key 已配置；真实中文多轮、插话、自然文本工具调用和正确会话回传已验证。新的主动问候、商家身份、范围回拉和 0.65/1800 ms 收音配置已进入真实 Session，其中噪声、慢速停顿、口音和小声表达的听感仍需用户复测。正式预约确认还需要后续把服务端确认快照和真实写入结果接到既有结果注入入口；本轮没有实现预约。
