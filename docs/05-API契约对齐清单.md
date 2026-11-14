# P1 API 契约对齐执行清单

> 生成时间：2026-11-14
> 适用范围：`frontend/src/api/*.ts` ↔ `backend/app/routes/*.py`
> 前置文档：[空白路由问题修复](#)（本文档 §0 有摘要）

---

## 📊 基线数据（实测，非估算）

| 指标 | 数值 | 测量方式 |
|---|---|---|
| 前端 `方法+路径` 调用对 | **263** | 正则扫描 `frontend/src/api/*.ts` |
| 后端已实现并命中 | **36** | 与运行时 `app.openapi()` 比对 |
| 后端缺失 | **227** | 同上 |
| 缺失业务组数 | **59** | 按路径前两段聚合 |
| 运行时 404 比例 | **27%**（65/237） | `backend/uvicorn.log` 实测 |
| OpenAPI 可用路径数 | **99** | `GET /openapi.json` → 200 |

---

## 🎯 目标

1. `/openapi.json` 返回 200，可作为唯一事实源
2. 引入契约校验脚本，**防止脱节再次静默积累**
3. 先用低成本的路径重命名消除缺口，再分批补齐缺失端点

---

## §0 背景：本次修复的由来

### 问题现象

点击左侧任意菜单（员工注册、员工管理、权限管理等），右侧内容区全部空白。

### 根因与修复（已完成）

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| R1 | 右侧内容区空白 | 路由懒加载 `() => import(...)` 返回 `default: undefined`，`<router-view>` 渲染出空注释 | 全部改为顶层静态 `import`（57 个） |
| R2 | 切换页面时 DOM 残留 | `withAppShell()` 自定义闭包包装路由，多个顶层路由共享 `AppShell` 实例导致 stale vnode，抛 `Cannot read properties of null (reading 'parentNode')` | 改为标准 Vue Router 嵌套路由：`AppShell` 作父级组件，页面作 `path: ''` 子路由 |
| R3 | 退出登录后框架残留 | `App.vue` 用 `v-if="$route.meta.public"` 分流两套视图 | `App.vue` 只保留单一顶层 `<router-view />` |
| R4 | 路由出口缺失 | `AppShell.vue` 内为 `<slot />` | 改为 `<router-view />` |
| R5 | 依赖反复重载 | `unplugin-vue-components` + `ElementPlusResolver` 逐个引入 CSS 触发 Vite 预构建循环 | `vite.config.ts` 的 `optimizeDeps.include` 预构建全部 element-plus 样式入口 |
| R6 | API 404 | 后端路由前缀与 Vite 代理重写不匹配 | `system.py` → `/v1/system`，`department.py` → `/v1/department` |

### 修复涉及的新增文件（**尚未提交，存在丢失风险**）

```
?? frontend/src/components/AppShell.vue    ← R2/R4 核心
?? frontend/src/router/layout.ts           ← R2 核心
```

### 遗留的测试守卫失效

`frontend/tests/routing-outlet.test.ts`（未跟踪文件）专门用于防 R1/R2，但两条断言已失效：

| 断言 | 失效原因 | 实现是否正确 |
|---|---|---|
| `路由中不应再有 () => import(...)` | 正则匹配到了**注释里的示例文字**（同文件其他断言都做了去注释，唯独这条没有） | ✅ 正确：剥离注释后动态 import = 0 |
| `应存在以 AppShell 为布局组件的路由` | `component: AppShell` 已移至 `router/layout.ts`，测试只读 `index.ts` | ✅ 正确：`layout.ts` 中 `AppShell` + `children` 均在 |

### 测试基线（已用 `git stash` 对比确认，均为既有问题）

| 套件 | 当前 | 修复前基线 | 本次影响 |
|---|---|---|---|
| 前端 | 166 通过 / 21 失败 | 20 失败 | 修复 1 个 |
| 后端 | 48 通过 / 12 失败 / 7 错误 | 47 通过 / 12 失败 / 8 错误 | 修复 1 个 |

---

## 🔧 批次 0 — 解锁契约基线 ⚠️ 前置必做

> **实测依据**：`main.py:903` 使用了 `Optional[float]`，而 `main.py:7` 存在 `from __future__ import annotations`，该注解是惰性字符串，**导入时不报错**，直到 Pydantic 生成 OpenAPI 时才抛 `PydanticUndefinedAnnotation: name 'Optional' is not defined`。

### 📌 现状复核（2026-11-14 15:01）→ ✅ 已完成

> ⚠️ **本批次大部分已完成**，但**运行中的服务器仍在用旧代码**，导致 `/openapi.json` 仍返回 500。

| # | 任务 | 文件 / 命令 | 状态 |
|---|---|---|---|
| 0.1 | 导入 `Optional` | `backend/app/main.py:10`<br>`from typing import Any, Optional` | ✅ **已完成** |
| 0.2 | `python-multipart` | `backend/requirements.txt` | ✅ **已完成**（已装 0.0.33 且已写入 requirements） |
| 0.3 | **重启后端服务器** | 杀掉旧进程并重新启动 uvicorn | ✅ **已完成**（PID 25696 → 14424） |
| 0.4 | 重新采集契约基线 | 契约脚本 / OpenAPI 比对 | ✅ **已完成**（36 命中 / 227 缺失） |

### 🔍 过程中的两个认知修正

**修正 1：服务器用的是旧代码**

| 项目 | 时间 |
|---|---|
| Uvicorn 进程启动 | 2026-11-14 **14:59:58** |
| `main.py` 最后修改 | 2026-11-14 **15:01:32** |

服务器比代码修改**早 1.5 分钟启动**，加载的是没有 `Optional` 导入的旧版本，故 `/openapi.json` 持续 500。重启后立即恢复。

**修正 2：删除 `Field(alias=...)` 这一项是多余的**

初稿曾计划删除 `main.py:903` 的 `Field(..., alias='request')`。经核对 Git `HEAD` 版本，**该 `alias=` 从未存在于源码中**——运行时警告里的 `FieldInfo(alias='request')` 是 FastAPI 自己为 body 参数生成的元数据，不是用户代码。

> ⚠️ 因此**无需修改** `ManagerReviewReq`，该警告可忽略。

### ✅ 验收结果

- [x] `curl http://localhost:8000/openapi.json` 返回 **HTTP 200**
- [x] OpenAPI 路径数 = **99**
- [x] 启动日志出现 `Import data router registered successfully`
- [x] 错误日志无 `PydanticUserError` / `not fully defined`
- [x] `ManagerReviewReq.model_rebuild()` 正常
- [x] 契约基线已重采：**36 命中 / 227 缺失**

> 📌 **批次 0 已全部完成**，可进入批次 1。

---

## 🔧 批次 1 — 路径重命名 + 契约校验脚本

### ⚠️ 1a 关键前提：改路径必须同步改响应体结构

后端 4 个 handler **统一返回** `{records, total, pageNo, pageSize}`，但前端 3 个期望**裸数组**。仅改路径仍会导致前端拿不到数据。

| # | 后端现路径 | 改为 | 后端现响应 | 前端期望类型 | 需同步改结构 |
|---|---|---|---|---|---|
| 1a.1 | `GET /department/template` | `/department/templates` | `{records,total,pageNo,pageSize}` | `DeliverableTemplate[]` | ✅ 改裸数组 |
| 1a.2 | `GET /department/standard` | `/department/acceptance-standards` | `{records,...}` | `AcceptanceStandard[]` | ✅ 改裸数组 |
| 1a.3 | `GET /department/score-rule` | `/department/score-rules` | `{records,...}` | `ScoreRule[]` | ✅ 改裸数组 |
| 1a.4 | `GET /department/todo` | `/department/todos` | `{records,...}` | `PageResponse<TodoItem>` | ✅ 结构已匹配 |
| 1a.5 | `GET /department/plan-approval` | `/department/month-plan-approvals` | `{records,...}` | `MonthPlanApprovalItem[]` | ✅ 改裸数组 |

### 📦 后端 — 路径重命名

- [ ] **1a.1** `backend/app/routes/department.py` — `@router.get("/template")` → `"/templates"`
- [ ] **1a.2** `backend/app/routes/department.py` — `@router.get("/standard")` → `"/acceptance-standards"`
- [ ] **1a.3** `backend/app/routes/department.py` — `@router.get("/score-rule")` → `"/score-rules"`
- [ ] **1a.4** `backend/app/routes/department.py` — `@router.get("/todo")` → `"/todos"`
- [ ] **1a.5** `backend/app/routes/department.py` — `@router.get("/plan-approval")` → `"/month-plan-approvals"`

### 📦 后端 — 响应体结构对齐

- [ ] **1a.6** `list_templates` — 返回值改为裸数组
- [ ] **1a.7** `list_standards` — 返回值改为裸数组
- [ ] **1a.8** `list_score_rules` — 返回值改为裸数组
- [ ] **1a.9** `list_plan_approvals` — 返回值改为裸数组
- [ ] **1a.10** `list_todo_items` — 确认字段名与 `PageResponse` 一致

### 📦 后端 — 旧路径兼容

- [ ] **1b.1** 旧路径保留并加 `include_in_schema=False`（如 `@router.get("/template", include_in_schema=False)`）
- [ ] **1b.2** 确认无其他调用方 —— 当前仅前端 `api/*.ts` 使用，可安全下线

### 📦 契约校验脚本

- [ ] **1c.1** 新建 `backend/scripts/check_api_contract.py`
- [ ] **1c.2** 读取 `app.openapi()["paths"]` 提取后端端点（**方法+路径**双维度匹配）
- [ ] **1c.3** 正则扫描 `frontend/src/api/*.ts` 提取前端调用
- [ ] **1c.4** 输出三分类：`✅ 已对齐` / `⚠️ 同组缺失` / `❌ 无对应业务组`
- [ ] **1c.5** 支持 `--json` 输出，便于 CI 消费
- [ ] **1c.6** 支持 `--allow-missing N` 白名单参数
- [ ] **1c.7** 接入 CI：退出码非 0 时阻断

### ✅ 验收标准

- [ ] 契约脚本输出：`已对齐 36 / 缺失 227`
- [ ] 重命名后，5 个 GET 从「缺失」转为「已对齐」（36 → **41**）
- [ ] 前端 `department.ts` 对应 5 个函数能正常拿到数据（不再报「暂无数据」）

---

## 🔧 批次 2 — 同组补子路径（38 个端点）

> 排序依据：按 **404 频次**优先，先解决用户最容易感知的「点了没反应」。

### 📦 system/knowledge（10）

- [ ] `GET /system/knowledge/documents`
- [ ] `GET /system/knowledge/documents/{id}`
- [ ] `DELETE /system/knowledge/documents/{id}`
- [ ] `POST /system/knowledge/documents/{id}/enable`
- [ ] `POST /system/knowledge/documents/{id}/disable`
- [ ] `GET /system/knowledge/documents/{id}/history`
- [ ] `POST /system/knowledge/documents/{id}/versions`
- [ ] `GET /system/knowledge/preview`
- [ ] `GET /system/knowledge/status`
- [ ] `POST /system/knowledge/sync`

### 📦 leader/knowledge（2）

- [ ] `POST /leader/knowledge/upload`
- [ ] `GET /leader/knowledge/uploads`

### 📦 department/export-tasks（5）

- [ ] `GET /department/export-tasks/{id}`
- [ ] `POST /department/export-tasks/{id}/check`
- [ ] `GET /department/export-tasks/{id}/download`
- [ ] `GET /department/export-tasks/{id}/download-info`
- [ ] `POST /department/export-tasks/{id}/retry`

### 📦 leader/export-tasks（2）

- [ ] `GET /leader/export-tasks/{id}/download`
- [ ] `GET /leader/export-tasks/{id}/download-info`

### 📦 employee/export-tasks（1）

- [ ] `GET /employee/export-tasks/{id}/download`

### 📦 leader/employees（3）

- [ ] `GET /leader/employees`
- [ ] `GET /leader/employees/{id}/work-items`
- [ ] `GET /leader/employees/{id}/month-plans/{planId}`

### 📦 department/employees（2）

- [ ] `GET /department/employees/{id}/work-items`
- [ ] `GET /department/employees/{id}/month-plans/{planId}`

### 📦 system/roles（2）

- [ ] `GET /system/roles/{id}/permissions`
- [ ] `PUT /system/roles/{id}/permissions`

### 📦 system/workday-rules（1）

- [ ] `POST /system/workday-rules/{id}/status`

### 📦 system/audits（1）

- [ ] `GET /system/audits/options`

### 📦 department/project-result-packages（1）

- [ ] `GET /department/project-result-packages/{id}`

### 📦 dashboard（2）

- [ ] `GET /employee/dashboard`
- [ ] `GET /dispute/dashboard`

### 📦 org-tree（2）

- [ ] `GET /department/org-tree`
- [ ] `GET /leader/org-tree`

### 📦 system/week-plan-standard-supplements（1）

- [ ] `GET /system/week-plan-standard-supplements/{id}`

### 📦 messages（1）

- [ ] `GET /messages`

### 📦 leader/workbench（1）

- [ ] `GET /leader/workbench`

### 📦 planning/stats（1）

- [ ] `GET /planning/stats`

### ✅ 验收标准

- [ ] 契约脚本输出：`已对齐 41 → 79`
- [ ] `backend/uvicorn.log` 中 404 比例从 27% 下降至 **< 10%**
- [ ] `/v1/department/org-tree`、`/v1/leader/knowledge/uploads` 等高频 404 消失

---

## 🔧 批次 3 — 核心业务域（147 个端点）

> 排序依据：按**业务闭环**排，不按模块排。同一业务链的端点一起做，避免反复返工。

### 3.1 员工计划闭环（24）

- [ ] `POST /employee/month-plans/draft`
- [ ] `GET /employee/month-plans/{id}`
- [ ] `POST /employee/month-plans/{id}/draft`
- [ ] `POST /employee/month-plans/{id}/draft/clear`
- [ ] `DELETE /employee/month-plans/{id}/items/{itemId}`
- [ ] `POST /employee/month-plans/{id}/submit`
- [ ] `GET /employee/month-plans/{id}/template-download`
- [ ] `GET /employee/month-plans/{id}/template-preview`
- [ ] `POST /employee/month-plans/{id}/withdraw`
- [ ] `GET /employee/day-plans/detail`
- [ ] `POST /employee/day-plans/draft`
- [ ] `POST /employee/day-plans/submit`
- [ ] `DELETE /employee/day-plans/{id}`
- [ ] `POST /employee/day-plans/{id}/withdraw`
- [ ] `GET /employee/week-plans`
- [ ] `POST /employee/week-plans`
- [ ] `GET /employee/week-plans/parent-options`
- [ ] `DELETE /employee/week-plans/{id}`
- [ ] `GET /employee/week-plans/{id}`
- [ ] `PUT /employee/week-plans/{id}`
- [ ] `POST /employee/week-plans/{id}/items/{itemId}/results/submit`
- [ ] `GET /employee/week-plans/{id}/items/{itemId}/results/submit-context`
- [ ] `POST /employee/week-plans/{id}/submit`
- [ ] `POST /employee/week-plans/{id}/withdraw`

### 3.2 审批闭环（26）

- [ ] `GET /leader/month-plan-approvals`
- [ ] `POST /leader/month-plan-approvals/batch-approve`
- [ ] `POST /leader/month-plan-approvals/batch-reject`
- [ ] `GET /leader/month-plan-approvals/page`
- [ ] `GET /leader/month-plan-approvals/{id}`
- [ ] `POST /leader/month-plan-approvals/{id}/approve`
- [ ] `POST /leader/month-plan-approvals/{id}/reject`
- [ ] `GET /leader/month-plan-approvals/{id}/template-download`
- [ ] `GET /department/month-plan-approvals`
- [ ] `GET /department/month-plan-approvals/page`
- [ ] `GET /department/month-plan-approvals/{id}`
- [ ] `POST /department/leader-month-plan-approvals/batch-approve`
- [ ] `POST /department/leader-month-plan-approvals/batch-reject`
- [ ] `GET /department/leader-month-plan-approvals/page`
- [ ] `GET /department/leader-month-plan-approvals/{id}`
- [ ] `POST /department/leader-month-plan-approvals/{id}/approve`
- [ ] `POST /department/leader-month-plan-approvals/{id}/reject`
- [ ] `GET /department/leader-month-plan-approvals/{id}/template-download`
- [ ] `GET /department/week-plan-approvals`
- [ ] `GET /department/week-plan-approvals/{id}`
- [ ] `POST /department/week-plan-approvals/{id}/approve`
- [ ] `POST /department/week-plan-approvals/{id}/reject`
- [ ] `GET /leader/week-plan-approvals`
- [ ] `GET /leader/week-plan-approvals/{id}`
- [ ] `POST /leader/week-plan-approvals/{id}/approve`
- [ ] `POST /leader/week-plan-approvals/{id}/reject`

### 3.3 领导点评闭环（24）

- [ ] `GET /leader/daily-reviews`
- [ ] `POST /leader/daily-reviews/batch-comment`
- [ ] `POST /leader/daily-reviews/batch-risk`
- [ ] `GET /leader/daily-reviews/{id}`
- [ ] `POST /leader/daily-reviews/{id}/comment`
- [ ] `POST /leader/daily-reviews/{id}/risk`
- [ ] `GET /department/daily-reviews`
- [ ] `POST /department/daily-reviews/batch-comment`
- [ ] `GET /department/daily-reviews/{id}`
- [ ] `POST /department/daily-reviews/{id}/comment`
- [ ] `GET /department/day-plan-reviews/{id}`
- [ ] `POST /department/day-plan-reviews/{id}/approve`
- [ ] `POST /department/day-plan-reviews/{id}/reject`
- [ ] `GET /leader/result-suggestions`
- [ ] `POST /leader/result-suggestions/batch-suggest`
- [ ] `GET /leader/result-suggestions/{id}`
- [ ] `GET /leader/result-suggestions/{id}/evidence/{evidenceId}`
- [ ] `GET /leader/result-suggestions/{id}/evidence/{evidenceId}/archive-entry`
- [ ] `GET /leader/result-suggestions/{id}/evidence/{evidenceId}/preview`
- [ ] `POST /leader/result-suggestions/{id}/suggest`
- [ ] `GET /department/result-confirms/{id}`
- [ ] `POST /department/result-confirms/{id}/confirm`
- [ ] `GET /department/result-confirms/{id}/evidence/{evidenceId}`
- [ ] `POST /department/result-confirms/{id}/reject`

### 3.4 部门管理（18）

- [ ] `POST /department/todos/{id}/remind`
- [ ] `POST /department/todos/{id}/read`
- [ ] `POST /department/todos/{id}/escalate`
- [ ] `POST /department/todos/{id}/done`
- [ ] `POST /department/todos/batch-remind`
- [ ] `POST /department/todos/batch-escalate`
- [ ] `GET /department/appeals/{id}`
- [ ] `POST /department/appeals/{id}/accept`
- [ ] `GET /department/appeals/{id}/package`
- [ ] `POST /department/appeals/{id}/resolve`
- [ ] `GET /employee/appeals`
- [ ] `POST /employee/appeals`
- [ ] `GET /employee/appeals/options`
- [ ] `GET /employee/appeals/{id}/package`
- [ ] `POST /employee/plan-adjustments`
- [ ] `GET /leader/plan-adjustments`
- [ ] `GET /leader/plan-adjustments/{id}`
- [ ] `POST /leader/plan-adjustments/{id}/process`

### 3.5 员工账号（7）

> 依赖批次 0.3 的 `python-multipart`

- [ ] `POST /system/users/register`
- [ ] `POST /system/users/import`
- [ ] `GET /system/users/import-template`
- [ ] `GET /system/users/export`
- [ ] `PUT /system/users/{id}`
- [ ] `POST /system/users/{id}/reset-password`
- [ ] `POST /system/users/{id}/status`

### 3.6 配置类 CRUD（10）

> 路径已在批次 1 改名为复数形式，此处补齐写操作

- [ ] `POST /department/templates`
- [ ] `PUT /department/templates/{id}`
- [ ] `POST /department/templates/{id}/{enable|disable}`
- [ ] `POST /department/acceptance-standards`
- [ ] `PUT /department/acceptance-standards/{id}`
- [ ] `POST /department/acceptance-standards/{id}/{enable|disable}`
- [ ] `POST /department/score-rules`
- [ ] `PUT /department/score-rules/{id}`
- [ ] `POST /department/score-rules/{id}/enable`
- [ ] `POST /department/score-rules/{id}/simulate`

### 3.7 成果提交（11）

- [ ] `POST /employee/results/submit`
- [ ] `POST /employee/results/submit/excel`
- [ ] `GET /employee/results/submit/options`
- [ ] `GET /employee/results/{id}`
- [ ] `GET /employee/results/{id}/evidence/{evidenceId}`
- [ ] `GET /employee/week-results/submit/options`
- [ ] `GET /employee/week-results/{id}`
- [ ] `GET /employee/week-results/{id}/evidence/{evidenceId}`
- [ ] `GET /employee/plan-draft-versions`
- [ ] `GET /employee/plan-draft-versions/{id}`
- [ ] `GET /employee/plan-draft-versions/{id}/restore`

### 3.8 争议（9）

- [ ] `GET /dispute/cases`
- [ ] `GET /dispute/cases/{id}`
- [ ] `POST /dispute/cases/{id}/reviewers`
- [ ] `DELETE /dispute/cases/{id}/reviewers/{reviewerId}`
- [ ] `GET /dispute/cases/{id}/reviewer-candidates`
- [ ] `POST /dispute/cases/{id}/opinions`
- [ ] `POST /dispute/cases/{id}/recusal`
- [ ] `GET /dispute/cases/{id}/package`
- [ ] `POST /dispute/cases/{id}/decision`

### 3.9 台账报表（14）

- [ ] `GET /department/department-ledgers`
- [ ] `POST /department/department-ledgers/export`
- [ ] `GET /leader/team-ledgers`
- [ ] `POST /leader/team-ledgers/export`
- [ ] `GET /employee/performance-evidence`
- [ ] `POST /employee/performance-evidence/export`
- [ ] `GET /leader/project-day-plan-packages`
- [ ] `GET /leader/project-day-plan-packages/{id}`
- [ ] `GET /leader/project-week-plan-packages`
- [ ] `GET /leader/project-week-plan-packages/{id}`
- [ ] `GET /leader/project-week-plan-packages/{id}/excel`
- [ ] `GET /leader/project-week-result-packages`
- [ ] `GET /leader/project-week-result-packages/{id}`
- [ ] `GET /leader/project-week-result-packages/{id}/excel`

### 3.10 周成果审批（4）

- [ ] `POST /leader/week-result-approvals/{id}/approve`
- [ ] `POST /leader/week-result-approvals/{id}/reject`
- [ ] `POST /department/week-result-approvals/{id}/approve`
- [ ] `POST /department/week-result-approvals/{id}/reject`

### ⚠️ 模板字面量路径需特殊处理

前端使用模板字符串构造路径时，后端路径参数需用 `{action}` 形式：

| 前端写法 | 后端应对应 |
|---|---|
| `` `.../templates/${id}/${enabled ? 'enable' : 'disable'}` `` | `POST /department/templates/{id}/{action}` |
| `` `.../acceptance-standards/${id}/${enabled ? 'enable' : 'disable'}` `` | `POST /department/acceptance-standards/{id}/{action}` |
| `` `/${workspace}/week-result-approvals/${resultId}/evidence/${evidenceId}` `` | 需拆成 leader / department 两个固定路径 |

### ✅ 验收标准

- [ ] 契约脚本输出：`已对齐 79 → 226`
- [ ] 员工 → 领导审批 → 点评 → 确认 全链路可端到端走通

---

## 🤖 批次 4 — AI 域（34 个端点）

> 难度最高：涉及 LLM 供应商调用、长耗时、流式、会话状态。

### 4.1 system/ai（10）— 纯 CRUD，最独立，先做

- [ ] `GET /system/ai/configs`
- [ ] `POST /system/ai/configs`
- [ ] `PUT /system/ai/configs/{id}`
- [ ] `POST /system/ai/configs/{id}/enable`
- [ ] `POST /system/ai/configs/{id}/test`
- [ ] `GET /system/ai/prompts`
- [ ] `POST /system/ai/prompts/publish`
- [ ] `GET /system/ai/metrics`
- [ ] `GET /system/ai/call-logs`
- [ ] `GET /system/ai/call-logs/{id}`

### 4.2 employee/ai（16）— 依赖 LLM 集成

- [ ] `POST /employee/ai/month-plans/generate`
- [ ] `GET /employee/ai/month-plans/context`
- [ ] `POST /employee/ai/month-plans/check`
- [ ] `POST /employee/ai/month-plans/items/optimize`
- [ ] `POST /employee/ai/week-plans/generate`
- [ ] `GET /employee/ai/week-plans/context`
- [ ] `POST /employee/ai/week-plans/adjust`
- [ ] `POST /employee/ai/day-plans/generate`
- [ ] `GET /employee/ai/day-plans/context`
- [ ] `POST /employee/ai/day-plans/adjust`
- [ ] `POST /employee/ai/plan-examples/extract`
- [ ] `POST /employee/ai/suggestions/{id}/actions`
- [ ] `GET /employee/ai/conversations/active`
- [ ] `GET /employee/ai/conversations/history`
- [ ] `POST /employee/ai/conversations/new`
- [ ] `GET /employee/ai/conversations/{id}/messages`

### 4.3 AI 审核（8）— 依赖 4.2

- [ ] `GET /ai/reviews/capability`
- [ ] `GET /ai/reviews/latest`
- [ ] `POST /ai/reviews/plans/{bizType}/{bizId}/check`
- [ ] `POST /ai/reviews/plans/{bizType}/{bizId}/ensure`
- [ ] `POST /ai/reviews/results/preview`
- [ ] `GET /leader/ai/month-plan-context`
- [ ] `PUT /leader/ai/month-plan-context`
- [ ] `POST /ai/requests/{requestId}`

### ✅ 验收标准

- [ ] 契约脚本输出：`已对齐 226 → 260`

---

## 📋 里程碑与工作量

| 批次 | 内容 | 已对齐端点数 | 预估工作量 |
|---|---|---|---|
| 0 | 解锁 OpenAPI + 重启服务 | — | 0.5h |
| 1 | 重命名 5 处 + 契约脚本 | 36 → **41** | 1d |
| 2 | 同组补子路径（38） | 41 → **79** | 2-3d |
| 3 | 核心业务域（147） | 79 → **226** | 5-8d |
| 4 | AI 域（34） | 226 → **260** | 5-8d |
| — | 剩余长尾 / 低频（3） | 260 → **263** | 按需 |

> 批次 2/3/4 的端点数为文档中实际列出的条目数（脚本统计），非估算。
> 批次 1 重命名的 5 个 GET 已从「缺失」中移除，未重复计入批次 2/3。

---

## ✅ 已对齐清单（36 个，无需动作）

```
/auth/login                      /auth/logout
/auth/me                         /auth/change-password
/health
/messages                        /messages/summary
/messages/read-all               /messages/{id}/read
/system/dashboard                /system/options
/system/departments              /system/departments/{id}
/system/employees                /system/roles
/system/roles/{id}               /system/permissions
/system/workday-rules            /system/workday-rules/{id}
/system/audits                   /system/audits/export
/system/knowledge                /system/week-plan-standard-supplements
/system/week-plan-standard-supplements/{id}
/department/dashboard             /department/employees
/department/export-tasks         /department/project-month-plan-packages
/department/project-month-plan-packages/{id}
/department/project-result-packages
/v1/import/employees-csv          /v1/import/departments-csv
```

---

## 🔍 遗留待确认（不在本次范围）

| # | 事项 | 说明 |
|---|---|---|
| B.1 | `main.py` 的 11 个 `/api/*` 旧路由 | 与 `/v1/*` 并存，疑似历史遗留，建议下批次清理 |
| B.2 | `GET /planning/month-plans`、`/day-plans`、`/results` | `main.py` 中用 `@app` 定义，但**裸路径无 `/v1/` 前缀**，需确认是否保留 |
| B.3 | `GET /system/users/export` | 已归入 3.5 |
| B.4 | `/api/` 与 `/v1/` 双前缀 | 前端代理 `/api/* → /v1/*`，需在契约脚本中明确只比对 `/v1/` |

---

## 🔒 代码卫生（建议与批次 0 同步处理）

当前工作区存在大量噪声，影响 diff 质量：

- [ ] `.gitignore` 增加 `__pycache__/`、`*.pyc`、`*.log`、`*.db`
- [ ] 清理已提交的 `backend/app/__pycache__/*.pyc`
- [ ] 清理 `backend/uvicorn.log`、`backend/uvicorn.err.log`
- [ ] 处理 `frontend/dist/` 的大量删除（确认是否应提交构建产物）
- [ ] **提交核心修复**：`frontend/src/components/AppShell.vue`、`frontend/src/router/layout.ts`（当前未跟踪，丢失风险高）

---

## ⚙️ 待拍板决策

| # | 决策 | 方案 A（推荐） | 方案 B |
|---|---|---|---|
| D.1 | 旧路径兼容策略 | 保留旧路径 + `include_in_schema=False` | 直接改名删除 |
| D.2 | CI 严格度 | 白名单放行已有缺口，只卡新增端点 | 立即全量阻断 |
| D.3 | 契约事实源 | 运行时从 `app.openapi()` 读取（推荐） | 额外维护静态 `api-contract.yaml` |

---

## 📌 立即可执行的下一步

1. **批次 0**（0.5h）— 解锁 OpenAPI，所有后续验收的前提
2. **提交核心修复**（30min）— 消除未跟踪文件的丢失风险
3. **批次 1**（1d）— 重命名 5 处 + 契约校验脚本，锁定基线防回归
4. **批次 2**（2-3d）— 补 32 个子路径，404 率从 27% 降到 <10%
5. **批次 3**（5-8d）→ **批次 4**（5-8d）— 按业务闭环补齐

---

*文档基于 2026-11-14 的实测数据生成。所有数量均来自脚本测量，非估算。*
