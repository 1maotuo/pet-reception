# 宠物美容组合阶段 B/C 证据（2026-09-28）

## 基线与提交

- 阶段 A 基线：`c317f50`（A 的代码提交为 `d2ade65`）。
- 阶段 B：`beca42c feat(grooming): freeze service combinations and deposits`。
- 阶段 C：`d9c28f5 feat(grooming): connect quotes to realtime frontend`。
- 没有进入 M5，没有清空开发数据库，没有删除历史预约，也没有升级 `qwen-audio-agent@1.11.0`。

## 已实现

- 草稿持久保存带单位体重和附加项目集合；省略、空数组、清除和同值更新语义明确。
- 排班首次查询、替代日期、旧查询复核和 prepare 都使用同一组合计算出的服务时长。
- 目录组合变化进入既有 schedule revision；体重/附加项变化保守增加 availability revision。
- prepare 在事务内重算组合并冻结报价、付费附加项、时长与预约金；摘要哈希覆盖这些事实。
- 新结构化组合使用服务端配置的固定正数预约金；模拟支付请求不接收金额，支付后记录冻结金额与币种。
- 客户页显示组合报价、付费附加项、预约金、支付期限和“预约已确认，预约金已支付”状态；旧记录明确显示历史金额未记录。
- 同一实时 Session 增加第七个 `quote_service` 只读工具；它不读写草稿，也不进入写入结果不确定分支。
- 受控摘要缺付费附加项、播错报价或播错预约金时不能取得 presented 回执。

## 可核对示例（synthetic/test 数据，不是正式商家政策）

输入：犬、35 lb、测试犬类理发套餐、选择刷牙，并重复提出套餐已含的剪指甲。

结果：

- 基础价：10500 美分。
- 剪指甲：已包含，0 美分、0 分钟。
- 刷牙：1200 美分、10 分钟。
- 合计：11700 美分 USD，排班时长 130 分钟。
- 固定模拟预约金：3000 美分 USD；配置说明为抵扣服务总价。

价格仅受体重影响的犬类洗澡套餐在未提供体重时仍能按 60 分钟查询空档，同时报价明确返回缺少 `weight`。猫类服务使用独立配置和猫类附加项，不套用犬类体重档。

## 实际验证

- `scripts/check.ps1`：通过。后端数据库外 57 passed / 50 skipped；Web 6 passed；Voice Gateway 69 passed；Web production build、`pip check`、Compose 配置通过。
- `RUN_POSTGRES_TESTS=1 pytest -q`：独立真实 PostgreSQL 107 passed。
- 全新临时库：从 `0001` 到 `0011` 全部迁移和 seed 通过，确认最新两版后删除临时库。
- 现有开发库：只增量应用 `0011_grooming_booking_combinations`，未清空或降级。
- `probe-m4-confirm-response-loss.mjs`：通过；确认响应丢失后复用原 operation，只产生一笔预约。
- `probe-m4-restart-recovery.mjs`：通过；presented 在网关重建后重播并重新取得播放回执，只发送一次 confirm POST。

## 未执行与商业边界

- 本轮未执行真实千问模型联网对话和真人麦克风通话；工具 schema、运行时白名单、回执和 Prompt 已由确定性测试覆盖，但不能冒充真人语音验收。
- 当前正式演示店仍没有经用户确认的新体重价格表、猫类价格、附加项目时长或预约金金额；因此没有把 synthetic 数值写入正式演示目录。其现有 legacy 服务继续原 M4 行为，新报价工具会如实返回旧价格说明/结构化价格未配置。
- 未接真实支付、税费、优惠券、退款、通知、医院、日托、RAG 或新的 Agent/工作流系统。
