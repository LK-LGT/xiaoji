export type WorkspaceId = 'system' | 'department' | 'leader' | 'employee' | 'dispute' | 'performance'

export interface WorkspaceMenuItem {
  label: string
  path: string
  icon: string
  permission: string
  /**
   * ⚠️ **功能板块**（部门维度）。
   *
   * 菜单项除了要满足 `permission`（角色维度：这个人能不能干这件事），
   * 还要满足 `module`（组织维度：**这个部门有没有这个业务**）。
   *
   * ⚠️ **为什么两个维度都要**
   * 2026-11-21 用户投诉：「别的部门为什么会有剪辑板块？」
   * ⇒ 只按权限过滤时，`pan:workload:view` 授予基础 `employee` 角色后，
   *   **全公司每个部门**的侧边栏都会出现「剪辑工作量」。
   *
   * ⚠️ **留空 = 不做板块限制**（通用功能对所有部门开放）。
   */
  module?: string
}

export interface WorkspaceDefinition {
  id: WorkspaceId
  title: string
  homePath: string
  permission: string
  matches: readonly string[]
  items: readonly WorkspaceMenuItem[]
}

export const WORKSPACES: readonly WorkspaceDefinition[] = [
  {
    id: 'system',
    title: '系统管理端',
    homePath: '/system/dashboard',
    permission: 'system:dashboard:view',
    matches: ['/system', '/dashboard'],
    items: [
      { label: '管理工作台', path: '/system/dashboard', icon: '▦', permission: 'system:dashboard:view' },
      { label: '考核方案管理', path: '/system/performance-schemes', icon: '', permission: 'performance:schemes:manage' },
      { label: '员工注册', path: '/system/employee-register', icon: '+', permission: 'system:employee:register' },
      { label: '员工管理', path: '/system/employees', icon: '♙', permission: 'system:employee:view' },
      { label: '部门/项目组', path: '/system/orgs', icon: '⌘', permission: 'system:org:view' },
      { label: '角色管理', path: '/system/roles', icon: '◇', permission: 'system:role:view' },
      { label: '权限管理', path: '/system/permissions', icon: '✓', permission: 'system:permission:view' },
      // ★ 部门功能板块隔离（2026-11-21）
      // ⚠️ **独立权限位**，不挂在 `system:org:view` 上 ——
      //    「能看部门树」≠「能改板块开通范围」
      { label: '部门功能板块', path: '/system/department-modules', icon: '◫', permission: 'system:department-module:manage' },
      { label: '工作日规则', path: '/system/workday-rules', icon: '◷', permission: 'system:workday:view' },
      { label: '周计划补录', path: '/system/week-plan-standard-supplements', icon: '!', permission: 'system:week-standard:view' },
      { label: '审计日志', path: '/system/audits', icon: '≡', permission: 'system:audit:view' },
      { label: 'AI 配置', path: '/system/ai', icon: '✦', permission: 'system:dashboard:view' },
      { label: '绩效分析', path: '/system/performance-analytics', icon: '◫', permission: 'system:dashboard:view' },
      { label: '企业知识', path: '/system/knowledge', icon: '◎', permission: 'system:knowledge:view' },
      { label: '飞书同步', path: '/system/feishu-sync', icon: '⇄', permission: 'system:feishu-sync:manage' },
      { label: '网盘校验', path: '/system/pan-verification', icon: '⬓', permission: 'system:pan-verification:manage' },
    ],
  },
  {
    id: 'department',
    title: '部门负责人端',
    homePath: '/department/dashboard',
    permission: 'department:dashboard:view',
    matches: ['/department'],
    items: [
      { label: '本部门考核方案', path: '/department/performance-schemes', icon: '', permission: 'performance:schemes:view' },
      { label: '部门工作台', path: '/department/dashboard', icon: '▦', permission: 'department:dashboard:view' },
      { label: '月计划审批', path: '/department/plan-approval', icon: '□', permission: 'department:month-approval:view' },
      { label: '周计划审批', path: '/department/week-plan-approval', icon: '▤', permission: 'department:month-approval:view' },
      { label: '日计划点评', path: '/department/daily-review', icon: '✓', permission: 'department:dashboard:view' },
      { label: '周成果审批', path: '/department/week-result-approval', icon: '✓', permission: 'department:result-confirm:view' },
      { label: '成果审批与查看', path: '/department/result-confirm', icon: '◇', permission: 'department:result-confirm:view' },
      { label: '通知待办', path: '/department/todo', icon: '!', permission: 'department:todo:view' },
      { label: '交付物模板', path: '/department/template', icon: '□', permission: 'department:template:view' },
      { label: '验收标准', path: '/department/standard', icon: '◆', permission: 'department:standard:view' },
      { label: '参考分规则', path: '/department/score-rule', icon: '≈', permission: 'department:score-rule:view' },
      { label: '导出任务', path: '/department/export-tasks', icon: '↓', permission: 'department:export-task:view' },
      // ★ BP-2：部门负责人核对/裁定下属的剪辑工作量
      // ⚠️ `module: 'pan_workload'` —— 只对**开通了剪辑工作量板块的部门**显示
      { label: '剪辑工作量', path: '/performance/pan-workload', icon: '▦', permission: 'pan:workload:view', module: 'pan_workload' },
      { label: '绩效问答', path: '/qa', icon: '?', permission: 'performance:dashboard:view' },
    ],
  },
  {
    id: 'leader',
    title: '直属领导端',
    homePath: '/leader/workbench',
    permission: 'leader:workbench:view',
    matches: ['/leader'],
    items: [
      { label: '工作台', path: '/leader/workbench', icon: '▦', permission: 'leader:workbench:view' },
      { label: '我的月计划', path: '/leader/my-month-plans', icon: '□', permission: 'leader:workbench:view' },
      { label: '我的周计划', path: '/leader/my-week-plans', icon: '▤', permission: 'leader:workbench:view' },
      { label: '我的日计划', path: '/leader/my-day-plans', icon: '◷', permission: 'leader:workbench:view' },
      { label: '我的成果', path: '/leader/results', icon: '◇', permission: 'leader:workbench:view' },
      { label: '月计划审批', path: '/leader/month-plan-approval', icon: '✓', permission: 'leader:month-approval:view' },
      { label: '周计划审批', path: '/leader/week-plan-approval', icon: '▤', permission: 'leader:workbench:view' },
      { label: '日计划点评', path: '/leader/daily-review', icon: '✓', permission: 'leader:daily-review:view' },
      { label: '员工成果', path: '/leader/result-suggest', icon: '✓', permission: 'leader:result-suggest:view' },
      // ★ BP-2：主管核对下属的剪辑工作量 + 裁定异议工单
      // ⚠️ `module: 'pan_workload'` —— 只对**开通了剪辑工作量板块的部门**显示
      { label: '剪辑工作量', path: '/performance/pan-workload', icon: '▦', permission: 'pan:workload:view', module: 'pan_workload' },
      { label: '绩效问答', path: '/qa', icon: '?', permission: 'performance:dashboard:view' },
      { label: '文档入库', path: '/leader/knowledge', icon: '↑', permission: 'leader:workbench:view' },
    ],
  },
  {
    id: 'employee',
    title: '员工工作台',
    homePath: '/employee/dashboard',
    permission: 'dashboard:view',
    matches: ['/employee', '/planning'],
    items: [
      { label: '工作台', path: '/employee/dashboard', icon: '▦', permission: 'dashboard:view' },
      { label: '月计划', path: '/employee/month-plans', icon: '□', permission: 'planning:month:view' },
      { label: '周计划', path: '/employee/week-plans', icon: '▤', permission: 'dashboard:view' },
      { label: '日计划', path: '/employee/day-plans', icon: '◷', permission: 'planning:day:view' },
      { label: '成果管理', path: '/employee/results', icon: '◇', permission: 'planning:result:view' },
      { label: '绩效依据', path: '/employee/performance-evidence', icon: '◇', permission: 'dashboard:view' },
      // ★ BP-2：员工填报自己的工作量 + 提出异议
      // ⚠️ `module: 'pan_workload'` —— 只对**开通了剪辑工作量板块的部门**显示
      { label: '剪辑工作量', path: '/performance/pan-workload', icon: '▦', permission: 'pan:workload:view', module: 'pan_workload' },
      { label: '绩效问答', path: '/qa', icon: '?', permission: 'performance:dashboard:view' },
      { label: '申诉记录', path: '/employee/appeals', icon: '!', permission: 'dashboard:view' },
      { label: '复核与校准中心', path: '/employee/review-center', icon: '', permission: 'dashboard:view' },
    ],
  },
  {
    id: 'dispute',
    title: '裁决工作台',
    homePath: '/dispute/dashboard',
    permission: 'dispute:dashboard:view',
    matches: ['/dispute'],
    items: [
      { label: '工作台', path: '/dispute/dashboard', icon: '▣', permission: 'dispute:dashboard:view' },
      { label: '争议案件', path: '/dispute/cases', icon: '!', permission: 'dispute:case:view' },
      { label: '复核与校准中心', path: '/dispute/review-center', icon: '', permission: 'dispute:case:view' },
    ],
  },
  {
    id: 'performance',
    title: '绩效管理',
    homePath: '/performance/dashboard',
    permission: 'performance:dashboard:view',
    matches: ['/performance', '/qa'],
    items: [
      { label: '管理工作台', path: '/performance/dashboard', icon: '★', permission: 'performance:dashboard:view' },
      { label: '按方案填报与评价', path: '/performance/scheme-filling', icon: '', permission: 'performance:dashboard:view' },
      { label: '员工自评', path: '/performance/self-reviews', icon: '✎', permission: 'performance:self-review:view' },
      { label: '主管评分', path: '/performance/manager-reviews', icon: '✓', permission: 'performance:manager-review:view' },
      // ★ BP-2：网盘剪辑工作量（员工填报 / 主管核对裁定）
      // ⚠️ `module: 'pan_workload'` —— 只对**开通了剪辑工作量板块的部门**显示
      { label: '剪辑工作量', path: '/performance/pan-workload', icon: '▦', permission: 'pan:workload:view', module: 'pan_workload' },
      { label: '绩效问答', path: '/qa', icon: '?', permission: 'performance:dashboard:view' },
    ],
  },
]

