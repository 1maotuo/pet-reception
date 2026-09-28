# Pet Reception

面向美国宠物医院、美容和日托商家的中文实时语音 AI 前台。当前仓库已完成 M4 的实现、独立 Review 修复与自动化/真实千问文字复验：同一个 Qwen Realtime Session 可读取结构化美容目录与商家当地日期、批量写草稿并同轮查询真实排班、准备并受控播报摘要、在下一轮明确确认后生成待付款记录，以及查询同一操作状态。网页“开始新预约”会经网关创建新的受信业务会话和空草稿；普通重连仍恢复原预约。真人麦克风、扬声器、停顿和打断仍需用户完成 C 层阶段验收；尚未进入 M5。

## 当前组件

- `web/`：React 19 + TypeScript 7 + Vite 8 状态页与客户预约凭证页。
- `backend/`：FastAPI 后端，提供健康检查、持久化商家/草稿、真实排班、短时占位、确认事务、状态恢复及客户/商家结果 API。
- `voice-gateway/`：固定 `qwen-audio-agent@1.11.0`，复用其 WebUI、实时音频、播放、打断和重连机制；注入确定性的受信业务 HTTP 协调器，并通过版本守卫补丁配置同一套服务端 VAD、逻辑会话问候去重、M4 六工具许可集合和播放完成回执。
- `config/demo-merchant.json`：当前演示商家、结构化美容服务目录、前台角色、场景安全分流和能力开关的受信配置；后端将业务事实 seed 到 PostgreSQL，语音网关读取同一份身份与前台配置。
- `compose.yaml`：绑定本机回环地址的 PostgreSQL 17.11 开发服务。
- `scripts/check-m4.ps1`：运行全量数据库外回归、独立 `*_test` PostgreSQL 测试、构建、运行服务健康检查，以及开发库真实 HTTP 工具闭环探针。

## 运行条件

- Node.js `^24.15.0` 与 npm `11.12.1`。Qwen Audio Agent 1.11.0 要求 `^22.22.2 || ^24.15.0 || >=26.0.0`；本仓已在 24.15.0 和 24.19.0 验证，项目限定 Node 24 的兼容范围。
- Python `3.12.x`。
- Docker Desktop，用于开发 PostgreSQL。
- 真实语音需要百炼 `DASHSCOPE_API_KEY`、浏览器麦克风权限和可用音频设备。

不要将 API Key 粘贴到聊天、前端代码或 Git。真实密钥只放在被忽略的根目录 `.env`。

## 首次安装（PowerShell）

在仓库根目录执行：

```powershell
.\scripts\init-env.ps1
```

脚本只在 `.env` 不存在时创建文件，并为网关内部密钥生成随机值。随后在 `.env` 中本地填写：

```dotenv
DASHSCOPE_API_KEY=你的百炼APIKey
```

安装依赖：

```powershell
node --version  # 必须是 v24.15.0 或更高的 Node 24
npm ci

python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
backend\.venv\Scripts\python.exe -m pip install -e backend --no-deps
```

`npm ci` 会运行历史文件名保留的 `scripts/apply-qwen-m1-patch.mjs`。它现在包含 M1–M4 所需的集中兼容钩子，只接受锁定的 Qwen Audio Agent 1.11.0；版本或源码锚点不匹配会停止安装，避免对未知上游静默打补丁。

需要替换演示商家时，在本地 `.env` 同时设置匹配的 `DEMO_MERCHANT_ID` 和 `MERCHANT_CONFIG_PATH`，随后再次执行 `python -m pet_reception_backend.manage setup` 将配置 seed 到开发库。语音网关读取该受信文件，FastAPI 从 seed 后的 PostgreSQL 读取公开事实；文件内 ID 与环境变量不一致时启动失败，不会静默绑定到另一家商户。

美容服务资料不是任意说明文字：每项服务可写 `supported_species`、`included_items`、
`recommended_for`、`assessment_required_for`、`not_included` 和 `source`；商家级
`grooming_policy` 写整体物种、人工评估、推荐与医疗边界。修改配置后必须重新执行 `manage setup`
才会写入数据库。当前文件明确是演示数据，只支持犬；任何打结先由店员评估，不能由模型凭行业常识
扩展成猫服务、医疗处理或承诺一定能处理打结。

