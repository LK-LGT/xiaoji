/**
 * 绩效管理工作台 API（2026-11-24 新增）。
 *
 * ⚠️ **为什么单独建这个文件**
 * `PerformanceDashboard.vue` 之前是**纯假数据页** ——
 * 页面看起来完全正常（有任务、有员工、有成功提示），
 * 但**一个请求都没发**（源码里留着「这里应该调用后端 API」的 TODO）。
 *
 * ⚠️ **最坏的一类缺陷**
 * 验收的人会以为「系统跑通了」，而实际数据库里什么都没变。
 * 同类问题在 `EmployeeSelfReview.vue` 出现过（BUG-R6-41），已修。
 *
 * ⚠️ **路径约定**
 * `http` 的 baseURL 是 `/api`，Vite 代理把 `/api/xxx` 重写成 `/v1/xxx`
 * ⇒ 这里写 `/performance/...` 而不是 `/v1/performance/...`。
 *
 * ⚠️ **数据来源的选择**
 * ① **任务**用 `GET /performance/tasks`（后端已存在）
 * ② **记录**用 `GET /performance/scheme-records`
 *    —— 它内部走 `can_access_employee` 做**范围过滤**
 *    ⇒ 员工只看得到自己的、主管只看得到管辖内的、HR 看全部。
 *    ⚠️ **不要用无范围过滤的原始记录表**来喂看板，
 *       否则员工会看到同事的成绩（越权）。
 */
import { http } from './http'

/** 考核任务（列表）。 */
export interface PerformanceTaskRow {
  task_id: string
  name: string
  period: string
  status: string
  created_at: string
}

/** 考核记录（列表，已按当前用户的数据范围过滤）。 */
export interface PerformanceRecordRow {
  id: string
  task_id: string
  name: string
  period: string
  employee_name: string
  is_own: boolean
  status: string
}

/** 任务详情（含状态分布）。 */
export interface PerformanceTaskDetail {
  task_id: string
  name: string
  period: string
  status: string
  owner_id: string | null
  created_at: string
  total_records: number
  status_breakdown: Record<string, number>
}

/** 工作台统计卡片。 */
export interface DashboardStats {
  activeTasks: number
  completedRecords: number
  pendingSelfReview: number
  pendingManagerReview: number
}

/**
 * 拉取工作台数据。
 *
 * ⚠️ **响应已被拦截器拆过一层** —— `http.ts` 的响应拦截器遇到
 * `{code, data}` 会**直接返回 `body.data`**
 * ⇒ 这里拿到的是**裸数据**，不要再取 `.data`。
 *
 * ⚠️ **两个端点互不依赖 ⇒ 并行拉取**。
 */
export async function fetchDashboard() {
  const [tasks, records] = await Promise.all([
    http.get<unknown, { items: PerformanceTaskRow[] }>('/performance/tasks?limit=100'),
    http.get<unknown, PerformanceRecordRow[]>('/performance/scheme-records'),
  ])
  return {
    tasks: tasks?.items ?? [],
    records: records ?? [],
  }
}

/** 按记录状态统计（口径与后端 `status_breakdown` 一致）。 */
export function computeStats(
  tasks: PerformanceTaskRow[],
  records: PerformanceRecordRow[],
): DashboardStats {
  return {
    // ⚠️ **「进行中」= 还没定稿/归档** —— 已确认和已归档都算结束。
    activeTasks: tasks.filter((t) => t.status !== 'confirmed' && t.status !== 'archived')
      .length,
    completedRecords: records.filter((r) => r.status === 'confirmed' || r.status === 'archived')
      .length,
    // ⚠️ **「待自评」= 还没提交自评**（尚未开始 / 进行中）
    pendingSelfReview: records.filter(
      (r) => r.status === 'not_started' || r.status === 'self_review_open',
    ).length,
    // ⚠️ **「待主管评分」= 自评已交、等主管打分**
    pendingManagerReview: records.filter((r) => r.status === 'self_review_submitted').length,
  }
}

/** 任务详情。 */
export function getTaskDetail(taskId: string) {
  return http.get<unknown, PerformanceTaskDetail>(`/performance/tasks/${taskId}`)
}

/**
 * HR 确认任务最终成绩。
 *
 * ⚠️ **仅对「非方案考核」的任务有效** ——
 * 方案考核必须走 `scheme-records` 的流程（面谈 → 员工确认 → HR 定稿），
 * 后端会返回 409 提示改走那条路。
 */
export function confirmTask(taskId: string) {
  return http.post<unknown, { message: string }>(`/performance/tasks/${taskId}/confirm`)
}

/**
 * 发起考核任务（**非方案**路径）。
 *
 * ⚠️ **什么时候才用这个**
 * 方案考核（`scheme_version_id` 绑定的快照）**必须**走
 * `POST /schemes/scheme-tasks` —— 那里会按方案版本分组冻结快照。
 * 用这个端点建出来的任务**没有快照** ⇒ 主管提交评分那一刻会炸
 * 「任务没有配置快照」（真实库里发生过 22/23 的事故）。
 *
 * ⚠️ **所以工作台的「发起考核」按钮默认引导到方案管理页**，
 * 只有明确知道自己在建「无方案任务」时才调这里。
 */
export function createPerformanceTask(payload: {
  title: string
  period: string
  target_employee_ids: string[]
  start_date: string
  end_date: string
  self_review_deadline: string
  manager_review_deadline: string
}) {
  return http.post<unknown, { task_id: string; target_count: number; message: string }>(
    '/performance/tasks',
    payload,
  )
}
