# 绩效全链路协同 Agent · 执行清单

> 定位：不是单纯算分机器人，是面向企业、打通飞书组织架构的绩效全链路协同 Agent。
> 原则：AI 只做统计、校验、提醒、辅助诊断，终审权留给人。全程可追溯、可审计、可降级。

## P0（不做就不算全链路 Agent）
- [x] P0-1 互评编排：补 peer 节点分发/回收/逾期催办进 scheme_workflow
  - 新增 PeerReview 表（task_id/record_id/被评人/互评人/分数/评语/状态/截止）
  - 分发：主管或 HR 指定互评人（同部门/跨部门），去重幂等
  - 回收：互评人提交，校验权限+截止，写入审计
  - 状态机：filling → self_done → peer_reviewing → peer_done → manager_review → …
  - 逾期：notification N+催办每日频控，主管可见回收进度
  - 验收：pytest test_peer_workflow + 前端回收进度条
  - 已落地：PeerReview 模型+0028迁移+peer_service+schemes路由+N12/N13+主管评分回收门禁；验收：pytest test_peer_workflow（3 例全绿）
- [x] P0-2 分布质检：同部门均值/方差/松紧度/异常分标记，只提示不改分
  - 新增 distribution_qc service：mean/std/z-score，松/紧阈值
  - 异常分标记 |z|>2，强制分布对比（reference_only）
  - 结果写入 AiReview（RULE），不写 Score
  - 已落地：backend/app/services/distribution_qc.py（只读 + 不写 Score）+ GET /v1/ai/reviews/distribution（可写 AiReview 审计）；分组口径=同部门+同岗位+同冻结方案，<5 人不出均值；验收：pytest test_distribution_qc（4 例全绿）
- [x] P0-3 智能核验：申诉单自动拉指标+历史+规则出中立报告
  - 实现 VerificationReport 生成：MetricValue + Score.trace + 历史 Score + scheme payload
  - 报告只读，不可篡改（已有 immutable listener）
  - 已落地：review_center.generate_report 增加跨周期历史对比（_cross_period_history）；新增 auto_generate_report + 受理后自动产出（失败不阻断受理）；验收：pytest test_verification_report（4 例全绿）

## P1（流程闭环）
- [x] P1-1 否决/联动规则：红线否决 + 联动扣分 + 封顶保底通用化
  - 已落地：backend/app/services/scheme_rules.py（veto grade/zero/cap + linkage total/指标级 + cap_floor）+ 接入 review_service.compute_score（唯一算分处，规则来自冻结快照可复算）；验收：pytest test_scheme_rules（11 例全绿）
- [x] P1-2 周期调度：自动开启 / 截止倒计时 / 逾期告警（scheduler + N 通知）
  - 已落地：period_scheduler（start_due_tasks/ensure_records/reminder_plan/deliver_reminders）+ period_scheduler_runner（后台线程，配置开关 PERIOD_SCHEDULER_ENABLED）+ N14 逾期场景 + GET/POST /v1/performance/scheduler/{status,run}（HR 专属）；顺带修 notification_service.enqueue 无法区分「新建/已存在」的缺陷（新增 enqueue_new，保证每日频控真实生效）
  - 验收：pytest test_period_scheduler（9 例全绿）
- [x] P1-3 执行类指令：催办/导出/统计走 tool_token 写入口 + 二次确认
  - 已落地：exec_tool_service（prepare→confirm 两阶段，一次性短时效令牌，绑定工具+用户）+ exec_tools 路由（exec_remind / exec_export / exec_stats）+ 授权键 exec:<tool> 与只读工具隔离 + 每步写 AiToolCall 审计；导出/统计走 privacy.redact_obj
  - 验收：pytest test_exec_tools（11 例全绿）
- [x] P1-4 代理授权：主管请假委托表 + 有效期 + 审计（delegation 表）
  - 已落地：Delegation 模型 + 0029 迁移 + delegation_service（direct_reports/department/explicit 三种范围，**不放大委托人权限**）+ 接入 access_control（AccessContext.delegated_employee_ids，can_access_employee/can_view_score_of）+ /v1/delegations 路由；有效期为硬闸门，到期自动失效，无需后台撤销任务
  - 验收：pytest test_delegation（14 例全绿）