`business_hours` 是可向客户公开的门店营业时间，和用于计算候选的员工 `weekly_hours` 分开；不能
拿某位员工的工作窗口推断门店闭店时间。员工可配置 `public_intro` 与 `specialties`，只用于公开
介绍，真正能否承接某项服务仍以 `service_ids` 和实时排班为准。年龄、疫苗、到店、迟到、攻击风险
和价格确认规则保存在结构化 `grooming_policy` 中；当前值均明确标记为演示商家资料。

## 启动

先启动数据库：

```powershell
docker compose up -d db
docker compose ps
```

首次启动或数据库结构变化后，执行迁移并写入明确标注的演示商家：

```powershell
backend\.venv\Scripts\python.exe -m pet_reception_backend.manage setup
```

该命令可重复执行；不会删除草稿或重建数据库。后端不会在请求过程中偷偷创建业务表，未迁移时会如实失败。

分别打开三个终端，在仓库根目录运行：

```powershell
# 终端 1：FastAPI
backend\.venv\Scripts\python.exe -m uvicorn pet_reception_backend.app:app --host 127.0.0.1 --port 8010

# 终端 2：React 状态页
npm run dev:web

# 终端 3：Qwen Audio Agent + 商家 HTTP 工具
npm run dev:voice
```

打开：

- 状态页：<http://127.0.0.1:5173>
- FastAPI 文档：<http://127.0.0.1:8010/docs>
- Qwen 原版 WebUI：<http://127.0.0.1:3101>

前端会实时显示后端和数据库状态。数据库不可用时页面显示“当前不可用”，`GET /readyz` 返回 503，不会静默降级。

## 验证

默认确定性检查：

```powershell
.\scripts\check.ps1
```

数据库运行后运行完整 M4 检查。数据库测试只创建/使用名称以 `_test` 结尾的独立测试库；最后的运行中 HTTP 探针会在开发库创建一条明确标识的未来待付款演示记录，但不会打印任何密钥或访问 token：

```powershell
.\scripts\check-m4.ps1
```

检查 HTTP 状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
Invoke-RestMethod http://127.0.0.1:3101/api/health
```

语音网关会记录不含客户原话、姓名、电话和工具参数的耗时事件：`voice_turn.input_ended`、
`voice_turn.response_started`、`voice_turn.first_audio`、`voice_turn.response_done` 以及
`business_tool.started/completed/failed/ignored`。工具终态包含实际 `durationMs`；完整轮次包含
从用户停说到响应阶段的 `sinceInputEndMs`。这些数据用于形成真实样本后的 P50/P95，当前 5 秒
HTTP 超时只是失败边界，不是对客户承诺的响应时间。实时查看本地日志：

```powershell
Get-Content logs\voice-current.stdout.log -Wait |
  Select-String 'voice_turn\.|business_tool\.'
