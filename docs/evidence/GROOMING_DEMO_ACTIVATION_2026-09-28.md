# 犬猫组合计价语音演示入口激活证据（2026-09-28）

## 实际运行基线与诊断

- 修改基线：`e13031c`；未回退、未升级 Qwen Audio Agent。
- 原日常入口绑定 `demo-grooming-001` 和 `config/demo-merchant.json`，数据库中只有两个 legacy 犬类服务，`service_configuration` 均为空，因此实际通话只能返回原基础洗护/全套美容和起价说明。
- 当前后端和语音网关统一绑定 `demo-grooming-combo-001` 与 `config/demo-grooming-combination-merchant.json`。
- 原店仍保留 2 项服务和 35 笔历史预约；新店独立保存 4 项服务，4 项均从 PostgreSQL 完整读回结构化配置。
- 当前真实 Qwen Session 暴露 `quote_service`；运行日志记录模型完成三次真实报价调用和付款后的 `get_booking_status`，不是只检查源码或用直接 HTTP 调用替代模型联调。

## 当前可用入口和演示数据

- 入口：`http://127.0.0.1:3101/`
- 商家：Paws & Whiskers 犬猫美容组合计价演示店。
- 服务：犬类洗澡套餐、犬类理发套餐、猫类美容套餐、犬猫单独剪指甲。
- 人员：Lisa 服务犬类组合；Mia 服务猫类组合；两人均可执行独立剪指甲。营业时间、人员班次和服务资格相互一致。
- 所有新增价格、时长、附加项和预约金均在商家资料与客户话术中标为本地演示数据，不代表真实门店或网站政策。

## 运行中可核对结果

| 输入 | 运行中结果 |
| --- | --- |
| 犬类洗澡，15 lb | USD 50.00，60 分钟，模拟预约金 USD 20.00 |
| 犬类洗澡，35 lb | USD 60.00，60 分钟，模拟预约金 USD 20.00 |
| 犬类理发，35 lb，刷牙 | USD 117.00，130 分钟，模拟预约金 USD 30.00 |
| 上一项再重复选择套餐已含剪指甲 | 剪指甲增价 USD 0、增时 0 分钟；重复项去重 |
| 猫类美容 | USD 70.00–90.00，90 分钟，模拟预约金 USD 25.00 |
| 单独剪指甲 | USD 18.00，15 分钟，模拟预约金 USD 5.00 |

只读报价探针执行前后，新商家草稿、查询、准备和预约计数均为 `1/1/1/1`，证明报价本身没有修改预约状态。真实模型的两次纯询价会话只产生入口要求的空草稿，草稿保持 version 0、业务字段为空；只有客户明确要求预约后才写入组合选择。

## 完整预约证据

真实 Qwen 文字会话先对犬类理发加刷牙询价；模型只追问缺少的体重，客户补充 35 lb 后真实调用 `quote_service` 并回答 USD 117、130 分钟和 USD 30 预约金。客户随后明确选择预约，系统查询实际排班、受控播报包含服务组合、金额、预约金和时间的摘要，再经明确确认创建 `BK-3B6D70807442`。

客户页面付款前为 `awaiting_payment`，显示报价 USD 117、刷牙和应付模拟预约金 USD 30；点击本地模拟支付后变为 `confirmed`，确认码 `PR-3B6D70807442`，数据库记录实付 3000 美分。回到同一 Qwen 会话声称已付款后，模型自动调用 `get_booking_status`，日志终态为 `confirmed`。另一个确定性运行探针预约 `BK-2DD33E99292D` 也完成相同闭环。客户访问 token 未写入本文、模型消息或日志摘要。

## 验证结果与边界

- `scripts/check.ps1`：通过；后端 59 passed / 50 skipped，Web 6 passed，Voice 71 passed，Web production build 和 `pip check` 通过。
- `RUN_POSTGRES_TESTS=1 pytest -m postgres -q`：独立真实 PostgreSQL 50 passed。
- `RUN_POSTGRES_TESTS=1 pytest -q`：后端数据库外与真实 PostgreSQL 合计 109 passed。
- `npm run probe:combination-demo --workspace @pet-reception/voice-gateway`：通过完整组合报价、排班、摘要、预约金和客户页面支付闭环。
- `COMBINATION_PROBE_QUOTE_ONLY=1 node voice-gateway/scripts/probe-grooming-combination.mjs`：通过；前后业务记录计数不变。
- 真实 Qwen 模型文字联调：通过报价、跨轮补体重、明确选择后预约、模拟付款后状态核实。
- 真人麦克风和扬声器：本轮未执行，留给用户在当前入口验收；不能据此声明真人音频已经通过。

未新增 Agent、RAG、规则引擎或状态系统；未改 Qwen runtime patch、VAD、播放器、确认门槛、幂等、防超约、凭证隔离或恢复机制。