- [x] P1-5 数据源白名单：可读源/脱敏字段 HR 可配（config 表 + privacy 联动）
  - 已落地：DataSourcePolicy 模型 + 0030 迁移（初始化 attendance/feishu_approval/manual_upload 三条默认策略）+ data_source_service（**默认不信任**：未配置的源不可读、不可进算分集；角色可读 hr/manager/department_leader/employee；allowed 开关控制算分入口；redacted_fields 字段级脱敏，叠加在 privacy.redact_obj 之上）+ /v1/data-sources 路由（仅 HR 可写）+ 接入 agent_tool_service.load_evidence（按真实角色集合过滤 + 逐源脱敏）
  - 验收：pytest test_data_source（13 例全绿）+ test_agent_business_tools 证据白名单回归全绿

## P2（智能与复盘）
- [x] P2-1 评语语义质检 + 风险识别 + 面谈提纲生成（仅建议，进 RatingDraft/CommentDraft）
  - 已落地：semantic_qc 服务（RULE_ONLY 确定性规则，R-CMT-000~006 语义质检 / R-RSK-001~006 风险识别 / 面谈提纲生成，每条带 ruleId+basis+confidence）+ /v1/semantic-qc/records/{id} 路由（权限闸门走 access_control）+ semantic_qc.py 服务层**不引用任何业务表**，只产出建议
  - 验收：pytest test_semantic_qc（27 例全绿）+ test_semantic_qc_api（9 例全绿，含「调用前后 Score / confirmed_rating / 草稿状态完全不变」只读断言）
- [x] P2-2 趋势复盘报表：跨周期趋势 / 岗位横向 / 风险清单 / 薪酬导出（脱敏）
  - 已落地：trend_report 服务（build_period_trend 跨周期趋势 / build_position_comparison 岗位横向含 **小样本保护 <5** / build_risk_roster 风险清单 / build_payroll_export 薪酬导出**默认不导出姓名**且声明「不构成调薪承诺」）+ /v1/trend-report 路由（趋势走 access_control、风险清单限 HR/部门负责人、薪酬导出**仅 HR**）；全程只读
  - 验收：pytest test_trend_report（24 例全绿，含权限闸门与「调用前后业务表完全不变」断言）
- [x] P2-3 案例库 + A/B 灰度回流 Prompt（ai_feedback + prompt_version 灰度）
  - 已落地：AiFeedback / PromptVersion / CaseLibraryEntry 三个模型 + 0031 迁移 + feedback_service（record_feedback 反馈闭环、upsert_prompt_version、resolve_prompt **确定性分桶** sha256(record_id|agent|version)、compare_rollout **只对比人工反馈且样本 <30 不下结论**、publish_case **强制脱敏且只收已确认评级**）+ /v1/feedback 路由（Prompt 管理与灰度对比仅 HR、案例库全员可读、收录限 HR/主管）
  - 验收：pytest test_feedback_service（29 例全绿）+ test_feedback_api（12 例全绿）

## 收尾
- [x] 全量回归验证：pytest + 前后端联调
  - 后端 pytest 全量：**2096 passed / 22 skipped / 0 failed**（105s）
  - 路由注册核验：OpenAPI 共 414 条 path，本轮新增 20 条全部在线（data-source / semantic-qc / trend-report / feedback / delegation / exec-tools）
  - 迁移链核验：0029→0030→0031 三条新迁移均可执行，schema drift guard 全绿
  - 契约锁定：新增 test_api_contract_p2（34 例）—— 钉死本轮端点注册 + `{"code":0,"data":...}` 信封
  - 前端联调：新增 `frontend/src/api/p2.ts` 客户端（22 个端点，路径按 `/api` → `/v1` 代理约定）；`vue-tsc --noEmit` 零错误、`npm run build` 成功（2118 模块）、`npm run test` **251 例全绿**

## 前端接入（第二轮）
- [x] P2-1 语义质检 + 面谈提纲 → 主管评分弹窗
  - 新增 `frontend/src/components/SemanticQcPanel.vue`
  - 嵌入 `frontend/src/views/performance/ManagerReview.vue`：面板放在**评价意见上方**（主管写评语时就能看到问题，而不是写完再回来找）
  - **只读**：组件内无任何 `http.post/put/patch/delete`，无「AI 改写评语」入口 —— 人保留最终决定权
  - 每条问题/风险强制展示 `basis`（判断依据）与 `ruleId`；免责提示默认折叠但**常驻 DOM**
