/**
 * ⚠️ **部门功能板块**管理 API（2026-11-21）。
 *
 * 用户投诉：「别的部门为什么会有剪辑板块？」⇒ 隔离修好了，
 * 但 HR 还需要**能自己开通 / 停用** ⇒ 否则隔离是一锤子买卖。
 *
 * ⚠️ **路径不带 `/v1`** —— vite 代理会重写（见 `vite.config.ts`）。
 */
import { http } from './http'

/** 已知板块。 */
export interface KnownModule {
  code: string
  label: string
}

/** 已开通的板块（部门 × 板块）。 */
export interface DepartmentModuleGrant {
  id: string
  departmentId: string
  departmentName: string
  moduleCode: string
  active: boolean
  note: string
}

// ── 岗位口径（2026-11-23）──────────────────────────────────────────

/**
 * ⚠️ **岗位在这块业务里的角色**。
 *
 * - `reporter` —— 要**填报**（剪辑）：收到「表格 + 表单」卡。
 * - `reviewer` —— 只**审**（编导 / 部门 leader）：收到只读汇总卡，
 *   ⚠️ **绝不给表单** —— 否则主管能替下属填，数据就串了。
 * - `none` —— **不参与**：不收任何推送。
 */
export type WorkloadRole = 'reporter' | 'reviewer' | 'none'

/** 部门 × 岗位 的考核口径。 */
export interface WorkloadPolicy {
  id: string
  departmentId: string
  departmentName?: string
  position: string
  role: WorkloadRole
  /** 该岗位要填的指标（逗号分隔）—— 空 = 用板块默认指标。 */
  metricCodes: string
  active: boolean
  note: string
}

/** 某部门的实际在岗岗位。 */
export interface DepartmentPosition {
  position: string
  role: WorkloadRole
  metricCodes: string[] | string
  /** 是否已在口径表里配过。 */
  configured: boolean
  /** 在职人数。 */
  headcount: number
}

/** 角色 → 中文标签。 */
export const WORKLOAD_ROLE_LABELS: Record<WorkloadRole, string> = {
  reporter: '要填报',
  reviewer: '审阅',
  none: '不参与',
}

/**
 * ★ 角色 → 默认填报指标（2026-11-23 角色倒转）。
 *
 * ⚠️ **为什么前端也要有一份**
 * ----------------------------
 * 后端 `pan_workload_policy.default_codes_for_role()` 是权威口径，
 * 但配置页要给 HR 看「留空会填什么」⇒
 * ⇒ 这里只是**展示用的镜像**，改了以后端为准。
 *
 * ⚠️ **剪辑填数量、主管填比率** —— 业务确认的角色分工。
 */
export const WORKLOAD_DEFAULT_METRICS: Record<'reporter' | 'reviewer', string[]> = {
  reporter: ['video_normal_count', 'video_ootd_count', 'video_agent_count'],
  reviewer: ['video_completion_rate', 'video_retention_rate'],
}

/** 指标码 → 中文名（配置页展示用）。 */
export const WORKLOAD_METRIC_LABELS: Record<string, string> = {
  video_normal_count: '普通视频',
  video_ootd_count: 'OOTD',
  video_agent_count: '智能体',
  video_completion_rate: '完播率',
  video_retention_rate: '视频留存率',
}

export const departmentModulesApi = {
  /** 已知板块（供下拉用）。 */
  getKnown: () =>
    http.get<{ items: KnownModule[] }, { items: KnownModule[] }>('/department-modules/known'),

  /** 已开通列表。 */
  getGrants: () =>
    http.get<{ items: DepartmentModuleGrant[] }, { items: DepartmentModuleGrant[] }>('/department-modules'),

  /** 开通 / 停用（幂等）。 */
  grant: (payload: {
    department_id: string
    module_code: string
    active?: boolean
    note?: string
  }) =>
    http.post<{ item: DepartmentModuleGrant }, { item: DepartmentModuleGrant }>(
      '/department-modules',
      payload,
    ),

  // ── 岗位口径（2026-11-23）────────────────────────────────────────
  // ⚠️ **板块开通 ≠ 全部门都收推送** —— 部门内部还有不同岗位，
  //    不同岗位的考核方式完全不一样 ⇒ 必须单独配。

  /** 岗位口径列表。 */
  getPolicies: (departmentId = '') =>
    http.get<{ items: WorkloadPolicy[] }, { items: WorkloadPolicy[] }>(
      '/department-modules/policies',
      { params: departmentId ? { department_id: departmentId } : {} },
    ),

  /** 配置 / 更新某部门某岗位的口径（幂等）。 */
  savePolicy: (payload: {
    department_id: string
    position: string
    role: WorkloadRole
    metric_codes?: string
    active?: boolean
    note?: string
  }) =>
    http.post<{ item: WorkloadPolicy }, { item: WorkloadPolicy }>(
      '/department-modules/policies',
      payload,
    ),

  /** 某部门的实际在岗岗位及其当前口径（配置界面用）。 */
  getPositions: (departmentId: string) =>
    http.get<{ items: DepartmentPosition[] }, { items: DepartmentPosition[] }>(
      '/department-modules/positions',
      { params: { department_id: departmentId } },
    ),
}
