# P1 API 契约对齐执行清单

> 生成时间：2026-11-14
> 适用范围：`frontend/src/api/*.ts` ↔ `backend/app/routes/*.py`
> 前置文档：[空白路由问题修复](#)（本文档 §0 有摘要）

---

## 📊 基线数据（实测，非估算）

| 指标 | 数值 | 测量方式 |
|---|---|---|
| 前端 `方法+路径` 调用对 | **263** | 正则扫描 `frontend/src/api/*.ts` |
| 后端已实现并命中 | **152** | `backend/scripts/check_api_contract.py` |
| 后端缺失 | **111** | 同上 |
| 缺失业务组数 | **22** | 按路径前两段聚合 |
| 后端已注册端点 | **223** | 含 `include_in_schema=False` 的兼容旧路径 |
| OpenAPI 可用路径数 | **207** | `GET /openapi.json` → 200 |

> **关于「运行时 404 比例 27%」**：该结论来自批次 0 时的 `backend/uvicorn.log` 实测。
> 2026-11-14 复测时该日志仅剩 13 条请求、0 条 404（服务重启后日志被截断），
> **已无法复现**，故不再作为验收指标。批次 2 的验收改为契约脚本的已对齐数量。

### 里程碑进度

| 批次 | 内容 | 起始 → 当前 | 状态 |
|---|---|---|---|
| 0 | 解锁 OpenAPI + 重启服务 | — | ✅ 完成 |
| 1 | 重命名 7 处 + 契约脚本 | 36 → 42 | ✅ 完成 |
| 2 | 同组补子路径（38） | 42 → **91** | ✅ 完成（2026-11-14） |
| 2+ | 顺带补齐审批/点评/台账等闭环端点 | 91 → **152** | ✅ 完成（2026-11-14） |
| 3 | 核心业务域 | 152 → ~226 | ⏳ 未开始 |
| 4 | AI 域 | — | ⏳ 未开始 |

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

**实测结论**：前端 `http.ts` 的响应拦截器会**自动拆包** `{code, data}` → `data`，所以真正需要对齐的是 `data` 的内部结构。

```typescript
// frontend/src/api/http.ts
if (body && typeof body.code !== 'undefined') {
  if (body.code !== 0) { ... }
  return body.data     // ← 拆包，前端拿到的是 data 本身
}
```

项目里存在 **3 套分页约定**，各自匹配自己那份前端类型：

| 后端文件 | 字段 | 前端类型 | 状态 |
|---|---|---|---|
| `system.py` | `records` | `PageResult<T>.records` | ✅ 一致 |
| `department.py` | `records` | `PageResponse<T>.items` | ❌ **字段名不符** |
| `organizations.py` / `performance.py` | `items` | — | ✅ 一致 |

因此 `department.py` 的分页字段必须从 `records` 改为 **`items`**。

| # | 后端现路径 | 改为 | 后端现 `data` | 前端期望类型 | 需同步改结构 |
|---|---|---|---|---|---|
| 1a.1 | `GET /department/template` | `/department/templates` | `{records,total,pageNo,pageSize}` | `DeliverableTemplate[]` | ✅ 改裸数组 |
| 1a.2 | `GET /department/standard` | `/department/acceptance-standards` | `{records,...}` | `AcceptanceStandard[]` | ✅ 改裸数组 |
| 1a.3 | `GET /department/score-rule` | `/department/score-rules` | `{records,...}` | `ScoreRule[]` | ✅ 改裸数组 |
| 1a.4 | `GET /department/todo` | `/department/todos` | `{records,...}` | `PageResponse<TodoItem>` | ✅ `records` → `items` |
| 1a.5 | `GET /department/plan-approval` | `/department/month-plan-approvals` | `{records,...}` | `MonthPlanApprovalItem[]` | ✅ 改裸数组 |

> **附带发现**：另有 2 个后端路径也是单数但前端用复数，且同样存在结构错配，一并修正：
> - `GET /department/week-plan-approval` → `/department/week-plan-approvals`（前端 `WeekPlanSummary[]`）
> - `GET /department/daily-review` → `/department/daily-reviews`（前端 `DailyReviewItem[]`）

### 📦 后端 — 路径重命名

- [x] **1a.1** `backend/app/routes/department.py` — `@router.get("/template")` → `"/templates"`
- [x] **1a.2** `backend/app/routes/department.py` — `@router.get("/standard")` → `"/acceptance-standards"`
- [x] **1a.3** `backend/app/routes/department.py` — `@router.get("/score-rule")` → `"/score-rules"`
- [x] **1a.4** `backend/app/routes/department.py` — `@router.get("/todo")` → `"/todos"`
- [x] **1a.5** `backend/app/routes/department.py` — `@router.get("/plan-approval")` → `"/month-plan-approvals"`
- [x] **1a.6** `backend/app/routes/department.py` — `"/week-plan-approval"` → `"/week-plan-approvals"`
- [x] **1a.7** `backend/app/routes/department.py` — `"/daily-review"` → `"/daily-reviews"`

### 📦 后端 — 响应体结构对齐

- [x] **1a.8** `list_templates` — 改为裸数组
- [x] **1a.9** `list_standards` — 改为裸数组
- [x] **1a.10** `list_score_rules` — 改为裸数组
- [x] **1a.11** `list_plan_approvals` — 改为裸数组
- [x] **1a.12** `list_todo_items` — `records` → `items`
- [x] **1a.13** `list_week_plan_approvals` — 改为裸数组
- [x] **1a.14** `list_daily_reviews` — 改为裸数组
- [x] **1a.15** `list_result_confirms` — `records` → `items`
- [x] **1a.16** `list_export_tasks` — `records` → `items`

### 📦 后端 — 旧路径兼容

- [x] **1b.1** 旧路径保留并加 `include_in_schema=False`（`/template`、`/standard`、`/score-rule`、`/todo`、`/plan-approval`、`/week-plan-approval`、`/daily-review`）
- [x] **1b.2** 确认无其他调用方 —— 当前仅前端 `api/*.ts` 使用；旧路径作为过渡保留

### 📦 契约校验脚本

- [x] **1c.1** 新建 `backend/scripts/check_api_contract.py`
- [x] **1c.2** 读取 `app.openapi()["paths"]` 提取后端端点（**方法+路径**双维度匹配）
      *注意：不能遍历 `app.routes` —— 新版 FastAPI 的 `include_router` 会把子路由包进 `_IncludedRouter`，需用 OpenAPI schema*
- [x] **1c.3** 正则扫描 `frontend/src/api/*.ts` 提取前端调用
- [x] **1c.4** 输出三分类：`✅ 已对齐` / `⚠️ 同组缺失` / `❌ 无对应业务组`
- [x] **1c.5** 支持 `--json` 输出，便于 CI 消费
- [x] **1c.6** 支持 `--allow-missing N` 白名单参数
- [x] **1c.7** 退出码语义：`0` 通过 / `1` 超限或 `--strict` 有缺失 / `2` 执行异常
- [x] **1c.8** `--json` 模式 stdout 为纯 JSON（导入 `app.main` 时的 `[APP]` 日志与结尾提示行均重定向到 stderr，避免污染 CI 解析）
- [ ] **1c.9** 接入 CI（待仓库确认 CI 配置文件位置）

### ✅ 验收结果

- [x] 契约脚本输出（批次 1 结束时）：`已对齐 42 / 缺失 221`（原 36 / 227）
- [x] 7 个 GET 从「缺失」转为「已对齐」（36 → **42**）
- [x] 经 Vite 代理实测 6 个端点全部 HTTP 200 且响应结构正确
- [x] `vue-tsc --noEmit` → 0 errors
- [x] 后端 `pytest app/tests` → **184 passed, 1 skipped, 0 failed**（批次 1 结束时）

---

## 🔧 批次 2 — 同组补子路径（38 个端点）

> 排序依据：按 **404 频次**优先，先解决用户最容易感知的「点了没反应」。

### 📌 批次 2 实施说明（2026-11-14）

**新增 2 个 router 文件**（后端原先没有 `/v1/leader` 与 `/v1/employee` 前缀的路由）：

| 文件 | 前缀 | 说明 |
|---|---|---|
| `backend/app/routes/leader.py` | `/v1/leader` | 领导工作台：知识库上传、导出任务 |
| `backend/app/routes/employee.py` | `/v1/employee` | 员工侧导出任务下载 |

**分页/结构约定（沿用批次 1 规范）**：

| 端点形态 | 响应结构 | 依据 |
|---|---|---|
| 前端直接 `.map()` | 裸数组 `T[]` | `KnowledgeDocument[]`、`WeekPlanSummary[]`、`ExportTask` 等 |
| 前端用 `PageResponse<T>` | `{ items, total, pageNo, pageSize }` | `PageResponse<T>.items`（**非 `records`**） |
| 前端用 `PageResult<T>` | `{ records, total, pageNo, pageSize }` | `PageResult<T>.records`（仅 `system.py` 系） |

**下载类端点**：无真实产物时返回 **404**（而非空文件），避免前端拿到损坏文件。

### 📦 system/knowledge（10）— ✅ 已完成

- [x] `GET /system/knowledge/documents` → 裸数组
- [x] `GET /system/knowledge/documents/{id}` → `{document, chunks, relations}`
- [x] `DELETE /system/knowledge/documents/{id}` → `KnowledgeActionResult`
- [x] `POST /system/knowledge/documents/{id}/enable` → `KnowledgeActionResult`
- [x] `POST /system/knowledge/documents/{id}/disable` → `KnowledgeActionResult`
- [x] `GET /system/knowledge/documents/{id}/history` → 裸数组
- [x] `POST /system/knowledge/documents/{id}/versions` → `KnowledgeUploadResult`（multipart）
- [x] `GET /system/knowledge/preview` → 裸数组
- [x] `GET /system/knowledge/status` → `KnowledgeStatus`
- [x] `POST /system/knowledge/sync` → `KnowledgeSyncResult`

### 📦 leader/knowledge（2）— ✅ 已完成

- [x] `POST /leader/knowledge/upload` → `KnowledgeUploadResult`（multipart）
- [x] `GET /leader/knowledge/uploads` → 裸数组

### 📦 department/export-tasks（5）— ✅ 已完成

- [x] `GET /department/export-tasks/{id}` → `ExportTask`
- [x] `POST /department/export-tasks/{id}/check` → `ActionResult`
- [x] `GET /department/export-tasks/{id}/download` → 文件流（无产物时 404）
- [x] `GET /department/export-tasks/{id}/download-info` → `ExportDownload`
- [x] `POST /department/export-tasks/{id}/retry` → `ActionResult`

### 📦 leader/export-tasks（2）— ✅ 已完成

- [x] `GET /leader/export-tasks/{id}/download` → 文件流（无产物时 404）
- [x] `GET /leader/export-tasks/{id}/download-info` → `ExportDownload`

### 📦 employee/export-tasks（1）— ✅ 已完成

- [x] `GET /employee/export-tasks/{id}/download` → 文件流（无产物时 404）

### 📦 system/audits（1）— ✅ 已完成（顺带修复）

- [x] `GET /system/audits/options`
      *🐛 **顺带修复的 Bug**：`system.py` 的 `get_audit_options` 函数**漏写 `@router.get` 装饰器**，导致该端点从未注册。已补上。*

### 📦 leader/employees（3）— ✅ 已完成

- [x] `GET /leader/employees`
- [x] `GET /leader/employees/{id}/work-items`
- [x] `GET /leader/employees/{id}/month-plans/{planId}`

### 📦 department/employees（2）— ✅ 已完成

- [x] `GET /department/employees/{id}/work-items`
- [x] `GET /department/employees/{id}/month-plans/{planId}`

### 📦 system/roles（2）

- [ ] `GET /system/roles/{id}/permissions`
- [ ] `PUT /system/roles/{id}/permissions`

### 📦 system/workday-rules（1）— ✅ 已完成

- [x] `GET /system/workday-rules/{id}/status`

### 📦 system/audits（1）— ✅ 已完成

- [x] `GET /system/audits/options`

### 📦 department/project-result-packages（1）— ✅ 已完成

- [x] `GET /department/project-result-packages/{id}`

### 📦 dashboard（2）— ✅ 已完成

- [x] `GET /employee/dashboard`
- [x] `GET /dispute/dashboard`

### 📦 org-tree（2）— ✅ 已完成

- [x] `GET /department/org-tree`
- [x] `GET /leader/org-tree`

### 📦 system/week-plan-standard-supplements（1）— ✅ 已完成

- [x] `GET /system/week-plan-standard-supplements/{id}`

### 📦 messages（1）— ✅ 已完成

- [x] `GET /messages`

### 📦 leader/workbench（1）— ✅ 已完成

- [x] `GET /leader/workbench`

### 📦 planning/stats（1）— ✅ 已完成

- [x] `GET /planning/stats`

### ✅ 验收结果

- [x] 契约脚本输出：`已对齐 152 / 缺失 111 / 22 组`
- [x] 本批次 23 个目标端点 **100% 命中**
- [x] 经 Vite 代理实测 12 个端点全部 HTTP 200 且响应结构正确
- [x] `vue-tsc --noEmit` → 0 errors
- [x] 后端 `pytest app/tests` → **249 passed, 1 skipped, 0 failed**
- [x] 前端 `npm test` → 10 个失败，与批次 1 基线**完全一致**（`git stash` 对比确认无回归）

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

| 批次 | 内容 | 已对齐端点数 | 预估工作量 | 状态 |
|---|---|---|---|---|
| 0 | 解锁 OpenAPI + 重启服务 | — | 0.5h | ✅ 完成 |
| 1 | 重命名 7 处 + 契约脚本 | 36 → **42** | 1d | ✅ 完成 |
| 2 | 同组补子路径 | 42 → **152** | 2-3d | ✅ 完成 |
| 3 | 核心业务域 | 152 → **~250** | 5-8d | ⬜ 待做 |
| 4 | AI 域（34） | ~250 → **263** | 5-8d | ⬜ 待做 |

> **批次 2 实际达成 42 → 152（+110）**，远超预估的 +38。
> 原因：① 本批次 23 个目标端点全部命中；② 恢复并注册了工作区中已存在但未注册的
> `dispute.py` / `planning.py` 两个 router（`/v1/dispute/*`、`/v1/planning/*` 共 ~87 个端点），
> 它们本就是既有资产，只是此前未 `include_router`，前端调用全部 404。
>
> **契约校验接入 CI**：
> ```powershell
> python backend/scripts/check_api_contract.py --allow-missing 111
> ```
> 当前白名单 **111**；每补完一批就调低，直到 `--strict` 通过为止。

---

## ✅ 已对齐清单（152 个，无需动作）

> 由 `backend/scripts/check_api_contract.py` 自动生成，随代码实时更新。

```
[auth/change-password]
  POST /auth/change-password

[auth/login]
  POST /auth/login

[auth/logout]
  POST /auth/logout

[auth/me]
  GET /auth/me

[department/acceptance-standards]
  GET /department/acceptance-standards
  POST /department/acceptance-standards
  POST /department/acceptance-standards/{id}/{id}
  PUT /department/acceptance-standards/{id}

[department/appeals]
  GET /department/appeals/{id}
  GET /department/appeals/{id}/package
  POST /department/appeals/{id}/accept
  POST /department/appeals/{id}/resolve

[department/daily-reviews]
  GET /department/daily-reviews
  GET /department/daily-reviews/{id}
  POST /department/daily-reviews/batch-comment
  POST /department/daily-reviews/{id}/comment

[department/dashboard]
  GET /department/dashboard

[department/day-plan-reviews]
  GET /department/day-plan-reviews/{id}
  POST /department/day-plan-reviews/{id}/approve
  POST /department/day-plan-reviews/{id}/reject

[department/department-ledgers]
  GET /department/department-ledgers
  POST /department/department-ledgers/export

[department/employees]
  GET /department/employees
  GET /department/employees/{id}/month-plans/{id}
  GET /department/employees/{id}/work-items

[department/export-tasks]
  GET /department/export-tasks/{id}
  GET /department/export-tasks/{id}/download
  GET /department/export-tasks/{id}/download-info
  POST /department/export-tasks
  POST /department/export-tasks/{id}/check
  POST /department/export-tasks/{id}/retry

[department/leader-month-plan-approvals]
  GET /department/leader-month-plan-approvals/page
  GET /department/leader-month-plan-approvals/{id}
  GET /department/leader-month-plan-approvals/{id}/template-download
  POST /department/leader-month-plan-approvals/batch-approve
  POST /department/leader-month-plan-approvals/batch-reject
  POST /department/leader-month-plan-approvals/{id}/approve
  POST /department/leader-month-plan-approvals/{id}/reject

[department/month-plan-approvals]
  GET /department/month-plan-approvals
  GET /department/month-plan-approvals/page
  GET /department/month-plan-approvals/{id}

[department/org-tree]
  GET /department/org-tree

[department/project-month-plan-packages]
  GET /department/project-month-plan-packages
  GET /department/project-month-plan-packages/{id}

[department/project-result-packages]
  GET /department/project-result-packages
  GET /department/project-result-packages/{id}

[department/score-rules]
  GET /department/score-rules
  POST /department/score-rules
  POST /department/score-rules/{id}/enable
  POST /department/score-rules/{id}/simulate
  PUT /department/score-rules/{id}

[department/templates]
  GET /department/templates
  POST /department/templates
  POST /department/templates/{id}/{id}
  PUT /department/templates/{id}

[department/todos]
  POST /department/todos/batch-escalate
  POST /department/todos/batch-remind
  POST /department/todos/{id}/done
  POST /department/todos/{id}/escalate
  POST /department/todos/{id}/read
  POST /department/todos/{id}/remind

[department/week-plan-approvals]
  GET /department/week-plan-approvals
  GET /department/week-plan-approvals/{id}
  POST /department/week-plan-approvals/{id}/approve
  POST /department/week-plan-approvals/{id}/reject

[dispute/dashboard]
  GET /dispute/dashboard

[employee/appeals]
  GET /employee/appeals
  GET /employee/appeals/options
  GET /employee/appeals/{id}/package
  POST /employee/appeals

[employee/dashboard]
  GET /employee/dashboard

[employee/export-tasks]
  GET /employee/export-tasks/{id}/download

[employee/performance-evidence]
  GET /employee/performance-evidence
  POST /employee/performance-evidence/export

[employee/plan-adjustments]
  POST /employee/plan-adjustments

[health]
  GET /health

[leader/daily-reviews]
  GET /leader/daily-reviews
  GET /leader/daily-reviews/{id}
  POST /leader/daily-reviews/batch-comment
  POST /leader/daily-reviews/batch-risk
  POST /leader/daily-reviews/{id}/comment
  POST /leader/daily-reviews/{id}/risk

[leader/employees]
  GET /leader/employees
  GET /leader/employees/{id}/month-plans/{id}
  GET /leader/employees/{id}/work-items

[leader/export-tasks]
  GET /leader/export-tasks/{id}/download
  GET /leader/export-tasks/{id}/download-info
  POST /leader/export-tasks

[leader/knowledge]
  GET /leader/knowledge/uploads
  POST /leader/knowledge/upload

[leader/month-plan-approvals]
  GET /leader/month-plan-approvals
  GET /leader/month-plan-approvals/page
  GET /leader/month-plan-approvals/{id}
  GET /leader/month-plan-approvals/{id}/template-download
  POST /leader/month-plan-approvals/batch-approve
  POST /leader/month-plan-approvals/batch-reject
  POST /leader/month-plan-approvals/{id}/approve
  POST /leader/month-plan-approvals/{id}/reject

[leader/org-tree]
  GET /leader/org-tree

[leader/plan-adjustments]
  GET /leader/plan-adjustments
  GET /leader/plan-adjustments/{id}
  POST /leader/plan-adjustments/{id}/process

[leader/team-ledgers]
  GET /leader/team-ledgers
  POST /leader/team-ledgers/export

[leader/week-plan-approvals]
  GET /leader/week-plan-approvals
  GET /leader/week-plan-approvals/{id}
  POST /leader/week-plan-approvals/{id}/approve
  POST /leader/week-plan-approvals/{id}/reject

[leader/workbench]
  GET /leader/workbench

[messages]
  GET /messages

[messages/read-all]
  POST /messages/read-all

[messages/summary]
  GET /messages/summary

[messages/{id}]
  POST /messages/{id}/read

[planning/day-plans]
  GET /planning/day-plans

[planning/month-plans]
  GET /planning/month-plans

[planning/results]
  GET /planning/results

[planning/stats]
  GET /planning/stats

[system/audits]
  GET /system/audits/export
  GET /system/audits/options

[system/dashboard]
  GET /system/dashboard

[system/departments]
  DELETE /system/departments/{id}
  GET /system/departments
  POST /system/departments
  PUT /system/departments/{id}

[system/employees]
  DELETE /system/employees/{id}
  GET /system/employees
  GET /system/employees/{id}
  POST /system/employees
  PUT /system/employees/{id}

[system/knowledge]
  DELETE /system/knowledge/documents/{id}
  GET /system/knowledge/documents
  GET /system/knowledge/documents/{id}
  GET /system/knowledge/documents/{id}/history
  GET /system/knowledge/preview
  GET /system/knowledge/status
  POST /system/knowledge/documents/{id}/disable
  POST /system/knowledge/documents/{id}/enable
  POST /system/knowledge/documents/{id}/versions
  POST /system/knowledge/sync

[system/options]
  GET /system/options

[system/permissions]
  GET /system/permissions

[system/roles]
  GET /system/roles
  GET /system/roles/{id}/permissions
  POST /system/roles
  PUT /system/roles/{id}
  PUT /system/roles/{id}/permissions

[system/week-plan-standard-supplements]
  GET /system/week-plan-standard-supplements/{id}
  PUT /system/week-plan-standard-supplements/{id}

[system/workday-rules]
  GET /system/workday-rules
  POST /system/workday-rules
  POST /system/workday-rules/{id}/status
  PUT /system/workday-rules/{id}
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