export function hasPermission(permissions: readonly string[], required?: string) {
  if (!required) return true
  if (required === '*') return true
  // 检查精确权限
  if (permissions.includes(required)) return true
  // 检查通配符权限（例如 "*" 表示所有权限）
  if (permissions.includes('*')) return true
  return false
}

/**
 * ⚠️ **板块判定**（部门维度）。
 *
 * 菜单项要显示，**必须同时**满足：
 * 1. `permission` —— 这个人**能不能干**这件事（角色维度）
 * 2. `module` —— 这个**部门有没有**这个业务（组织维度）
 *
 * ⚠️ **为什么必须两个都要**
 * 2026-11-21 用户投诉：「别的部门为什么会有剪辑板块？」
 * ⇒ 只按权限过滤时，`pan:workload:view` 授予基础 `employee` 角色后，
 *   **全公司每个部门**的侧边栏都会出现「剪辑工作量」入口
 *   ⇒ 仓储部 / 客服部的人点进去发现自己一条数据都没有。
 *
 * ⚠️ **模块没声明 = 不做板块限制**（通用功能对所有部门开放）
 *
 * ⚠️ **模块列表为空 = 该部门一个板块都没开通**
 * ⇒ 所有带 `module` 的菜单项都不显示。
 * ⚠️ 这是**故意的** —— 「默认关闭」比「默认全开」安全
 * （漏配导致「功能没出现」，可见、可修）。
 */