- [x] P2-2 趋势复盘报表 → 绩效管理工作台
  - 新增 `frontend/src/components/TrendReportPanel.vue`（跨周期趋势 / 岗位横向 / 风险清单三页签）
  - 嵌入 `frontend/src/views/performance/PerformanceDashboard.vue`，整体默认折叠（它是分析工具，不是主流程）
  - **小样本保护前端再挡一道**：后端返回 `smallSample` 时前端不渲染明细表 —— 不能只靠后端兜底
  - 风险清单加载失败**明确提示**（可能无权限），不静默显示空表
- [x] P2-2 薪酬建议导出 → 新建「绩效分析」页（HR）
  - 新增 `frontend/src/components/PayrollExportPanel.vue` + `frontend/src/views/system/PerformanceAnalytics.vue`
  - 路由 `/system/performance-analytics`，菜单挂在系统管理端
  - **默认不导出姓名**，需 HR 显式勾选；免责说明随 CSV 文件一起走（脱离系统上下文后仍有效）
- [x] P2-3 反馈闭环 + Prompt 灰度 → 主管评分弹窗 + AI 控制中心
  - 新增 `frontend/src/components/FeedbackPanel.vue`
  - 主管评分弹窗内嵌「AI 草稿反馈」（采纳/修改后采纳/拒绝），**只落 `ai_feedback`，不改分数与等级**；反馈失败不阻断评分提交
  - AI 控制中心新增「Prompt 灰度」页签（仅 HR）：版本配置、灰度比例、确定性分桶、版本对比
  - 样本不足时**如实显示「样本不足」**，前端不自己下结论
- [x] P2-3 案例库 → 复核中心 + 绩效分析页
  - 新增 `frontend/src/components/CaseLibraryPanel.vue`
  - 嵌入 `frontend/src/views/dispute/ReviewCenter.vue`：**全员可读**，收录仅主管/HR（`caps.canManage || caps.isHr`）
- [x] P1-5 数据源白名单 → AI 控制中心
  - 新增 `frontend/src/components/DataSourcePolicyPanel.vue`
  - 嵌入 `frontend/src/views/system/AiManagement.vue` 新增「数据源白名单」页签（放在 AI 控制中心而非系统设置 —— 它决定的是「哪些数据能进 AI 证据集」）
  - 「进入算分集」与「谁可读」分成两个独立配置；脱敏字段明示为「读出时打码，不是删除字段」

### 前端接入的缺陷修复
- **查询参数名 camelCase → snake_case（静默失效类）**
  - 现象：前端 `p2.ts` 发 `departmentId` / `includeNames`，后端 `Query(...)` 声明的是 `department_id` / `include_names`
  - 危害：FastAPI **不报错**，而是当成「没传这个参数」—— 筛选条件被静默丢弃，页面显示「全部部门」而用户以为筛选生效了
  - 修复：`p2.ts` 显式映射为 snake_case；新增 `test_frontend_query_param_names_are_accepted`（8 例）+ `test_frontend_camelcase_params_are_not_sent` 钉死
- **周期格式化复用 `@/utils/period`**
  - 现象：TrendReportPanel 手写 `slice(0, 4)` 拼周期，被 `period.test.ts` 的守卫测试拦下
  - 修复：改用 `formatPeriodLabel`（`202611` → `2026年11月`，比手写的 `2026-11` 更适合中文界面）

### 前端验收
- `vue-tsc --noEmit`：零错误
- `npm run build`：成功，无动态导入告警
- `npm run test`：**274 例全绿**（新增 `p2-frontend-integration.test.ts` **23 例**）
  - 测的是**安全不变量**而非渲染细节：免责提示常驻 DOM、语义质检面板无写回入口、小样本前端二次拦截、薪酬导出默认无姓名且说明随文件走、反馈不改分数、缺分记录显式标注
- 后端 pytest 全量回归：**2117 passed / 22 skipped / 0 failed**（105s）

## P2 跨端联调验证（第三轮）
> 目的：pytest 全绿 ≠ 联调通过。pytest 是「进程内 + 依赖覆盖 + 内存 SQLite」，
> 它测不到**路由真实挂载**、**查询参数名对不上**、**迁移没跑**、**真实角色下的权限表现**。

