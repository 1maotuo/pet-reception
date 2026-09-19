# M3 独立 Review 修复与本地模拟付款证据（2026-09-20）

## 范围

- 输入：独立 Review R1–R4/F1。Review 用作缺陷证据，不作为重构指令。
- 用户另行明确授权：接入本地模拟付款。
- 本轮未进入 M4；没有修改 Qwen Audio Agent、实时工具集合、录音、VAD、播放或重连。
- 没有接真实支付、银行卡、邮件或短信，也没有把本地模拟称为真实收款。

## 实现结果

1. R1：排班保存/重读、草稿更新、prepare/confirm 统一为 merchant-first 锁序；真实 PostgreSQL 记录的 SQL 顺序测试通过。
2. R2：保存过的候选和替代日期会按当前 confirmed booking、有效付款占位和 preparation hold 重校验；已占候选不再宣称 current/available。
3. R3：冲突判断改为存在性查询，长候选同时重叠两条预约或两条 hold 时稳定返回不可用，不抛多行异常。
4. R4：准备占位过期清理可提交，已标 expired 的遗留草稿引用也会清除；锁后重新取时钟，锁等待跨过期限不会接受确认。
5. F1：状态页 Hooks 移入独立组件；畸形百分号凭证路径显示受控错误。
6. 客户确认只创建 `awaiting_payment` 记录、`BK-` 参考号和默认十分钟占位；正式 `PR-` 确认号为空。
7. 客户凭证页明确显示本地模拟付款，不采集卡资料。模拟付款成功后，同一记录才变为 `confirmed`，并由服务端生成正式确认号。
8. 付款超时持久化为 `payment_expired`，释放人员时段和草稿绑定；重复付款幂等，同一付款事件不能跨预约复用。
9. 数据库使用追加迁移 `0006_m3_simulated_payment`；已应用的 `0005_m3_booking` 未改写。

## 产品与验收基线变更

用户授权后，`docs/PRODUCT.md` 从“不实现支付”细化为“不接真实支付平台，但提供明确标注的本地模拟付款”。`docs/ACCEPTANCE.md` 新增 B16–B19，要求付款前不得声称成功、付款后同记录确认、过期释放及事件幂等。原有确认、并发、鉴权和持久化标准没有降低。

## 实际环境

- Windows / PowerShell
- Python 3.12 项目虚拟环境
- PostgreSQL 17.11 Docker 开发与独立 `*_test` 数据库
- Qwen Audio Agent 1.11.0（本轮未改）
- Node 24.19.0；依赖声明调整为上游支持的 `^24.15.0`，系统 Node 20 不满足要求

## 实际命令与结果

| 命令 | 结果 |
|---|---|
| `python -m pet_reception_backend.manage setup` | 通过；开发库追加应用 `0006_m3_simulated_payment` 并 seed 演示商家 |
| `scripts/check.ps1`（PATH 使用 Node 24.19.0） | 通过；后端数据库外 25 passed / 33 skipped，Web 6 passed，Voice 18 passed，Vite build、`pip check`、Compose config 通过 |
| `scripts/check-m3.ps1` | 通过；独立 PostgreSQL 33 passed / 25 deselected；整个脚本成功结束 |
| `RUN_POSTGRES_TESTS=1 python -m pytest -q` | 通过；58 passed |
| 全新临时 `pet_reception_m3_payment_fresh_test` 执行 setup 后删除 | 通过；依次应用 0001–0006，付款字段存在，seed 成功 |
| `scripts/probe-m3-booking.ps1` | 通过；prepared → presented → awaiting_payment → confirmed，同一 booking 被状态、客户和商家入口读取；未输出客户 token |
| `git diff --check` | 通过；仅提示 Windows 行尾将来可能转换，无空白错误 |

FastAPI 8010 已重启为当前源码；OpenAPI 包含 `/api/customer/bookings/{booking_reference}/simulate-payment`。开发库 HTTP 探针实际得到 `BK-` 参考号，付款前客户/状态均为 pending，本地模拟付款后得到同记录的 `PR-` 确认号。

## 验收状态

| 项目 | 状态 | 证据 |
|---|---|---|
| R1 | 通过 | 真实 PostgreSQL 锁序与并发回归 |
| R2 | 通过 | 当前 hold、confirmed booking、替代日期重校验测试 |
| R3 | 通过 | 0/1/2 冲突记录测试 |
| R4 | 通过 | 批量/直接过期与真实锁等待跨期限测试 |
| F1 | 通过 | Web 组件与畸形 URL 自动测试 |
| B16 | 通过（A） | HTTP/页面测试均不在 pending 时显示成功或正式码 |
| B17 | 通过（A） | 真实 PostgreSQL + HTTP 模拟付款闭环 |
| B18 | 通过（A） | 过期付款 409、记录/草稿/占用释放测试 |
| B19 | 通过（A） | 同预约重试与跨预约事件复用测试 |
| 真实支付平台 | 未执行（不在范围） | 未配置或调用真实收单平台，不采集卡资料 |
| M4 真实模型预约工具 | 未执行 | 按 Review 边界保持 M1 只读工具集合 |
| M4 真人语音预约/付款 | 未执行 | 本轮没有把 A 层测试冒充 B/C 层验收 |

## 已知限制

- 本地模拟付款按钮只用于开发验收，不代表真实资金结算或支付回调可信度。
- 完整摘要实际播完、自然语言确认授权、打断和旧连接结果时序仍属于 M4。
- FastAPI/Starlette 测试有两个已知上游弃用警告，不影响本轮断言。
