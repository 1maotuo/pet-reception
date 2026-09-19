# M1 独立 Review 修复记录（2026-09-19）

## 范围与基线

- 原冻结基线：`596e8257c0d3c30b51a52b6663de22f1f2e34c1d`，标签 `m1-accepted-2026-09-19`。
- 原源码 ZIP SHA-256：`cf1ac067dd9990c3e630e5b06ba301661896477f3a7c7d6bd8b0f592cf5ec537`。
- 外部 Review 包只作为待核实材料读取，保存在仓库外的 `work/incoming-review-2026-09-19`，没有覆盖产品文件。
- 本轮只修 M1 接缝，不进入 M2，不新增预约写入、数据库表、多 Agent、队列或第二套语音状态机。
- `docs/PRODUCT.md` 与 `docs/ACCEPTANCE.md` 未修改，原冻结标签未改写。

## Review 逐项处理

状态含义：**已修复、未复现、阻塞、未执行**。

| 项目 | 状态 | 实际证据 |
|---|---|---|
| R1 通用工具超出只读边界 | 已修复 | 实际 Qwen Session 只暴露 `spawn_thinking`、`cancel_agent_task`、`get_agent_task_status`，描述限定为当前商家只读查询。运行时强制调用 `memory`、`notes`、`schedule_reminder` 均返回 `tool_not_allowed`，副作用计数为 0。 |
| R2 两端商家配置源不一致 | 已修复 | FastAPI 在 `create_app()` 时按 `MERCHANT_CONFIG_PATH` 加载商家；默认 A、自定义 B、同 ID 新资料均通过，环境 ID 与文件 ID 不一致时启动明确失败。无模块导入缓存。 |
| R3 电话/时长未进入模型事实 | 已修复 | `content` 改为完整可信 JSON 事实，包含电话、地址、来源、服务 ID、时长与价格限定；短口播单独放在 presentation。真实千问回答电话、基础洗护 60 分钟、全套美容 120 分钟。 |
| R4 required 麦克风可被 deviceId 绕过 | 已修复 | 冲突的内置麦克风或 `default` deviceId 会被配置的物理 USB 设备覆盖；设备缺失或同一标签匹配多个物理设备时明确失败，不静默回退或任意选择。 |
| R5 取消信号未进入 HTTP | 已修复 | 外部 signal 与 5 秒超时通过 `AbortSignal.any()` 合并；预先取消时 HTTP 请求数为 0，处理中取消会终止响应读取；500 与超时仍明确抛错。 |
| R6 重连重复问候 | 已修复 | 真实浏览器先复现同一会话累积多次问候；修复后按可信 `ownerId + sessionId` 在网关生命周期内去重。同 session 页面刷新重建 WebSocket 没有新增问候，新 session 只新增一次问候。没有新增重连器。进程重启会重置内存去重表，仍作为已知边界。 |

## 实际自动测试

锁定环境：Windows、Node 24.15.0、npm 11.12.1、Python 3.12 项目虚拟环境、Qwen Audio Agent 1.11.0、PostgreSQL 17.11。

| 命令 | 结果 |
|---|---|
| `npm ci` | 通过：从锁文件重新安装 267 个包，postinstall 从原版 Qwen 1.11.0 成功重建全部守卫补丁；npm audit 报告 0 vulnerabilities。 |
| `npm run postinstall` | 通过；守卫补丁成功应用到 `qwen-audio-agent@1.11.0`，重复执行仍通过。 |
| `python -m pytest backend/tests/test_app.py -q` | 通过：8 passed。 |
| `node --test voice-gateway/test/merchant-tool.test.mjs voice-gateway/test/audio-input-bootstrap.test.mjs voice-gateway/test/tool-policy.test.mjs` | 通过：10 passed。 |
| `node --test voice-gateway/test/initial-greeting-policy.test.mjs voice-gateway/test/qwen-package.test.mjs` | 通过：5 passed。 |
| `scripts/check.ps1`（系统 Node 20） | 预期失败：版本守卫报告必须使用 Node 24.15.0；未修改系统 Node。 |
| 将项目便携 Node 24.15.0 放在 PATH 前后运行 `scripts/check.ps1` | 通过：Backend 8 passed/1 skipped；Web 2 passed；Voice 18 passed；`pip check`、Vite build、Compose config 均通过。 |
| `RUN_POSTGRES_TESTS=1 ... pytest backend/tests/test_postgres_integration.py -q` | 通过：真实 PostgreSQL 1 passed。 |
| `npm run probe:tool --workspace @pet-reception/voice-gateway` | 通过：返回完整可信事实及独立短口播，商家 ID/来源正确。 |
| `git diff --check` | 通过；只有 Windows 工作区的 LF→CRLF 提示，无空白错误。 |