- [x] 环境对齐：迁移补齐 + 服务重启
  - **发现 1**：真实开发库 `backend/data/perf.db` 停在 `0027`，P0~P2 的 4 条迁移（0028~0031）**从未执行** → `ai_feedback` / `prompt_version` / `case_library_entry` / `data_source_policy` 四张表全缺
    - 已用 Alembic 补齐（需显式 `DATABASE_URL` + `ALEMBIC_EXPECT_TARGET` 双确认，防打错库）
    - 种子数据核对：`attendance` 角色范围含 `employee`（与用户确认口径一致）
  - **发现 2**：运行中的后端进程是 13:07 启动的**旧代码**，OpenAPI 里**一个 P2 端点都没有** → 重启后 11 个 P2 端点全部在线
- [x] 真实 HTTP 打真实服务：`backend/scripts/verify_p2_crossend.py`（**69 例全绿**）
  - 真实登录拿 token（admin/manager/leader/employee 四个账号），逐个端点验响应信封 + 权限闸门
  - ★ 验证「提交反馈**不改**记录」：提交前后比对 `confirmed_rating` / `manager_comment` 完全不变
- [x] 代理链路验证：`backend/scripts/verify_p2_proxy_chain.py`（**14 例全绿**）
  - 打**前端自己的源**（5180），走 Vite `/api` → `/v1` 完整代理，验的是「前端真正会走的那个 URL」
  - 验完 10 个 P2 端点 + 未登录拦截（代理不绕过鉴权）
- [x] 浏览器实测：登录 admin → 新建「绩效分析」页 → 三个页签渲染正常 → 薪酬导出查询 → 截图核对

### ★ 跨端联调发现并修复的缺陷：趋势报表吞掉「已确认评级但未算分」的记录
- **现象**：薪酬导出返回 **0 行**，风险清单返回 **0 条**，趋势只有 0 个周期 —— 但库里明明有 21 条已确认评级的记录
- **根因**：`submit_manager_review` 明确允许「算分失败**不回滚**评分」（主管工作已完成，不该因配置问题让主管白填），于是存在「有 `confirmed_rating` 但没有 `Score` 行」的记录；而 `trend_report` 四个端点全部用 `JOIN(Score, ...)`（**INNER JOIN**）
- **危害**：这类记录在趋势 / 岗位横向 / 风险清单 / 薪酬导出里**整条消失**。HR 看到的不是「这批人还没算分」，而是「这批人不见了」—— 少掉的人就没人调薪了
- **更糟的是**：`RISK_CONFIRMED_WITHOUT_BASIS`（"已确认评级缺少指标依据"）这个风险信号被写成了**死代码** —— 它恰好要检查「有评级但没依据」的记录，而 `if not ratios: continue` 恰好在它之前就短路了
- **修复**：
  - 四个端点 `JOIN(Score)` → `outerjoin(Score)`，缺分记录以 `total=None` 出现（**不编造 0**）
  - 岗位横向：人数**计入**缺分的人（人数是真实的），但均值/排名**只由有分的人算**（没分就没有分）
  - 风险清单：把「已确认评级缺依据」的判断**提到 `ratios` 短路之前**，死代码复活
  - 薪酬导出：新增 `unscoredCount` + 每行 `scored` 标记，免责说明在有缺分时**追加警告**（"其系数仅为等级映射值，结算前须补齐算分与举证"）
  - 前端：缺分单元格显示橙色「未算分」而非留空（留空在 Excel 里像 0），CSV 增加「算分状态」列
- **修复效果（真实库实测）**：薪酬导出 0 → **21 行**（全部标记缺分 + 警告）；风险清单 0 → **21 条**（正确报出 `RISK_CONFIRMED_WITHOUT_BASIS`）；趋势 0 → **12 个周期**
- **回归保护**：新增 `backend/app/tests/test_trend_report_data_integrity.py`（**11 例**）+ 跨端脚本 7 例断言 + 前端 2 例

### 跨端验收
- 后端 pytest 全量：**2121 passed / 26 skipped / 0 failed**（105s）
- 前端 `npm run test`：**274 全绿**；`vue-tsc --noEmit` 零错误
- 跨端脚本：`verify_p2_crossend.py` **69/69**、`verify_p2_proxy_chain.py` **14/14**

