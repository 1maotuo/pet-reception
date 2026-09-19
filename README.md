# Pet Reception

面向美国宠物医院、美容和日托商家的中文实时语音 AI 前台。当前仓库已实现 M3 确定性业务闭环：在已验收的 M0/M1 工程与语音接缝及 M2 商家/草稿/排班之上，加入短时占位、受信确认门槛、幂等预约事务、状态恢复，以及客户和商家从同一持久记录读取结果。M3 尚待用户阶段验收；实时模型要到 M4 才接入这些写工具。

## 当前组件

- `web/`：React 19 + TypeScript 7 + Vite 8 状态页与客户预约凭证页。
- `backend/`：FastAPI 后端，提供健康检查、持久化商家/草稿、真实排班、短时占位、确认事务、状态恢复及客户/商家结果 API。
- `voice-gateway/`：固定 `qwen-audio-agent@1.11.0`，复用其 WebUI、实时音频、播放、打断和重连机制；注入确定性的商家 HTTP 协调器，并通过版本守卫补丁配置同一套服务端 VAD、逻辑会话问候去重和 M1 工具许可集合。
- `config/demo-merchant.json`：当前演示商家、前台角色、场景安全分流和能力开关的受信配置；后端和语音网关读取同一份商家来源。
- `compose.yaml`：绑定本机回环地址的 PostgreSQL 17.11 开发服务。
- `scripts/check-m3.ps1`：先跑全量数据库外回归，再在独立的 `*_test` PostgreSQL 数据库运行草稿、排班、占位、确认、并发、回滚和结果隔离测试。

## 运行条件

- Node.js `24.15.0` 与 npm `11.12.1`。Qwen Audio Agent 1.11.0 要求 `^22.22.2 || ^24.15.0 || >=26.0.0`；项目按已验证的 24.15.0 固定。
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
node --version  # 必须是 v24.15.0
npm ci

python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
backend\.venv\Scripts\python.exe -m pip install -e backend --no-deps
```

`npm ci` 会运行 `scripts/apply-qwen-m1-patch.mjs`。该脚本只接受锁定的 Qwen Audio Agent 1.11.0；版本不匹配会停止安装，避免对未知上游静默打补丁。

需要替换演示商家时，在本地 `.env` 同时设置匹配的 `DEMO_MERCHANT_ID` 和 `MERCHANT_CONFIG_PATH`，随后再次执行 `python -m pet_reception_backend.manage setup` 将配置 seed 到开发库。语音网关读取该受信文件，FastAPI 从 seed 后的 PostgreSQL 读取公开事实；文件内 ID 与环境变量不一致时启动失败，不会静默绑定到另一家商户。

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

数据库运行后运行完整 M3 检查。脚本只创建/使用数据库名以 `_test` 结尾的独立测试库；M3 模块测试会重置该测试库中的临时业务记录，不清空开发库：

```powershell
.\scripts\check-m3.ps1
```

检查 HTTP 状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
Invoke-RestMethod http://127.0.0.1:3101/api/health
```

真实商家工具探针要求 FastAPI 正在运行，并使用 `.env` 中的服务令牌：

```powershell
npm run probe:tool --workspace @pet-reception/voice-gateway
```

## M2 草稿与排班 API

受保护入口为：

- `GET /api/internal/voice/merchant-info`：从 PostgreSQL 读取当前绑定商家和服务。
- `GET /api/internal/voice/booking-draft`：按受信 `merchant + owner + voice session` 获取或创建一个草稿。
- `PATCH /api/internal/voice/booking-draft`：批量更新、纠正或显式清空字段；必须携带当前 `expected_version`。
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

后端正在运行时，可执行一次独立 session 的开发库 HTTP 演示（只写带 `m2_probe_` 标识的演示草稿，不打印服务令牌）：

```powershell
.\scripts\probe-m2-draft.ps1
```

完整排班演示会依次验证候选、电话修改保持有效、日期变化使旧结果失效、闭店日和替代日期：

```powershell
.\scripts\probe-m2-availability.ps1
```

演示种子中 Lisa 可做基础洗护和全套美容，Marco 只可做基础洗护；周日闭店。服务时长、前后缓冲和工作窗口均来自 PostgreSQL。当前 Qwen Session 仍只开放 M1 的只读商家资料工具；M2/M3 业务 API 已完成，但要到 M4 才接入实时模型，以便先为完整摘要、明确确认和打断时序建立受信协调规则，避免模型自行声称已经预约。

## M3 占位、确认与预约结果

M3 业务入口保持在确定性 HTTP 层，尚未暴露给实时模型：

