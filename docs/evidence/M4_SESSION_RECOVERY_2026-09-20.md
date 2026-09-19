# M4 连续预约与 Review 修复证据（2026-09-20）

基线：`3df38211f8031ce25b054494cca1be8ff8fca0ed`。独立 Review 包 SHA-256：
`8470C41E18885D9A2B8207808EC3883CFCA6508165F8AF5CEB8686AAB21E593E`。

## 实际复现结论

- 转写现象：页面看似开始了新对话，后续出现年龄 422、已提交 409、旧日期查询和 prepare 循环。
- 日志/数据库事实：浏览器继续使用旧 `voice_session_id`；该草稿已经绑定一条已付款预约，后续更新、查询和准备仍命中同一旧草稿；模型曾把 booking ID 当 operation ID 查询而得到 404。
- 推断：网关进程重启清掉了进程内对话状态，但浏览器 localStorage 保留业务会话 ID，造成“界面像新会话、业务仍是旧会话”的错觉。旧预约和付款记录本身没有丢失或被改写。

## 修复与边界

- 网关签发新 session，后端在同一受信 owner/merchant 下创建空草稿；网页只有收到 201 才切换。自动重连和服务重启不触发新预约。
- 已提交草稿的 update/search/prepare 统一返回当前状态、正确 `operation_id` 和真实的“开始新预约”恢复入口；confirmed 会话在网关内阻断重复必败调用。
- 月龄保存为原始表达和整数月数；旧的近似年值不补造原文。电话、宠物名和服务支持物种在 PATCH、prepare、confirm 共用校验。
- 无明确日期的“最近可用下午”本阶段明确暂缓，返回缺少 `preferred_date`，不沿用旧日期。
- 未扩展改期、取消、多订单平台或真实支付；未删除旧预约、付款或占用记录。

## 结果

- 通过：后端全量真实 PostgreSQL 76 项。
- 通过：Web 6 项、Voice Gateway 39 项。
- 通过：全新 `qwen-audio-agent@1.11.0` 包应用兼容补丁。
- 通过：真实浏览器显示“开始新预约”；点击后 POST 201，localStorage 从旧 UUID 切换为后端已创建的 `booking-*` session，并重新读取新 session 的任务/时间线。
- 通过：开发库只应用迁移 `0008_m4_session_and_age_contract`，运行中后端和网关健康。
- 未执行：本次修改后的真人语音连续两笔预约、重启中断和口语月龄复测；不能由自动测试替代。

本轮没有修改 `docs/PRODUCT.md` 或 `docs/ACCEPTANCE.md`，没有降低原验收要求。