## 新增 API 一览（本轮）
| 端点 | 权限 | 性质 |
|---|---|---|
| `GET/PUT /v1/data-sources` | 读全员 / 写仅 HR | 白名单配置 |
| `GET /v1/semantic-qc/records/{id}` | 本人/主管/HR | 只读建议 |
| `GET /v1/trend-report/trend/{employee_id}` | 本人/主管/HR | 只读报表 |
| `GET /v1/trend-report/position` | 主管/部门负责人/HR | 只读报表（<5 保护） |
| `GET /v1/trend-report/risk-roster` | 部门负责人/HR | 只读名单 |
| `GET /v1/trend-report/payroll-export` | **仅 HR** | 脱敏导出 |
| `POST/GET /v1/feedback` | 提交=本人/主管；列=HR | 反馈闭环 |
| `GET/PUT /v1/feedback/prompts` | **仅 HR** | 灰度配置 |
| `GET /v1/feedback/prompts/resolve` | 仅 HR | 确定性分桶 |
| `GET /v1/feedback/prompts/compare` | 仅 HR | 人工反馈对比 |
| `GET/POST /v1/feedback/cases` | 读全员 / 收录=主管/HR | 脱敏案例库 |
| `GET/PUT /v1/delegations*` | 本人 + HR | 代理授权 |
| `POST /v1/exec-tools/*/prepare|confirm` | 授权键 exec:* | 两阶段确认 |

## 前端接入（第三轮）：P0/P1 六项能力补齐
> 上一轮只补了 **P2** 的前端接入。P0-1/P0-2/P0-3/P1-2/P1-3/P1-4 六项
> 当时**只有后端**，前端完全空缺 —— 能力存在但用户点不到。

- [x] P0-1 互评 → 填报页 + 主管评分页
  - 新增 `frontend/src/components/PeerReviewPanel.vue`
  - 嵌入 `SchemeFilling.vue`（被评人提交互评）与 `ManagerReview.vue`（主管回收进度）
  - 三种角色态：互评人（填分+意见）/ 主管（进度条+分派人）/ HR（逾期催办）
  - 意见**最短 5 字**，与后端规则一致；**无改分入口**
- [x] P0-2 分布质检 → 主管评分页
  - 新增 `frontend/src/components/DistributionQcPanel.vue`，嵌入 `ManagerReview.vue`
  - **只读观察面板**：无任何写接口、无"通过/不通过"结论词
  - 后端 `limitations` **原样透出**（小样本保护、不可横向比较等前提必须让用户看见）
  - 小样本时显示「—」占位，**不展示 0**（0 会被误读成"均值是 0"）
- [x] P0-3 智能核验 → 复核与校准中心
  - 新增 `frontend/src/components/VerificationReportPanel.vue`
  - `ReviewCenter.vue` 里**替换掉原始 JSON 展示**（`pretty(r.content.facts)` → 结构化面板）
  - **不作裁决**：不显示"申诉成立/不成立/应予调整"，历史只作"核查线索"
- [x] P1-2 周期调度 → 工作日规则页
  - 新增 `frontend/src/components/SchedulerStatusPanel.vue`，嵌入 `WorkdayRules.vue`
  - **非 HR 不可见**（静默降级，不报错）；手动触发必须二次确认
- [x] P1-3 执行类指令 → 绩效管理工作台
  - 新增 `frontend/src/components/ExecToolConfirmDialog.vue`，嵌入 `PerformanceDashboard.vue`
  - **严格两阶段**：阶段一只出计划（无 confirm 按钮），阶段二携一次性令牌执行
  - 完整回显**受影响对象**（含姓名），不允许只显示一个数字
- [x] P1-4 代理授权 → 系统管理工作台
  - 新增 `frontend/src/components/DelegationPanel.vue`，嵌入 `SystemDashboard.vue`
  - **不提供续期绕过**（到期即失效是硬闸门，续期 = 重新创建一条委托）
- [x] 新增 `frontend/src/api/p0p1.ts`：六组 API 客户端（路径按 `/api` → `/v1` 代理约定）

