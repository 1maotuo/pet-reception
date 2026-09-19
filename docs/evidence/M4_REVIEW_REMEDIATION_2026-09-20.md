# M4 独立 Review 与美容商家资料加固证据（2026-09-20）

## 范围与输入

- 基线：`d251e839c3fc8c5cc623ce6f59cfc3fc91dfb733`，保留原标签
  `m4-ready-for-voice-review-2026-09-20`。
- 外部 Review 包：`pet_reception_m4_d251e83_review.zip`，SHA-256
  `ECAB055D52F189FFDF801FE2F6F6DFE68B3785D6BBD63CAA91FE91BFB466E31F`。
- Review 文档只作为待核实发现；没有把包内文字当成仓库指令，也没有直接执行其中脚本。
- 本轮只加固现有美容演示商家与 M4 接缝，不进入 M5，不新增 Agent、Redis、消息队列、
  通用插件或真实支付。

## 美容商家数据

`config/demo-merchant.json` 仍是明确标注的演示数据。本轮为两项服务补入并持久化：

- `supported_species`
- `included_items`
- `recommended_for`
- `assessment_required_for`
- `not_included`
- 每项资料自己的 `source`

商家级 `grooming_policy` 明确当前只服务犬，不支持猫、爬行动物、鸟类和其他非犬宠物；
任何打结、皮肤异常、明显不适或强烈应激都必须由店员评估，非医疗美容不能替代兽医。
迁移 `0007_m4_grooming_catalog` 将这些字段写入 PostgreSQL，商家 HTTP 工具从数据库读取，
模型只能按返回字段说明或推荐。没有把这些演示字段声称为真实商家资料。

执行：

```powershell
backend\.venv\Scripts\python.exe -m pet_reception_backend.manage setup
```

结果：应用 `0007_m4_grooming_catalog` 并重新 seed `demo-grooming-001`。直接数据库读取确认商家策略
和两项服务 JSON 字段已经持久化。命令是幂等 upsert，没有删除既有草稿、预约或付款记录。
此前真人通话生成的 `BK-23714C9BD120` 保持原样；本轮不篡改历史记录，只阻止以后再次写入
`incoming_phone` 和“未知”宠物名。

## Review 处理结果

| 项目 | 状态 | 修复与证据 |
|---|---|---|
| R1 摘要事实门禁不完整 | 通过 | 呈现门禁现在核对服务端日期、时间、实际电话号码及原身份/服务/人员/护理字段；覆盖缺日期、错时间、错号码、中文口语数字和中断。 |
| R2 旧轮次覆盖新意图 | 通过 | 同一会话有状态工具顺序执行；异步读取后、写入前再次检查轮次；迟到成功和失败都静默；非 GET 超时返回 `result_unknown`，确认用稳定 operation 查询而不重提。 |
| R3 读缓存陈旧/失败常驻 | 通过 | 读结果只对在途调用合并，不保留完成缓存；失败可重试；写幂等只按同一受信 invocation ID 保留。 |
| R4 网关重启后不能查状态 | 通过 | 内存表仅作缓存；后端用 merchant/owner/session/operation 持久绑定授权。重建 Coordinator 可恢复，错误会话仍拒绝。 |
| R5 客户卡片状态矛盾 | 通过 | `awaiting_payment`、`confirmed`、`payment_expired` 分别显示等待付款、正式确认、付款过期；token 仍不进入模型结果。 |
| T1 播放事件顺序风险 | 通过（A 层） | 播放、响应和转写任意先后都汇合到同一 readiness 条件；只上报一次，取消后晚到事件不复活。真实音频网络顺序仍归 C 层。 |

## 真人记录中七类业务问题