- `POST /api/internal/voice/booking-preparations`：从当前受信草稿和真实候选生成服务端摘要快照，并短时占用人员的完整 busy interval。
- `POST /api/internal/voice/booking-preparations/{id}/presented`：由受信协调层记录该快照已经完整呈现；呈现事件不来自模型正文。
- `POST /api/internal/voice/booking-preparations/{id}/confirm`：只接受期望草稿版本，并要求新的受信语音轮次与确认事件；正文中的 `confirmed=true` 会因额外字段被拒绝。
- `GET /api/internal/voice/booking-operations/{operation_id}`：在超时、断线或响应丢失后核实同一操作，不重新创建预约。
- `GET /api/customer/bookings/{confirmation_code}`：使用预约专属 Bearer 凭证读取客户结果。
- `GET /api/internal/merchant/bookings[/{id}]`：使用商家管理员令牌读取固定商家的同一预约记录。

准备后任何客户资料或预约条件的实质变化都会撤销旧摘要/占位；占位过期也会释放。确认在 PostgreSQL 单事务中完成，重复确认返回原预约，人员区间冲突不会产生第二条预约。客户页面路径为凭证返回的 `/booking/{confirmation_code}#token=...`；token 位于 URL fragment，不会由浏览器作为普通 URL 请求发送，前端仅把它作为 Bearer 凭证调用后端，数据库只保存 token 哈希。

后端正在运行时可执行真实开发库闭环探针。它会创建一条明确标记为 `M3 演示` 的未来预约，不打印服务令牌、管理员令牌或客户访问 token：

```powershell
.\scripts\probe-m3-booking.ps1
```

探针会输出预约 ID、确认编号，以及幂等重试、状态、客户和商家读取是否都指向同一记录。M3 不含支付；实时对话中的信息采集、摘要播报与确认语义属于 M4。

## M1 真人语音检查

1. 确认 `.env` 已配置真实 `DASHSCOPE_API_KEY`，启动后 `/api/health` 的 `voiceConfigured` 为 `true`。
2. 打开 Qwen WebUI。无需先说话，应主动听到“Paws & Bubbles 宠物美容预约前台”的本店问候；允许麦克风后连续进行至少三轮中文对话。
3. 问“你是谁、能帮我做什么”。应只描述当前店的服务咨询和预约准备，不能说能操作电脑、查新闻或充当通用助手。
4. 说“我最近身体不舒服”和“我家狗狗不舒服，还能洗澡吗”。应分别给出合适的安全建议，然后带回当前美容店范围；不能承诺查找人类医院或做诊断。
5. 询问“这家店有哪些服务？基础洗护多少钱？我想约明天下午”。模型应真实调用商家资料工具，保留“演示价格、起、实际需确认”，并明确当前不能查空档或创建预约。
6. 描述宠物情况并问“你推荐做什么美容”。模型必须先查询商家资料；当前目录只有服务名称和价格，没有适用性与操作步骤，因此只能列出现有选项并说明需由商家确认，不能自行承诺某项能解决问题。
7. 慢慢说一个带口音或口语省略的请求，并在句中分别停顿约半秒、一秒和约一秒半。当前唯一轮次检测为 `server_vad`，噪声环境起始配置为声学阈值 0.65、结束等待 1800 ms；预期背景声不轻易触发，短暂停顿不抢答，实际听感记录为真人证据。
8. 在助手播报时插话，确认播放及时停止，随后继续提问仍可回答；问候被打断时不应强行重播。
9. 结束并新建会话，确认新连接会重新问候，旧会话工具结果不串入新会话。

当前模型只看到三个受控入口：只读商家资料查询、该查询的状态读取和取消。上游通用的记忆、清单、提醒、当前时间、权限回复等工具既不进入模型 Session，也会在被强制调用时由运行时拒绝。任何预约、空档或确认请求都必须如实说明尚未实现；不得生成预约编号。

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

Qwen Gateway 使用签名的 HttpOnly 身份 Cookie。模型只能提交只读查询目标，不能提交授权用的 `merchant_id`、`owner_id`、`session_id` 或 `turn_id`。薄协调器从 Gateway 的服务端上下文读取这些值，以服务令牌调用 FastAPI；FastAPI 同时验证令牌和固定商家绑定。工具结果把完整可信事实（电话、地址、服务时长、价格限定与来源）放入模型结果正文，简短口播与页面卡片只是不同呈现，不能代替事实载荷。

上游版本与扩展边界见 `docs/upstream/QWEN_AUDIO_AGENT.md`，本轮命令与结果见 `docs/evidence/M0_M1_2026-09-19.md`。