### ★ 补齐后端缺口：执行类令牌的前端获取入口
- **现象**：`/v1/exec-tools/*` 网关只认**工具令牌**（`Authorization: Bearer <tool_token>`），
  且令牌必须带 `exec:<tool>` 授权键。这个令牌原先**只能由问答链路在服务端内部签发** ——
  前端页面拿不到，P1-3 只有后端、前端接不上。
- **修复**：新增 `POST /v1/exec-tools/token`（`backend/app/routes/exec_tokens.py`）
  —— 登录会话换**短时效（5 分钟）、只含本次工具授权、绑定用户+任务**的执行类令牌。
- **顺带修掉一个越权**：`exec_remind` 原判据用 `can_view_score_of(user.id)`，
  而**任何在职员工都能看自己的分** —— 等于人人都能拿到催办令牌。
  已改为要求**真实管理范围**（直属下属/受托人/所管部门/HR）。

### 前端验收
- `vue-tsc --noEmit`：零错误
- `npm run test`：**318 例全绿**
  - `p0p1-frontend-integration.test.ts` **33 例**（源码级安全不变量）
  - `p0p1-panel-rendering.test.ts` **12 例**（**SSR 真渲染** —— 断言最终 DOM 里出现的字）
- 后端 pytest 全量：**2132 passed / 26 skipped / 0 failed**
- 新增 `backend/app/tests/test_exec_token_issue.py` **9 例**（令牌签发的权限边界）

### P0/P1 跨端联调验证（第四轮）
- [x] 新增 `backend/scripts/verify_p0p1_proxy_chain.py`（**21 例全绿**）
  - 打**前端自己的源**（5180），走 Vite `/api` → `/v1` 完整代理
  - 验六个能力的端点可达 + 互评清单结构 + 分布质检 `limitations` 非空
    + **执行类指令完整链路**（会话 → 工具令牌 → prepare）
  - ★ 关键断言：**只带会话令牌调执行网关会被拒（401）** —— 证明两阶段不是摆设
- [x] 浏览器实测（manager 登录）
  - 主管评分弹窗：**互评进度与意见**（P0-1）+ **分数分布与核查线索**（P0-2）均正常渲染
  - 绩效工作台：「催办」「导出」按钮 + 执行确认弹窗**阶段一只有"生成执行计划"**、无确认执行
  - 系统管理工作台：**代理授权**面板（我委托出去的 / 委托给我的 / 新建委托）
  - 工作日规则页：**考核周期调度运行态**（「周期调度未启用」+ 说明文案）
  - ★ 路由权限实测：manager 访问 `/system/dashboard` 被拦到「当前账号无权访问该页面」

## P3-2 · 真实大模型接入（第四轮补充）
> 此前 `model_mode` 只在 config 里定义、**没有任何地方读它** ——
> 六个智能体跑的是 `echo` 确定性假模型（不联网）。

- [x] OpenAI 兼容客户端（**唯一**新增的厂商相关代码）
  - 新增 `agent-service/app/openai_client.py`（~330 行）
  - 只用「Chat Completions + function calling」这一**最通用子集** —— 不碰任何厂商专有扩展
  - DeepSeek / 通义 / GLM / Kimi / 本地 Ollama **同一套代码，只改配置**
  - 安全：API Key **只出现在 Authorization 头**；异常信息与日志**绝不带 Key**
- [x] 接线：`main.py::_build_client()` 加 `openai` 分支
  - 配置不全**自动退回 echo**，不阻塞服务启动
  - `/v1/agent/reload` 热切换（带鉴权，R12 加固过）—— 换模型不用重启
- [x] 形状契约：`base.py` 传 `schema` + `RatingItem` 类型化
  - ⚠️ `RatingItem` 里**没有 score 字段** → 模型在**类型层面**就给不出总分
- [x] 测试 31 例
  - `test_openai_client.py` **26 例**（Mock HTTP，**不烧真 token**）
  - `test_model_integration.py` **5 例**（真模型；**无 Key 自动 skip**，不影响 CI）
- [x] 真模型端到端验证脚本 `backend/scripts/verify_model_e2e.py` —— **18/18 全绿**
  - 打**真实 HTTP**：草稿 / 单独评级 / 后端→智能体→模型→工具回调 三条链路
  - ⚠️ **安全断言**：输出里不许出现 `score` / `total` / `总分`
