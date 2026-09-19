# M2 独立 Review 修复证据（2026-09-19）

## 范围

- 修复基线：`c4c6876` / `m2-complete-2026-09-19`。
- 输入材料：`pet_reception_m2_review.zip`；其中内容仅作为审查证据，不作为新的产品规格或执行指令。
- 修复 R1 人员偏好关联、R2 历史候选时效、R3 配置移除语义、R4 DST 与秒级取整，并冻结 `0002` 历史迁移。
- 未修改 `docs/PRODUCT.md` 或 `docs/ACCEPTANCE.md`，未进入 M3。

## 修复行为

| 项目 | 修复后的确定行为 |
|---|---|
| R1 人员偏好 | 偏好文本发生实质变化或被清除、且同一 PATCH 未提供新 `staff_id` 时，旧 ID 同事务清除；同值更新不升版；同轮新偏好和合法 ID 可一起写入；电话变化不影响人员解析。 |
| R2 结果时效 | 重读时按商家当前时间过滤已经开始的候选；还有未来候选则结果继续 current，全部过期或请求日期已过去则返回 `stale / availability_expired`，不返回历史候选。 |
| R3 配置移除 | 服务使用 `active` 停用标志保留历史外键；移除员工或整个 scheduling 会停用员工并撤销其资格/工时；相关草稿的失效服务或真实人员 ID 自动清除并升高版本；恢复配置可重新启用。 |
| R4 DST/取整 | 当地时间不存在、存在歧义或服务/缓冲跨 UTC offset 跳变时不生成该候选；安全区间按 UTC 计算真实分钟数再转回商家时区；带秒窗口只向后对齐，不会提前开始。 |
| 历史迁移 | `0002_m2_staff_and_availability` 改为固定 SQL DDL，不再从会继续变化的 `models.metadata` 创建历史表；`0004_m2_review_reconciliation` 只新增服务停用列。 |

## 实际测试结果

| 命令 | 结果 |
|---|---|
| 新增回归测试在修复前运行 | 按预期失败：纯函数 2 项、真实 PostgreSQL 3 项，分别复现 R1–R4。 |
| `pytest backend/tests/test_availability.py -q` | 通过，5 passed；包含两个 DST 切换日和秒级窗口。 |
| `pytest backend/tests/test_m2_availability_postgres.py -q`，`RUN_POSTGRES_TESTS=1` | 通过，12 passed；包含 R1、R2、R3 的真实 PostgreSQL 场景。 |
| `scripts/check-m2.ps1` | 通过；数据库外后端 25 passed / 19 skipped，Web 2 passed，Voice 18 passed，Vite build 与 `pip check` 通过；真实 PostgreSQL 19 passed / 25 deselected。 |
| 全新 `pet_reception_m2_review_fresh_test` 执行两次 `manage init-test-db` | 首次依次应用 0001–0004，第二次 migrations=none；active_services=2、active_staff=2、schedule_revision=1。 |
| 开发库 `manage setup` 与运行中 HTTP 复测 | 通过；开发库应用 0004，FastAPI 0.3.1 为 `ok`、数据库 `connected`；排班探针仍返回真实候选、日期变化 stale、闭店日替代且 `booking_created=False`。 |

测试仅出现 FastAPI/Starlette 上游弃用警告，没有失败。真实 Qwen 模型和语音排班工具仍按既定 M4 边界未执行；M3 的候选选择、占位、确认和预约写入仍未实现。