## 真实浏览器与模型证据

- Playwright headed 会话：`pet-reception-m1-accepted`，页面 `http://127.0.0.1:3101/`。
- 修复前页面快照显示同一历史中重复出现多条相同开场问候，确认 R6 不是纯静态猜测。
- 修复后，同一会话 `b619eae6-6030-4a8a-980b-525aa74fb116` 刷新并重建 WebSocket，网关重新连接但页面没有新增问候。
- 点击“新会话”后生成 `14a4a71a-4f5b-4ceb-ac5a-3cf7b59f77df`，页面出现一次新问候。
- 输入：`请只告诉我本店电话，以及基础洗护和全套美容各需要多长时间。`
- 工具轨迹：`work_05ca647c-e840-4141-803b-a67cb4ba7b02`，同一 session/turn 下 13 ms 完成真实 HTTP 查询。
- 模型结果：`本店电话是 +1-415-555-0142。基础洗护需要 60 分钟，全套美容需要 120 分钟。`
- 本轮浏览器联调使用文字输入与真实千问/真实 HTTP 工具，证明 B 层事实回传；没有把它冒充真人麦克风 C 层测试。

## V06 与 V08 的真实状态

| 验收项 | 状态 | 说明 |
|---|---|---|
| V06-A 确定性正常/取消/HTTP 500/超时 | 通过 | 项目回归测试覆盖快速成功、预先取消、处理中取消、HTTP 500 和可控短超时；失败不返回伪事实。 |
| V06-B/C 真实模型 + 真人麦克风的慢/失败/超时 | 未执行 | 本轮只完成正常工具的真实模型文字联调；没有对真人语音会话注入慢响应、500 和超时，不能标为通过。 |
| V08 三分钟以上重复通话 | 未执行 | 没有新增带实际计时的真人重复通话记录。 |

V08 有限复测清单（由用户操作，不把未操作项写成通过）：

1. 新建会话，开启指定 USB 耳机麦克风，连续对话至少 3 分钟；记录开始/结束时间、总轮数、一次工具查询和一次插话。
2. 再做两次新的 3 分钟会话；其中一次包含慢速分段表达，一次包含价格咨询后转预约意向。
3. 每次确认：只问候一次、没有旧会话结果、麦克风仍为 USB、插话后可继续、无假预约成功；记录任何异常与时间点。
4. 三次均完成后才能把 V08 从未执行改为通过；单次通过不替代三次记录。

## 当前运行状态

- PostgreSQL：`127.0.0.1:5432`，healthy。
- FastAPI：`http://127.0.0.1:8010`，`status=ok`、数据库 `connected`。
- React：`http://127.0.0.1:5173`，HTTP 200。
- Qwen Gateway：`http://127.0.0.1:3101`，`ready`、`voiceConfigured=true`、音频选择 `required-label / USB Audio Device`。

## 未降低标准

本轮没有修改产品规格或验收清单。新增测试把工具权限、商家配置一致性、事实载荷、取消、麦克风 required 语义和逻辑会话问候去重变成可执行约束；V06-B/C、V08 继续保留为未执行。