- [x] 验收
  - agent-service 全量：**105 passed / 0 failed**
  - backend 全量：**2136 passed / 30 skipped / 0 failed**
  - ruff：**All checks passed**
  - 选型：DeepSeek `deepseek-flash`（OpenAI 兼容 + **function calling 实测通过**）

### ★ 真模型暴露的 3 个坑（Mock 测试一个都发现不了）
> 这是本次最有价值的产出 —— **只写 Mock 测试的话，这三个坑会全部漏到线上。**

1. **DeepSeek 把 JSON Schema 本身当成答案吐回来**
   - 现象：评级建议返回 `{"title":"RatingOutput","properties":{...}}` 而不是 `{"ratings":[...]}`
   - 根因：把 Pydantic 的 `model_json_schema()` **原文**塞进 prompt ——
     Schema 里的 `title`/`properties` 是描述性字段，模型以为「你要的就是这个 schema」
   - 修复：新增 `_schema_shape_hint()` —— 只取字段名与类型，生成**具体示例**传给模型
   - 💡 跨厂商时，**示例**比 **Schema** 可靠得多
2. **Schema 里的 `$ref` 不解析 → 示例全是 `null`**
   - Pydantic 的子模型是 `{"$ref": "#/$defs/RatingItem"}` → 不解析就返回 `None`
   - 修复：`_example_for_type()` 加本地 `$ref` 解析
3. **`ratings: list[dict[str, Any]]` 是不透明类型** —— Schema 说不清里面该有什么
   - 修复：新增 `RatingItem`（`dimension`/`level`/`reason`/`evidence_ids`）
   - ⚠️ **顺带的安全收益**：`score` 在类型层面就不可能存在

---

## P3-2 续 · 真模型幻觉引文压测（第五轮补充）
> 2026-11-20 · DeepSeek `deepseek-flash` · **22 个真实历史用例 × 3 轮**
> 📄 完整报告：`docs/21-真模型幻觉引文压测报告.md`

- [x] 用真实数据压测，不用合成数据
  - `extract_pressure_cases.py` 从真实库抽 22 条有指标 + 有证据的考核记录
  - ⚠️ 数据模型坑：`evidence.record_id` **全库 NULL** —— 证据按 `employee_id` 挂，join 走 `employee_id`
- [x] **先证明检测器是准的**（否则「检出率 0%」毫无意义）
  - `check_hallucination_detector.py` —— **33 个人工构造的已知案例**，每个写明期望结果
  - 每发现一个误报就补一条用例；自检从 **6/10 → 33/33**
  - 💡 **本次最大教训**：压测第一版结论**全是错的**（编造数字 100% 误报、引文 22.7% 误报）
  - ⚠️ **第四轮又抓出 3 个新误报**：带时间戳的日期 / 不等式区间 / `attendance:1` 占位符
  - 📌 命名：原名 `test_hallucination_detector.py` 撞了仓库护栏 `test_no_orphan_test_dirs.py`
    （叫 `test_*.py` 就必须是 pytest 测试）→ 按约定改名为 `check_*.py`
- [x] 多轮压测（模型非确定性，单轮无统计意义）
  - `pressure_test_hallucination.py`（单轮）+ `pressure_test_multi.py`（多轮统计）
- [x] ❌ **压测全失败却报「100% 干净」** —— 已修
  - 根因：shell 残留旧 `AGENT_SHARED_SECRET`（25 字符）≠ `.env`（43 字符）→ 全部 401
  - ⚠️ **失败的用例不会被检查 → 不计入检出 → falsely 显示 100% 干净**
  - 修法：① 失败时带响应体 ② 汇总先判失败、不给结论 ③ 报告写 `"valid": false` + 退出码 2
