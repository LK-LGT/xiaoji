# P1 API 契约对齐执行清单

> 生成时间：2026-11-14
> 适用范围：`frontend/src/api/*.ts` ↔ `backend/app/routes/*.py`
> 前置文档：[空白路由问题修复](#)（本文档 §0 有摘要）

---

## 📊 基线数据（实测，非估算）

| 指标 | 数值 | 测量方式 |
|---|---|---|
| 前端 `方法+路径` 调用对 | **267** | 正则扫描 `frontend/src/api/*.ts` |
| 后端已实现并命中 | **267** | `backend/scripts/check_api_contract.py` |
| 后端缺失 | **0** | 同上 |
| 缺失业务组数 | **0** | 按路径前两段聚合 |
| 后端已注册端点 | **336** | 含 `include_in_schema=False` 的兼容旧路径 |
| OpenAPI 可用路径数 | **336** | `GET /openapi.json` → 200 |

> **关于「运行时 404 比例 27%」**：该结论来自批次 0 时的 `backend/uvicorn.log` 实测。
> 2026-11-14 复测时该日志仅剩 13 条请求、0 条 404（服务重启后日志被截断），
> **已无法复现**，故不再作为验收指标。批次 2 的验收改为契约脚本的已对齐数量。

> **关于当前数字与批次 2 提交点的差异**：批次 2 提交时为「已对齐 152 / 缺失 111」。
> 此后经批次 3 补齐至 **267 / 0**，其中包含：
> - **批次 3.1 员工计划闭环（24 个）**
> - **批次 3 核心业务域（91 个）**：employee/ai 16、dispute/cases 10、employee/week-plans 10、
>   system/ai 10、leader/result-suggestions 7、system/users 7、employee/results 5、
>   employee/week-results 3、employee/plan-draft-versions 3、ai/reviews 5、
>   leader 项目包 8、department 结果确认 4、周成果审批 5、leader/ai 2 等
> - **并行的 AI 域端点补齐**（`employee_ai.py` / `system_ai.py`）
>
> 其中属于批次 3.1 的部分已由 `backend/app/tests/test_employee_plan_api.py`
> 的 41 条断言单独锁定。

### 里程碑进度

| 批次 | 内容 | 起始 → 当前 | 状态 |
|---|---|---|---|
| 0 | 解锁 OpenAPI + 重启服务 | — | ✅ 完成 |
| 1 | 重命名 7 处 + 契约脚本 | 36 → 42 | ✅ 完成 |
| 2 | 同组补子路径（38） | 42 → **91** | ✅ 完成（2026-11-14） |
| 2+ | 顺带补齐审批/点评/台账等闭环端点 | 91 → **152** | ✅ 完成（2026-11-14） |
| 3.1 | 员工计划闭环（24） | 152 → **216** | ✅ 完成（2026-11-14，含并行的 AI 域增量） |
| 3.1+ | 计划域落地（模型/迁移/CRUD/状态机） | 契约骨架 → 真实持久化 | ✅ **完成（2026-11-14）** — 见下方「计划域落地」 |
| 3 | 核心业务域（91） | 216 → **267** | ✅ **完成（2026-11-14）** — 缺失归零 |
| 4 | AI 域 | — | ✅ 完成（与批次 3 合并交付） |

### 🐛 批次 3 顺带修复的全项目级 Bug：`data: dict` 请求体无法解析

**现象**：所有用 `data: dict` / `payload: dict` 声明请求体的端点，POST/PUT 一律返回
**422**（`dict_type` / `model_attributes_type`），请求体以**字符串**形式传入 FastAPI。

**根因**：本项目使用的 **FastAPI 0.162.4 + Pydantic 2.14.3** 组合不会把裸 `dict`
注解自动解析为 JSON body。必须显式声明 Pydantic 模型。

**影响范围**：`employee_plans.py`(6)、`employee.py`(1)、`leader.py`(8)、
`department.py`(13)、`v1/system.py`(3) —— 共 **31 个既有端点**，
这些是批次 3 之前就存在的潜在缺陷，只是契约脚本只校验「方法+路径」，无法发现。

**修复**：
- 新增 `backend/app/routes/shared.py` 的 `JsonBody` 基类（宽松接收任意字段，兼容 `.get()` 用法）
- 全部 `data: dict` → `data: JsonBody`
- 新增端点一律使用显式 Pydantic 模型（`system_ai.py` / `dispute.py` / `employee_ai.py` 等）

**验证**：`backend/scripts/smoke_batch3.py` 逐个端点发真实 HTTP 请求，
**96/96 通过**（含 422 / 404 断言），确保请求体确实可被解析。

### 🐛 契约脚本自身的 Bug：模板字面量截断

**现象**：`POST /ai/requests/{id}` 被误报为「前端有后端无」，但实际前端调用是
`` http.post<...>(`/ai/requests/${encodeURIComponent(requestId)}/cancel`) ``。

**根因**：旧正则 `([^`'\"),]+)` 在 `encodeURIComponent(` 的左括号处截断，
把 `/ai/requests/${encodeURIComponent(requestId` 当成完整路径。

**修复**：改为「括号配对扫描」——找到 `http.<method>(` 后按深度定位匹配的右括号，
再取参数区第一层的字符串/模板字面量。修复后前端调用数 267、后端命中 267、**缺失 0**。

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

`frontend/tests/routing-outlet.test.ts` 专门用于防 R1/R2，其中两条断言曾失效：

| 断言 | 失效原因 | 实现是否正确 | 现状 |
|---|---|---|---|
| `路由中不应再有 () => import(...)` | 正则匹配到了**注释里的示例文字**（同文件其他断言都做了去注释，唯独这条没有） | ✅ 正确：剥离注释后动态 import = 0 | ✅ **已修**（2026-11-16 轨1）：断言改为去注释后匹配 |
| `应存在以 AppShell 为布局组件的路由` | `component: AppShell` 已移至 `router/layout.ts`，测试只读 `index.ts` | ✅ 正确：`layout.ts` 中 `AppShell` + `children` 均在 | ✅ **已修**（2026-11-16 轨1）：断言改为跨文件验证工厂 + 调用方 |

> 📌 **2026-11-16 更新**：这两条已随轨1 一并修复，
> 前端测试现为 **193 通过 / 0 失败**，基线机制已退役。

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

- [x] `GET /system/roles/{id}/permissions`
- [x] `PUT /system/roles/{id}/permissions`

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

## 🔧 批次 3 — 核心业务域（91 个端点）— ✅ 已完成（2026-11-14）

> 排序依据：按**业务闭环**排，不按模块排。同一业务链的端点一起做，避免反复返工。
>
> **批次 3 全部完成**：契约脚本输出 **已对齐 267 / 缺失 0**（批次 3 起点为 216 / 51）。

### 3.1 员工计划闭环（24）— ✅ 已完成（2026-11-14）

> **实现说明（阶段一）**：本批最初为**契约层实现** —— 后端当时 34 张表中
> 没有任何计划类表，因此这 24 个端点的路由、方法、参数、响应结构对齐了前端
> TypeScript 接口，但未接领域模型，返回内容为空的响应。
>
> **实现说明（阶段二，已完成）**：计划域已补齐**领域建模 + 数据表 + Alembic 迁移 +
> 真实 CRUD + 状态机 + 验收测试**。详见下方「计划域落地」小节。
>
> 路由归属：
> - 月计划 9 个 → `backend/app/routes/employee_plans.py`（前缀 `/v1/employee`）
> - 周计划 10 个 → `backend/app/routes/employee_week_plan.py`（前缀 `/v1/employee/week-plans`）
> - 日计划 5 个 → `backend/app/routes/employee_plans.py`（前缀 `/v1/employee`）

- [x] `POST /employee/month-plans/draft`
- [x] `GET /employee/month-plans/{id}`
- [x] `POST /employee/month-plans/{id}/draft`
- [x] `POST /employee/month-plans/{id}/draft/clear`
- [x] `DELETE /employee/month-plans/{id}/items/{itemId}`
- [x] `POST /employee/month-plans/{id}/submit`
- [x] `GET /employee/month-plans/{id}/template-download`
- [x] `GET /employee/month-plans/{id}/template-preview`
- [x] `POST /employee/month-plans/{id}/withdraw`
- [x] `GET /employee/day-plans/detail`
- [x] `POST /employee/day-plans/draft`
- [x] `POST /employee/day-plans/submit`
- [x] `DELETE /employee/day-plans/{id}`
- [x] `POST /employee/day-plans/{id}/withdraw`
- [x] `GET /employee/week-plans`
- [x] `POST /employee/week-plans`
- [x] `GET /employee/week-plans/parent-options`
- [x] `DELETE /employee/week-plans/{id}`
- [x] `GET /employee/week-plans/{id}`
- [x] `PUT /employee/week-plans/{id}`
- [x] `POST /employee/week-plans/{id}/items/{itemId}/results/submit`
- [x] `GET /employee/week-plans/{id}/items/{itemId}/results/submit-context`
- [x] `POST /employee/week-plans/{id}/submit`
- [x] `POST /employee/week-plans/{id}/withdraw`

**验证结果**

| 项目 | 结果 |
|---|---|
| 契约脚本 | 3 组全部对齐，缺失 0 |
| 新增断言 | `backend/app/tests/test_employee_plan_api.py` — 41 passed |
| 冒烟测试 | 8 GET + 12 POST + 1 PUT + 3 DELETE = 24/24 通过 |
| 鉴权 | 全部端点要求 token，无 token 返回 401 |
| 响应结构 | 逐字段核对前端 TS 接口（`EmployeeMonthPlanDetailResp` / `WeekPlanDetail` / `WeekPlanSummary` / `EmployeeDayPlanDetailResp` / `WeekResultSubmitContext`） |
| 前端 `vue-tsc --noEmit` | 0 errors |
| 全量 pytest | **无新增失败**（与基线 `git stash` 对比：基线 22 failed + 20 errors，当前 22 failed + 20 errors） |

> **注**：基线的 42 项失败/错误为**既有问题**（`pytest-asyncio` 未安装导致
> `Unknown pytest.mark.asyncio` 标记失效，以及 `app.db` schema 陈旧），
> 与本批次无关，已用 `git stash` 对比确认。

---

### 🏗️ 计划域落地（阶段二）— ✅ 已完成（2026-11-14）

把批次 3.1 的「契约骨架」升级为**真实持久化实现**：领域模型、数据表、Alembic 迁移、
完整 CRUD、状态机、乐观锁、鉴权边界、验收测试全部到位。

#### 数据表设计（6 张）

采用「三层计划 + 各自条目」的扁平结构，层级归属靠外键串起来：

| 表 | 说明 | 关键字段 |
|---|---|---|
| `month_plan` | 月计划容器 | `owner_id`、`plan_month`、`status`、`version_no`、`result_status` |
| `month_plan_item` | 月计划条目（待办任务） | `plan_id`、`task_type`、`performance_weight`、`deliverable`、`quality` |
| `week_plan` | 周计划容器 | `owner_id`、`week_start`/`week_end`、`status`、`version_no` |
| `week_plan_item` | 周计划条目 | `plan_id`、`month_plan_item_id`（↓关联月条目） |
| `day_plan` | 日计划容器 | `owner_id`、`plan_date`、`status`、`version_no`、审批/风控字段 |
| `day_plan_item` | 日计划条目 | `plan_id`、`week_plan_item_id`（↓关联周条目） |

**唯一约束**：
- `uq_month_plan_owner_month` —— 同一员工每月一份月计划
- `uq_day_plan_owner_date` —— 同一员工每天一份日计划

**下钻链路**：`month_plan_item → week_plan_item → day_plan_item`，
周计划条目可挂月计划条目，日计划条目可挂周计划条目。
`GET /employee/week-plans/parent-options` 只返回**尚未被引用**的月计划条目，
防止同一月任务被反复下钻。

#### 状态机

三层计划共用一套状态机（`domain/enums.py::PlanStatus`），流转规则集中在
`services/planning.py`，路由层不允许直接写 `plan.status = ...`：

```
draft ──submit──> submitted ──approve──> approved
  ↑                    │                      │
  │                    ├──reject──> rejected │
  └───── withdraw ─────┴──────────────────────┘
```

| 状态 | 含义 | 可编辑 |
|---|---|---|
| `draft` | 草稿 | ✅ |
| `submitted` | 已提交，进入审批队列 | ❌ |
| `withdrawn` | 已撤回 | ✅ |
| `pending` | 审批中 | ❌ |
| `approved` | 已批准 | ❌ |
| `rejected` | 已驳回 | ✅ |

**周计划状态**对齐前端 `weekPlan.ts` 的 `WeekPlanStatus`：
内部 `draft`/`submitted` → 前端 `DRAFT`/`PENDING`，由 `to_week_status()` 做映射。

**非法流转**统一返回 **409**（`PlanStateError`）。

#### 乐观锁

每张计划表带 `version_no`，每次内容变更 +1。
周计划的提交/撤回/删除接受 `versionNo` 查询参数，不匹配返回 **409**
（`PlanVersionConflict`）。

#### 鉴权与数据范围

- 全部端点复用既有 `get_current_user` 依赖，**无 token 返回 401**。
- 服务层所有读写以 `owner_id = 当前用户` 为边界；跨人访问返回 **404**
  （不泄露「该 id 存在但不属于你」这一信息）。
- 列表端点只返回本人计划。

#### Alembic 迁移

`backend/migrations/versions/0003_phase3_planning.py`（`revises: 0002_phase2_feishu`）：

- 支持 SQLite `render_as_batch=True` 批量改表。
- **幂等**：已存在的表/索引/约束自动跳过，可在已建表的库上重放。
- `downgrade()` 可完整回退 6 张表。
- 已应用到实际库 `perf.db`（34 表 → 40 表），原有数据（92 员工 / 30 部门 / 36 考核记录）完好。

#### 新增文件

| 文件 | 内容 |
|---|---|
| `backend/app/domain/models.py` | 6 个 SQLAlchemy 模型（`MonthPlan` … `DayPlanItem`） |
| `backend/app/domain/enums.py` | `PlanStatus` / `PlanTaskType` / `PlanVersionState` |
| `backend/app/services/planning.py` | 状态机 + 乐观锁 + CRUD + 序列化 |
| `backend/migrations/versions/0003_phase3_planning.py` | Alembic 迁移 |
| `backend/app/tests/test_planning_domain.py` | 40 条验收测试 |

#### 验证结果

| 项目 | 结果 |
|---|---|
| 新增验收测试 | `test_planning_domain.py` — **40 passed** |
| 契约测试 | `test_employee_plan_api.py` — **43 passed**（响应结构改为「有数据」断言） |
| 合计 | **83 passed** |
| Alembic 迁移 | upgrade / 幂等重跑 / downgrade 全部通过；模型 ↔ 迁移 schema **逐列一致** |
| 实际库验证 | `perf.db` 40 表，数据完好 |
| 端到端冒烟 | 真实库跑通「建→读→提交→重复提交 409→撤回→周→日→父选项→清理」 |
| 契约脚本 | 已对齐 **267 / 267**，缺失 **0** |
| 全量 pytest | 基线 **43** → 当前 **42**，**无新增失败**（且顺带修复 1 项） |

> **注**：基线的 43 项失败/错误为**既有问题**（`pytest-asyncio` 未安装导致
> `Unknown pytest.mark.asyncio` 标记失效，以及 `app.db` schema 陈旧），
> 与计划域无关，已用 `git worktree` 在干净 HEAD 上精确对比确认。

#### 未纳入本阶段（后续排期）

- **周成果（week_result）持久化**：`submit-context` / `submit` 目前只校验归属与条目
  有效性，证据落库与结果版本表待「周成果」子域补齐。
- **月计划模板 Excel 产物**：`template-download` 校验归属后返回 404（尚无产物）。
- **历史版本快照**：`recordVersion` / `restoredFromVersionId` 字段已入模型，
  但本次按「行内 `version_no`」方案实现，未建版本快照表。
- **审批端点**：状态机已支持 `approved`/`rejected`，但审批动作本身由主管侧端点触发，
  不在员工侧 24 个端点范围内。

### 3.2 审批闭环（26）

- [x] `GET /leader/month-plan-approvals`
- [x] `POST /leader/month-plan-approvals/batch-approve`
- [x] `POST /leader/month-plan-approvals/batch-reject`
- [x] `GET /leader/month-plan-approvals/page`
- [x] `GET /leader/month-plan-approvals/{id}`
- [x] `POST /leader/month-plan-approvals/{id}/approve`
- [x] `POST /leader/month-plan-approvals/{id}/reject`
- [x] `GET /leader/month-plan-approvals/{id}/template-download`
- [x] `GET /department/month-plan-approvals`
- [x] `GET /department/month-plan-approvals/page`
- [x] `GET /department/month-plan-approvals/{id}`
- [x] `POST /department/leader-month-plan-approvals/batch-approve`
- [x] `POST /department/leader-month-plan-approvals/batch-reject`
- [x] `GET /department/leader-month-plan-approvals/page`
- [x] `GET /department/leader-month-plan-approvals/{id}`
- [x] `POST /department/leader-month-plan-approvals/{id}/approve`
- [x] `POST /department/leader-month-plan-approvals/{id}/reject`
- [x] `GET /department/leader-month-plan-approvals/{id}/template-download`
- [x] `GET /department/week-plan-approvals`
- [x] `GET /department/week-plan-approvals/{id}`
- [x] `POST /department/week-plan-approvals/{id}/approve`
- [x] `POST /department/week-plan-approvals/{id}/reject`
- [x] `GET /leader/week-plan-approvals`
- [x] `GET /leader/week-plan-approvals/{id}`
- [x] `POST /leader/week-plan-approvals/{id}/approve`
- [x] `POST /leader/week-plan-approvals/{id}/reject`

### 3.3 领导点评闭环（24）

- [x] `GET /leader/daily-reviews`
- [x] `POST /leader/daily-reviews/batch-comment`
- [x] `POST /leader/daily-reviews/batch-risk`
- [x] `GET /leader/daily-reviews/{id}`
- [x] `POST /leader/daily-reviews/{id}/comment`
- [x] `POST /leader/daily-reviews/{id}/risk`
- [x] `GET /department/daily-reviews`
- [x] `POST /department/daily-reviews/batch-comment`
- [x] `GET /department/daily-reviews/{id}`
- [x] `POST /department/daily-reviews/{id}/comment`
- [x] `GET /department/day-plan-reviews/{id}`
- [x] `POST /department/day-plan-reviews/{id}/approve`
- [x] `POST /department/day-plan-reviews/{id}/reject`
- [x] `GET /leader/result-suggestions`
- [x] `POST /leader/result-suggestions/batch-suggest`
- [x] `GET /leader/result-suggestions/{id}`
- [x] `GET /leader/result-suggestions/{id}/evidence/{evidenceId}`
- [x] `GET /leader/result-suggestions/{id}/evidence/{evidenceId}/archive-entry`
- [x] `GET /leader/result-suggestions/{id}/evidence/{evidenceId}/preview`
- [x] `POST /leader/result-suggestions/{id}/suggest`
- [x] `GET /department/result-confirms/{id}`
- [x] `POST /department/result-confirms/{id}/confirm`
- [x] `GET /department/result-confirms/{id}/evidence/{evidenceId}`
- [x] `POST /department/result-confirms/{id}/reject`

### 3.4 部门管理（18）

- [x] `POST /department/todos/{id}/remind`
- [x] `POST /department/todos/{id}/read`
- [x] `POST /department/todos/{id}/escalate`
- [x] `POST /department/todos/{id}/done`
- [x] `POST /department/todos/batch-remind`
- [x] `POST /department/todos/batch-escalate`
- [x] `GET /department/appeals/{id}`
- [x] `POST /department/appeals/{id}/accept`
- [x] `GET /department/appeals/{id}/package`
- [x] `POST /department/appeals/{id}/resolve`
- [x] `GET /employee/appeals`
- [x] `POST /employee/appeals`
- [x] `GET /employee/appeals/options`
- [x] `GET /employee/appeals/{id}/package`
- [x] `POST /employee/plan-adjustments`
- [x] `GET /leader/plan-adjustments`
- [x] `GET /leader/plan-adjustments/{id}`
- [x] `POST /leader/plan-adjustments/{id}/process`

### 3.5 员工账号（7）

> 依赖批次 0.3 的 `python-multipart`

- [x] `POST /system/users/register`
- [x] `POST /system/users/import`
- [x] `GET /system/users/import-template`
- [x] `GET /system/users/export`
- [x] `PUT /system/users/{id}`
- [x] `POST /system/users/{id}/reset-password`
- [x] `POST /system/users/{id}/status`

### 3.6 配置类 CRUD（10）

> 路径已在批次 1 改名为复数形式，此处补齐写操作

- [x] `POST /department/templates`
- [x] `PUT /department/templates/{id}`
- [x] `POST /department/templates/{id}/{enable|disable}`
- [x] `POST /department/acceptance-standards`
- [x] `PUT /department/acceptance-standards/{id}`
- [x] `POST /department/acceptance-standards/{id}/{enable|disable}`
- [x] `POST /department/score-rules`
- [x] `PUT /department/score-rules/{id}`
- [x] `POST /department/score-rules/{id}/enable`
- [x] `POST /department/score-rules/{id}/simulate`

### 3.7 成果提交（11）

- [x] `POST /employee/results/submit`
- [x] `POST /employee/results/submit/excel`
- [x] `GET /employee/results/submit/options`
- [x] `GET /employee/results/{id}`
- [x] `GET /employee/results/{id}/evidence/{evidenceId}`
- [x] `GET /employee/week-results/submit/options`
- [x] `GET /employee/week-results/{id}`
- [x] `GET /employee/week-results/{id}/evidence/{evidenceId}`
- [x] `GET /employee/plan-draft-versions`
- [x] `GET /employee/plan-draft-versions/{id}`
- [x] `GET /employee/plan-draft-versions/{id}/restore`

### 3.8 争议（9）

- [x] `GET /dispute/cases`
- [x] `GET /dispute/cases/{id}`
- [x] `POST /dispute/cases/{id}/reviewers`
- [x] `DELETE /dispute/cases/{id}/reviewers/{reviewerId}`
- [x] `GET /dispute/cases/{id}/reviewer-candidates`
- [x] `POST /dispute/cases/{id}/opinions`
- [x] `POST /dispute/cases/{id}/recusal`
- [x] `GET /dispute/cases/{id}/package`
- [x] `POST /dispute/cases/{id}/decision`

### 3.9 台账报表（14）

- [x] `GET /department/department-ledgers`
- [x] `POST /department/department-ledgers/export`
- [x] `GET /leader/team-ledgers`
- [x] `POST /leader/team-ledgers/export`
- [x] `GET /employee/performance-evidence`
- [x] `POST /employee/performance-evidence/export`
- [x] `GET /leader/project-day-plan-packages`
- [x] `GET /leader/project-day-plan-packages/{id}`
- [x] `GET /leader/project-week-plan-packages`
- [x] `GET /leader/project-week-plan-packages/{id}`
- [x] `GET /leader/project-week-plan-packages/{id}/excel`
- [x] `GET /leader/project-week-result-packages`
- [x] `GET /leader/project-week-result-packages/{id}`
- [x] `GET /leader/project-week-result-packages/{id}/excel`

### 3.10 周成果审批（4）

- [x] `POST /leader/week-result-approvals/{id}/approve`
- [x] `POST /leader/week-result-approvals/{id}/reject`
- [x] `POST /department/week-result-approvals/{id}/approve`
- [x] `POST /department/week-result-approvals/{id}/reject`

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

- [x] `GET /system/ai/configs`
- [x] `POST /system/ai/configs`
- [x] `PUT /system/ai/configs/{id}`
- [x] `POST /system/ai/configs/{id}/enable`
- [x] `POST /system/ai/configs/{id}/test`
- [x] `GET /system/ai/prompts`
- [x] `POST /system/ai/prompts/publish`
- [x] `GET /system/ai/metrics`
- [x] `GET /system/ai/call-logs`
- [x] `GET /system/ai/call-logs/{id}`

### 4.2 employee/ai（16）— 依赖 LLM 集成

- [x] `POST /employee/ai/month-plans/generate`
- [x] `GET /employee/ai/month-plans/context`
- [x] `POST /employee/ai/month-plans/check`
- [x] `POST /employee/ai/month-plans/items/optimize`
- [x] `POST /employee/ai/week-plans/generate`
- [x] `GET /employee/ai/week-plans/context`
- [x] `POST /employee/ai/week-plans/adjust`
- [x] `POST /employee/ai/day-plans/generate`
- [x] `GET /employee/ai/day-plans/context`
- [x] `POST /employee/ai/day-plans/adjust`
- [x] `POST /employee/ai/plan-examples/extract`
- [x] `POST /employee/ai/suggestions/{id}/actions`
- [x] `GET /employee/ai/conversations/active`
- [x] `GET /employee/ai/conversations/history`
- [x] `POST /employee/ai/conversations/new`
- [x] `GET /employee/ai/conversations/{id}/messages`

### 4.3 AI 审核（8）— 依赖 4.2

- [x] `GET /ai/reviews/capability`
- [x] `GET /ai/reviews/latest`
- [x] `POST /ai/reviews/plans/{bizType}/{bizId}/check`
- [x] `POST /ai/reviews/plans/{bizType}/{bizId}/ensure`
- [x] `POST /ai/reviews/results/preview`
- [x] `GET /leader/ai/month-plan-context`
- [x] `PUT /leader/ai/month-plan-context`
- [x] `POST /ai/requests/{requestId}/cancel`

### ✅ 验收标准

- [x] 契约脚本输出：`已对齐 226 → 267`，**缺失 0**

---

## 🔧 批次 3 交付清单（2026-11-14）

### 新增文件（7 个路由 + 2 个脚本）

| 文件 | 前缀 | 端点数 | 说明 |
|---|---|---|---|
| `backend/app/routes/employee_ai.py` | `/v1/employee/ai` | 16 | 员工侧 AI 助手：上下文、生成、调整、建议、会话 |
| `backend/app/routes/employee_week_plan.py` | `/v1/employee/week-plans` | 10 | 员工侧周计划 + 周成果提交 |
| `backend/app/routes/employee_result.py` | `/v1/employee` | 11 | 月成果提交、周成果、草稿版本 |
| `backend/app/routes/v1/system_ai.py` | `/v1/system/ai` | 10 | AI 模型配置、提示词、指标、调用日志 |
| `backend/app/routes/v1/system_users.py` | `/v1/system/users` | 7 | 用户管理、注册、导入导出 |
| `backend/app/routes/leader_suggestion.py` | `/v1/leader` | 15 | 结果建议、项目日/周计划包、项目周成果包 |
| `backend/app/routes/ai_review.py` | `/v1/ai/reviews` | 5 | AI 复核能力、复核触发、结果预览 |
| `backend/app/routes/department_confirm.py` | `/v1`（多前缀） | 13 | 部门结果确认、周计划详情、周成果审批、领导 AI 上下文 |
| `backend/scripts/smoke_batch3.py` | — | — | **新增**：批次 3 端点冒烟测试（96 断言） |

### 修改文件

| 文件 | 改动 |
|---|---|
| `backend/app/routes/dispute.py` | 新增 `dispute/cases` 10 个端点 + 4 个 Pydantic 请求模型 |
| `backend/app/routes/shared.py` | 新增 `JsonBody` 基类（修复全项目 `data: dict` 解析失败） |
| `backend/app/routes/{employee_plans,employee,leader,department}.py` | `data: dict` → `data: JsonBody`（31 个既有端点） |
| `backend/app/routes/v1/system.py` | `data: dict` → `data: JsonBody`（3 个既有端点） |
| `backend/app/main.py` | 注册 8 个新 router |
| `backend/scripts/check_api_contract.py` | 修复模板字面量截断 bug（括号配对扫描） |
| `docs/05-API契约对齐清单.md` | 基线数据、里程碑、批次 3 交付清单（158 个勾选同步更新） |

### ✅ 验收结果

- [x] 契约脚本：`已对齐 **267** / 缺失 **0** / 业务组 **0**`（`--strict` 退出码 0）
- [x] 冒烟测试：`backend/scripts/smoke_batch3.py` → **96/96 通过**
- [x] 后端 `pytest app/tests` → **322 passed, 1 skipped, 0 failed**
- [x] 前端 `vue-tsc --noEmit` → **0 errors**
- [x] `GET /openapi.json` → **HTTP 200**，336 条路径
- [x] 服务启动日志无 `Traceback` / `Duplicate Operation ID` / `ImportError`

---

## 📋 里程碑与工作量

| 批次 | 内容 | 已对齐端点数 | 预估工作量 | 状态 |
|---|---|---|---|---|
| 0 | 解锁 OpenAPI + 重启服务 | — | 0.5h | ✅ 完成 |
| 1 | 重命名 7 处 + 契约脚本 | 36 → **42** | 1d | ✅ 完成 |
| 2 | 同组补子路径 | 42 → **152** | 2-3d | ✅ 完成 |
| 3.1 | 员工计划闭环 | 152 → **216** | 1d | ✅ 完成 |
| 3 | 核心业务域 | 216 → **267** | 5-8d | ✅ **完成（缺失归零）** |
| 4 | AI 域 | — | — | ✅ 完成（与批次 3 合并交付） |

> **批次 2 实际达成 42 → 152（+110）**，远超预估的 +38。
> 原因：① 本批次 23 个目标端点全部命中；② 恢复并注册了工作区中已存在但未注册的
> `dispute.py` / `planning.py` 两个 router（`/v1/dispute/*`、`/v1/planning/*` 共 ~87 个端点），
> 它们本就是既有资产，只是此前未 `include_router`，前端调用全部 404。
>
> **批次 3 实际达成 216 → 267（+51）**，契约缺失归零。
> 除新增端点外，还顺带修复了 `data: dict` 请求体解析失败（全项目 31 个既有端点）
> 与契约脚本自身的模板字面量截断 bug。

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

## 🗃️ 阶段三：领域建模与持久化（2026-11-14）

> 详细说明（含给领导的书面汇报版本）见 [`08-阶段三领域建模交付说明.md`](08-阶段三领域建模交付说明.md)。

### 背景

契约层面已「全绿」（267 / 缺失 0），但**契约骨架 ≠ 可用功能**——
`week-plan`、`dispute`、`ai` 相关端点多数只返回固定空对象，
缺少数据表、数据迁移、持久化 CRUD 与状态机。

### ✅ 交付内容

| 交付项 | 内容 |
|---|---|
| 数据模型 | 新增 **9 张表**：`plan_draft_version`、`plan_approval`、`dispute_case`、`dispute_reviewer`、`dispute_opinion`、`ai_review`、`ai_conversation`、`ai_message`、`ai_suggestion_action` |
| 数据库迁移 | `backend/migrations/versions/0004_phase3_domain_modeling.py`（幂等、可降级、SQLite 兼容） |
| 服务层 | `services/dispute_service.py`（含状态机 + 序列化）、`services/ai_service.py`、`services/ai_review_engine.py`（确定性规则引擎） |
| 路由改造 | `routes/dispute.py`（12 端点接真实持久化 + 权限）、`routes/ai_review.py`（含 `/latest/batch`）、`routes/employee_ai.py`（会话 CRUD） |
| 状态机 | 争议：`SUBMITTED → REVIEWING → NEEDS_SUPPLEMENT → DECIDED → ARCHIVED`；非法迁移 → 409 |
| 权限 | 复用 `get_current_user` + `build_access_context`；匿名 401、越权 403 |
| 测试 | `backend/app/tests/test_phase3_domain_modeling.py` → **30 个用例全部通过** |

### ⚠️ 一处事实校正

任务描述称「week-plan / dispute / ai 接口都是空骨架」，实测**计划域已完整实现**
（`services/planning.py` 922 行 + `0003_phase3_planning` 迁移）。
本次因此**未重复建设计划 CRUD**，只补齐真正缺失的版本快照表与审批流水表。

### ✅ 验收结果（2026-11-14 实测）

- [x] 真实库 `backend/data/perf.db` 迁移成功：**49 表 / 9 新表 / 20 索引 / FK 违规 0**
- [x] `alembic downgrade -1` → `upgrade head` 往返无损；`alembic current` → `0004_phase3_domain_modeling (head)`
- [x] 后端 `pytest backend/app/tests` → **441 passed, 1 skipped, 0 failed**
- [x] 契约脚本 `--strict` → **已对齐 267 / 缺失 0**，退出码 0
- [x] 前端 `vue-tsc --noEmit` → **0 errors**
- [x] `GET /openapi.json` → **HTTP 200**，**317 条路径**
- [x] `GET /v1/health` → **HTTP 200**
- [x] 未登录访问 `/v1/dispute/cases` 与 `/v1/ai/reviews/latest` → **401** ✅
- [x] 服务启动日志无 `Traceback` / `Duplicate Operation ID` / `ImportError`

### 📋 里程碑补充

| 批次 | 内容 | 已对齐端点数 | 预估工作量 | 状态 |
|---|---|---|---|---|
| 5 | **领域建模与持久化** | 267（不变） | 3-5d | ✅ **完成（9 张新表 + CRUD + 状态机 + 30 测试）** |

---

*文档基于 2026-11-14 的实测数据生成。所有数量均来自脚本测量，非估算。*