```

完成若干次真实通话后，用固定 Node 24 环境汇总每种工具与完整轮次的样本数、最小值、P50、P95
和最大值；没有样本时命令会明确返回 `no_samples`，不会生成一个看似准确的承诺：

```powershell
npm run report:voice-latency
```

真实商家工具探针要求 FastAPI 正在运行，并使用 `.env` 中的服务令牌：

```powershell
npm run probe:tool --workspace @pet-reception/voice-gateway
npm run probe:m4 --workspace @pet-reception/voice-gateway
```

## M2 草稿与排班 API

受保护入口为：

- `GET /api/internal/voice/merchant-info`：从 PostgreSQL 读取当前绑定商家和服务。
- `GET /api/internal/voice/booking-draft`：按受信 `merchant + owner + voice session` 获取或创建一个草稿。
- `PATCH /api/internal/voice/booking-draft`：批量更新、纠正或显式清空字段；必须携带当前 `expected_version`。
- `POST /api/internal/voice/service-quotes`：阶段 A 的受保护只读报价；询价条件全部显式传入，商家身份来自受信请求头，不读取或修改预约草稿。当前未注册为 Qwen 实时工具。
- `POST /api/internal/voice/availability-searches`：只从当前受信草稿取服务、日期、时段和人员，查询 PostgreSQL 排班。
- `GET /api/internal/voice/availability-searches/{id}`：重新读取持久化结果，并检查它对当前排班条件是否仍有效。

PATCH 只接受业务字段。`merchant_id`、`owner_id`、`voice_session_id`、来电号码和电话来源不能由模型在请求正文指定。未提供字段保持不变；显式 JSON `null` 不等于清空，清空必须列入 `clear_fields`；写入同值不会增加版本。未知服务返回 422，旧版本返回 409。

响应分别返回 `missing_for_availability` 与 `missing_for_booking`。查询只要求真实服务与日期；如果用户点名人员，还必须先得到当前商家真实 `staff_id`。正式提交仍要求客户、电话、宠物及后续选择的候选时段，因此 M2 不会误称已经预约成功。

排班查询使用独立的 `availability_revision`：服务、日期、时段或人员变化会使旧候选变成 `stale`，而姓名和联系电话变化不会无条件重查排班。人员偏好文本发生实质变化但未同时提交新的真实 `staff_id` 时，旧人员 ID 会自动撤销并重新进入待解析状态。重读查询只返回尚未开始的候选；全部候选或请求日期过期后返回 `stale / availability_expired`。

候选时间带商家 IANA 时区偏移；无空档时只返回后续真实开放日期。DST 切换日中不存在、存在歧义或跨越 UTC offset 跳变的时间段不会生成候选，其余时段仍按真实 UTC 时长计算。重新导入配置时，被移除的服务和员工会停用而不是删除历史引用，并使相关草稿和旧候选失效；恢复配置可重新启用。查询结果明确包含 `availability_checked=true` 和 `booking_created=false`。

可在 FastAPI 文档中查看契约，也可直接运行真实 PostgreSQL 行为测试：

```powershell
$env:RUN_POSTGRES_TESTS = '1'
Push-Location backend
.\.venv\Scripts\python.exe -m pytest tests\test_m2_drafts_postgres.py
Pop-Location
Remove-Item Env:RUN_POSTGRES_TESTS
```

### 宠物美容组合与计价（阶段 A）

`MerchantService.service_configuration` 是可选的强类型 JSONB 配置。它可以表达套餐或独立单项、稳定标识的包含项目、收费附加项、犬/猫独立服务、连续且无重叠的 lb/kg 体重档、价格语义和时长影响。金额统一使用整数美分。未配置结构化价格的旧服务继续返回原 `price_note`，不会把未知金额当作 0。

当前 `config/demo-merchant.json` 没有被测试价格覆盖；完整商业价格表、体重档边界、附加项时长和预约金金额仍待商家确认。自动测试只使用 `backend/tests/fixtures/synthetic-grooming-merchant.json` 中明确标注的 synthetic 数据。阶段 A 不修改草稿、生产排班、prepare/confirm、模拟付款页面，也不向实时模型开放报价工具。

后端正在运行时，可执行一次独立 session 的开发库 HTTP 演示（只写带 `m2_probe_` 标识的演示草稿，不打印服务令牌）：

```powershell
.\scripts\probe-m2-draft.ps1
```

完整排班演示会依次验证候选、电话修改保持有效、日期变化使旧结果失效、闭店日和替代日期：

```powershell
.\scripts\probe-m2-availability.ps1
```

演示种子中 Lisa 可做基础洗护和全套美容，Marco 只可做基础洗护；周日闭店。服务时长、前后缓冲和工作窗口均来自 PostgreSQL。M4 已将这些入口接入同一个 Qwen Realtime Session；商家、客户、会话、轮次及呈现/确认事件仍只由网关受信上下文提供。

## M3 占位、确认与预约结果

M3 业务入口保持在确定性 HTTP 层，并由 M4 的薄协调器暴露给同一个实时模型：

- `POST /api/internal/voice/booking-preparations`：从当前受信草稿和真实候选生成服务端摘要快照，并短时占用人员的完整 busy interval。
- `GET /api/internal/voice/booking-preparations/{id}`：网关重启后按受信商家、客户和业务会话恢复同一准备记录；不会据此伪造新的播放回执。
- `POST /api/internal/voice/booking-preparations/{id}/presented`：由受信协调层记录该快照已经完整呈现；呈现事件不来自模型正文。
- `POST /api/internal/voice/booking-preparations/{id}/confirm`：只接受期望草稿版本，并要求新的受信语音轮次与确认事件；正文中的 `confirmed=true` 会因额外字段被拒绝。
- `GET /api/internal/voice/booking-operations/{operation_id}`：在超时、断线或响应丢失后核实同一操作，不重新创建预约。
- `GET /api/customer/bookings/{booking_reference}`：使用预约专属 Bearer 凭证读取待付款、已确认或已过期结果。
- `POST /api/customer/bookings/{booking_reference}/simulate-payment`：本地开发模拟付款成功；只接收受保护的预约凭证和幂等事件，不接收银行卡资料。
- `GET /api/internal/merchant/bookings[/{id}]`：使用商家管理员令牌读取固定商家的同一预约记录。

准备后任何客户资料或预约条件的实质变化都会撤销旧摘要/占位；占位过期也会释放。确认在 PostgreSQL 单事务中创建 `awaiting_payment` 记录，重复确认返回同一记录，人员区间冲突不会产生第二条有效占位。客户页面路径为凭证返回的 `/booking/{booking_reference}#token=...`；付款前页面不会显示成功或正式确认码。本地模拟付款后同一记录才变成 `confirmed` 并生成 `PR-` 码。付款保留默认十分钟，超时释放时段和草稿绑定。token 位于 URL fragment，数据库只保存 token 哈希。