- [x] **查出 3 个真缺陷并修复**（`agent-service/app/sanitize.py`，输出侧确定性清洗）
  - **分数泄漏**（10 处/3 轮）：模型把 `impact` 里的「个人绩效分：95」抄进评级理由
    - ⚠️ **不能靠提示词解决** —— 分数**确实在原文里**（`_build_impact()` 写的），模型是忠实引用
    - 修法：删「分数 + 数值」片段，保留「完成率 100%」与合规免责；**括号配平**（实测踩过）
  - **编造考勤引用 ID**（3 例/3 轮）：`attendance-2026-11-14` —— 考勤没有 ID，模型为格式统一自造
    - 修法：归为独立类别 `attendance_placeholder`（体验问题，非「非法证据引用」）
  - **编造引文并加引号**（1 例/**第三轮才出现**）：原文只说「问题均已闭环」，
    模型却写「继续坚持『**问题当天处理完毕**』的做法」—— ⚠️ **最隐蔽的一类**：
    听起来完全合理，主管不会怀疑，而加引号等于暗示「这是原话」
    - 修法：`strip_fabricated_quotes()` —— 判定口径与检测器一致（原文有 / 锚定词 ≥2 = 改写），
      判定为编造则**去掉引号、降级为普通陈述**（⚠️ 不删内容；无原文时完全不动）
    - 💡 **这条只有多轮压测能发现** —— 单轮 22 次调用一次都没碰到
- [x] 验收
  - 检测器自检 **35/35**；清洗器单测 **18 + 7**（后者用**真实模型输出**做夹具）
  - 最终结果（**22 用例 × 3 轮 = 66 次真实调用**，**3 轮全部 valid=true、请求失败 0**）：
    **编造引文/事实/数字 全 0**；分数泄漏 **0**；考勤被当扣分 **0**；非法证据引用 **0**
  - 零问题用例占比 **95.5% / 95.5% / 100%**；残余 2 例 = **考勤占位符**（体验问题，非幻觉）
  - 回归：agent-service **130 passed**（105→130）；backend **2144 passed / 0 failed**；
    ruff **All checks passed**；**本地 CI 8/8 全绿**

- [x] **顺带修掉 ruff 门禁会拦的存量问题**（CI 跑 `ruff check .`，会直接挂）
  - ❌ **F821 `access_control` 未定义**（`exec_tool_service.py` 两处）—— ⚠️ **真运行时 bug**：
    `access_control.PermissionAction.VIEW_ALL_SCORES` 从没在模块级导入过
    → 走到「部门统计」分支就 **NameError**。已修（模块级 `from . import access_control`，
    并验证双向导入顺序**无循环导入**）
  - ⚠️ **命名撞护栏**：`test_hallucination_detector.py` 撞 `test_no_orphan_test_dirs.py`
    （叫 `test_*.py` 就必须是 pytest 测试）→ 改名为 `check_hallucination_detector.py`
  - 其余 E402 / F401 / F811 / F841 / I001 共 18 处 → 已清零
- [x] **修掉 API 契约严格模式不通过**（CI 硬门槛）
  - 现象：`❌ 存在 2 个缺失端点` —— 前端调 `POST /exec-tools/{tool}/prepare|confirm`，
    后端只有硬编码的 `/exec_remind/prepare` 这种**逐工具**路径
  - ⚠️ 这不只是路径对不上 —— **前端一调就是 404**，执行类指令整条链路走不通
  - 修法：后端**补参数化别名** `/{tool}/prepare` 与 `/{tool}/confirm`，
    按 tool 分发到**同一份 service 实现**（行为与硬编码路径完全一致），
    带工具名的老路径保留兼容
  - 补 4 条测试（别名等价 / 未知工具 404 / 跨工具令牌仍拒 409 / prepare 无副作用）
  - 契约复核：前端 330 端点 → **330 全对齐，缺失 0**

### ★ 压测暴露的真模型能力边界
| 能力 | 结论 |
|---|---|
| 引用证据 ID | ✅ 正确（`[evidence: dc3c789f-...]`） |
| 复述审批意见 | ✅ 正确（「成果内容与证据符合要求，同意通过」） |
| 写免责 | ✅ 正确（「考勤异常不自动扣分，仅作参考」） |
| 自己算比例 | ✅ 不会（`ratio`/`ratio_level` 由业务层算好再给） |
| 复述原文里的分数 | ⚠️ **会** —— 需清洗兜住 |
| 给无 ID 的考勤编占位符 | ⚠️ **会** —— 需清洗兜住 |

- [ ] **工具层 Step 6 计划助手工具化** —— 本轮未做（用户明确「先做测压再做 step6」）

---
执行方式：按 P0→P1→P2 顺序，一项一验收，不跳项。每项含：模型/迁移/服务/路由/通知/测试。