export function hasModule(modules: readonly string[], required?: string) {
  if (!required) return true
  if (required === '*') return true
  return modules.includes(required)
}

export function availableWorkspaces(permissions: readonly string[]) {
  return WORKSPACES.filter((workspace) => hasPermission(permissions, workspace.permission))
}

export function defaultWorkspacePath(permissions: readonly string[]) {
  return availableWorkspaces(permissions)[0]?.homePath
}

export function workspaceForPath(path: string) {
  return WORKSPACES.find((workspace) => workspace.matches.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  ))
}

/**
 * 取当前工作区里**该显示**的菜单项。
 *
 * ⚠️ **两个维度都要过**：
 * - `permissions`（角色维度）—— 没权限的不显示
 * - `modules`（部门维度）—— 部门没开通这个板块的不显示
 */
export function visibleWorkspaceItems(
  workspace: WorkspaceDefinition,
  permissions: readonly string[],
  modules: readonly string[] = [],
) {
  return workspace.items.filter((item) =>
    hasPermission(permissions, item.permission) && hasModule(modules, item.module),
  )
}

/**
 * ⚠️ **这个路径对应的菜单项是否对当前用户可见** —— 路由守卫用。
 *
 * ⚠️ **为什么路由也要守卫**
 * 只隐藏菜单只是**表象** —— 用户仍可直接敲 URL 访问
 * ⇒ 必须前端路由 + 后端 API **两层都拦**。
 *
 * ⚠️ **找不到菜单项 ⇒ 放行**
 * 有些路由本来就没有对应菜单项（如详情页、兼容别名）
 * ⇒ 不能因为「菜单里没有」就把合法用户挡在外面。
 *
 * ⚠️ **这里只判「板块」，不判「权限」**
 * 权限由**路由自己的 `meta.permission`** 判定（``router.beforeEach`` 里先做），
 * 菜单项上的权限是**菜单**的准入条件 —— 两者不总是同一个。
 * ⇒ 这里再判一次权限会导致「路由允许、菜单不允许」的路径被误伤
 *   （例如详情页挂在某个受限菜单项下）。
 */
export function canAccessPath(
  path: string,
  _permissions: readonly string[] = [],
  modules: readonly string[] = [],
) {
  const workspace = workspaceForPath(path)
  if (!workspace) return true
  const item = workspace.items.find((entry) =>
    entry.path === path || path.startsWith(`${entry.path}/`),
  )
  if (!item) return true
  return hasModule(modules, item.module)
}