后端正在运行时可执行真实开发库闭环探针。它会创建一条明确标记为 `M3 演示` 的未来预约，不打印服务令牌、管理员令牌或客户访问 token：

```powershell
.\scripts\probe-m3-booking.ps1
```

探针会输出预约 ID、参考号和付款后的确认编号，并验证确认前待付款、模拟付款后成功，以及状态、客户和商家读取都指向同一记录。该付款仅供本地验收，不连接真实支付平台。

确认响应丢失和网关重启恢复可在独立测试库上用真实本地 HTTP 验证。先在终端 1 启动测试库后端，再在终端 2 运行两个探针；这些命令不读取或清理开发库：

```powershell
# 终端 1（项目根目录）
.\backend\.venv\Scripts\python.exe backend\tests\m4_fault_probe_server.py

# 终端 2（voice-gateway 目录）
npm run probe:m4-confirm-loss
npm run probe:m4-restart
```

## M4 真人语音检查

1. 确认 `.env` 已配置真实 `DASHSCOPE_API_KEY`，启动后 `/api/health` 的 `voiceConfigured` 为 `true`。
2. 打开 Qwen WebUI。无需先说话，应主动听到“Paws & Bubbles 宠物美容预约前台”的本店问候；允许麦克风后连续进行至少三轮中文对话。
3. 问“你是谁、能帮我做什么”。应只描述当前店的服务咨询和预约准备，不能说能操作电脑、查新闻或充当通用助手。
4. 说“我最近身体不舒服”和“我家狗狗不舒服，还能洗澡吗”。应分别给出合适的安全建议，然后带回当前美容店范围；不能承诺查找人类医院或做诊断。
5. 一句话给出客户姓名、电话、宠物名/种类/年龄、服务、日期和时段，并同时问价格。模型应保留演示价格限定，一次批量保存已有字段，然后给出 PostgreSQL 中的真实候选，不逐项重复询问。
6. 选定候选后应听到服务端生成的完整摘要；摘要播完前不能出现预约成功。摘要中插话后，原播报应停止；若立刻说“确认”，系统应拒绝提交并从受控入口重新完整播报摘要。
7. 慢慢说一个带口音或口语省略的请求，并在句中分别停顿约半秒、一秒和约一秒半。当前唯一轮次检测为 `server_vad`，噪声环境起始配置为声学阈值 0.65、结束等待 1800 ms；预期背景声不轻易触发，短暂停顿不抢答，实际听感记录为真人证据。
8. 摘要完整播完后，在新一轮说“确认，就按这个提交”。应只听到已生成 `BK-` 参考号并“等待本地模拟支付”；不能说正式确认，也不能出现 `PR-` 确认码。页面应另外显示“打开客户预约页面”的专属链接，助手不得朗读链接或 token。随后问状态，应返回同一参考号和待付款状态。
9. 在提交播报期间插话，再问“刚才是不是已经约上了”。系统应查询原 `operation_id`，不再次提交；真实 DB 结果成功则报告同一记录，失败则如实报告。
10. 完成第一笔后先刷新或重启网关，确认仍恢复原预约；再点击“开始新预约”，确认新连接重新问候、草稿为空，旧会话迟到结果不串入新预约。
11. 问“猫咪毛发打结能否做全套美容”。当前演示目录应明确只支持犬，并把任何打结转为店员评估，不能承诺猫可服务或凭常识补充目录没有的步骤。
12. 在网页说“电话就是我现在打来的这个”。网页没有可信电话线路信息，助手应追问实际号码；后端也会拒绝 `incoming_phone` 等占位值。宠物名未提供时同样不能写成“未知”。