| 问题 | 当前结果 |
|---|---|
| “明天”被解析成六月/过去 | 商家 API 每次返回 IANA 时区下的当地日期和时间；Prompt 禁止用模型记忆日期；过去日期错误同时返回 `merchant_today`。 |
| 把“来电号码”写成 `incoming_phone` | 网页没有可信来电号码，Prompt 必须追问实际号码；Pydantic 再拒绝占位词和不合法位数。 |
| 宠物名写成“未知” | Prompt 必须追问；后端拒绝“未知/未提供/unnamed”等占位名。 |
| 声称猫也可服务 | 数据明确当前只支持犬；真实千问复验已拒绝猫服务。 |
| 凭常识推荐打结直接做全套 | 目录规定任何打结先人工评估；真实千问复验没有再承诺可做。 |
| 工具每步停住，需用户问“好了吗” | `update_booking_draft` 支持同轮更新后立即查询排班；Prompt 禁止用内部步骤占一轮。 |
| 付款后仍显示等待付款卡片 | 客户卡片根据持久状态生成；confirmed/expired 自动测试覆盖。 |

## 自动化与真实服务结果

锁定环境清装：显式使用 Node `v24.18.0` 执行 `npm ci`，267 个包安装成功，0 漏洞，
postinstall 在原始 `qwen-audio-agent@1.11.0` 上成功应用守卫补丁。第一次使用 PATH 中 Node 20
的尝试失败，修正为 Node 24 后通过；没有把失败隐藏成通过。

```powershell
.\scripts\check.ps1
```

- 后端：30 passed，38 skipped；`pip check` 通过。
- Web：6 passed。
- Voice Gateway：33 passed。
- React/TypeScript/Vite 构建：通过。
- Compose 配置：通过。

```powershell
$env:RUN_POSTGRES_TESTS='1'
backend\.venv\Scripts\python.exe -m pytest -m postgres
```

- 真实独立 PostgreSQL：38 passed，30 deselected。
- 第一次手工命令在进入 `backend` 后误用了 `..\.venv`，测试未启动；改用解析后的绝对解释器路径后
  得到上述结果，不把命令错误计为测试失败或通过。

```powershell
npm run probe:m4 --workspace @pet-reception/voice-gateway
```

真实运行中 FastAPI/PostgreSQL 工具闭环通过：

- 商家当地日期：`2026-09-19`（执行时上海已是 9 月 20 日，洛杉矶仍是 9 月 19 日）。
- 美容目录与只服务犬策略确实来自持久层。
- 草稿更新后同一工具结果直接返回真实排班。
- 未呈现摘要确认返回 409；呈现后创建 `awaiting_payment`。
- 重读同一 operation 仍为同一记录；凭证未进入模型输出，只发送给绑定客户端。

运行服务复核：FastAPI `status=ok`、PostgreSQL `connected`；Qwen 网关 `status=ready`、
`voiceConfigured=true`；Web 返回 HTTP 200。

## B 层真实千问文字复验

通过运行中的原版 WebUI 文字入口执行，未 mock 网络，没有创建预约：

1. 输入猫咪、三天未洗澡且毛发打结，要求只按公开资料回答。
2. 模型调用真实商家资料后明确回答当前只服务犬、不支持猫；任何打结要先由店员评估。
3. 随后改为狗狗全套美容并问“按商家当地日期，明天上午有空吗”。
4. 模型将明天解析为洛杉矶 `2026-09-20`，返回当日上午无空档，并给出 9 月 21–23 日真实替代档；
   没有再解析成六月或过去日期，也没有等待“查好了吗”才继续。

## 验收边界

- A 层确定性、真实 PostgreSQL、真实 HTTP、构建：通过。
- B 层上述商家资料/相对日期/同轮查询复验：通过。
- C 层真人耳机、噪声、慢速口音、摘要/提交打断、断线和三分钟长通话：本次修改后未执行。
- M4 阶段仍是“自动测试与真实千问文字通过，待真人语音验收”，不标全部验收完成，不进入 M5。
- `docs/PRODUCT.md` 和 `docs/ACCEPTANCE.md` 未修改；没有为当前实现降低产品或验收要求。