当前模型只看到六个受控入口：`get_merchant_info`、`update_booking_draft`、`search_availability`、`prepare_booking`、`confirm_booking`、`get_booking_status`。上游通用的后台 Agent、记忆、清单、提醒和权限工具既不进入模型 Session，也会在被强制调用时由运行时拒绝。`request_human_callback` 尚未实现，不会伪装成可用工具。

停顿参数位于根目录被忽略的 `.env`：

```dotenv
VOICE_TURN_DETECTION_TYPE=server_vad
VOICE_SILENCE_DURATION_MS=1800
VOICE_VAD_THRESHOLD=0.65
VOICE_AUDIO_INPUT_LABEL=USB Audio Device
```

静音时间有效范围为 200–6000 ms，声学阈值有效范围为 -1 到 1；阈值越高越不容易被背景噪声触发，但也要求说话更清楚。调整后需重启语音网关；这些都是千问原生服务端 VAD 参数，不是浏览器侧的第二套断句逻辑。

`VOICE_AUDIO_INPUT_LABEL` 用于匹配物理录音设备名称。本机已配置为耳机对应的 `USB Audio Device`；页面取得权限后会把匹配设备的真实 `deviceId` 传给上游原有 `getUserMedia()`。required 模式会覆盖调用方传入的冲突 `deviceId`；设备不存在或同一标签匹配多个物理设备时明确报错，不会静默回退或任意选一个。该薄适配不替换上游录音、播放、VAD 或重连实现。

当前 0.65/1800 ms 只是面向普通室内噪声的首轮实验值，不代表所有麦克风和口音都已验收。背景声仍频繁触发时可小步提高阈值；正常说话漏收时应降低阈值；句中停顿仍被拆开时延长静音时间；说完等待过久时缩短静音时间。每次只改一个参数并重启网关，保留真人对照结果。

## 身份与密钥边界

Qwen Gateway 使用签名的 HttpOnly 身份 Cookie。模型不能提交授权用的 `merchant_id`、`owner_id`、`session_id`、`turn_id`、呈现事件或确认事件。薄协调器从 Gateway 服务端上下文读取身份，以服务令牌调用 FastAPI；FastAPI 同时验证令牌和固定商家绑定。客户访问 token 与凭证 URL 不进入模型工具结果，只通过绑定到原 WebSocket 的页面卡片交付。摘要由服务端快照生成并走原版播放队列，只有播放、响应和转写都完成，且转写与服务端快照中的身份、服务、员工、护理事项、日期、时间和实际电话号码一致时才记录 `presented`；三类事件的到达顺序不影响最终一次上报，取消/打断播放不会被晚到事件重新放行。

上游版本与扩展边界见 `docs/upstream/QWEN_AUDIO_AGENT.md`，M4 Review 修复、测试结果和仍未执行的真人项见 `docs/evidence/M4_REVIEW_REMEDIATION_2026-09-20.md`。
