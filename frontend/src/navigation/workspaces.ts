export type WorkspaceId = 'system' | 'department' | 'leader' | 'employee' | 'dispute' | 'performance'

import type { ModuleMenuGroup, ModuleMenuItem } from '@/api/auth'

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
 * ⚠️ **2026-11-24 演进**
 * ----------------------
 * 过去：菜单项**写死**在 ``WORKSPACES`` 里，靠 ``module`` 字段过滤。
 * 现在：**菜单由后端下发**（``moduleMenus``），前端只**渲染**。
 *
 * ⇒ ``hasModule`` 仍保留，因为**路由守卫**还要用它判「这个路径属不属于某个板块」
 * （后端下发的目录里没有路径 → 需要另建映射）。
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
 * ⚠️⚠️ **这个路径是不是「板块子模块」的路径**（2026-11-24）。
 *
 * ⚠️ **为什么需要单独判断**
 * 板块子模块的路径由**后端**下发（``moduleMenus``），
 * 前端**事先不知道** ⇒ 不能靠路径前缀猜。
 *
 * ⚠️ **为什么必须区分它**
 * 子模块路径被放在了 ``/performance`` 前缀下（例：``/performance/pan-workload``）
 * ⇒ ``workspaceForPath`` 会把它判成「绩效管理」工作区
 * ⇒ 用户在「系统管理端」点【短视频 → 剪辑工作量】，
 *    整个侧边栏会**跳到另一个层级**（标题、菜单、面包屑全变）
 * ⇒ 这正是用户投诉的「每次点这个板块的内容都会跳到另一个层级」。
 *
 * ⚠️ **命中规则**
 * 与 ``moduleMenus`` 里的**路径完全相等**或**以其为前缀**（详情页）。
 */
export function isModuleSubmodulePath(
  path: string,
  moduleMenus: readonly ModuleMenuGroup[] = [],
): boolean {
  for (const group of moduleMenus) {
    for (const item of group.children ?? []) {
      if (!item.path) continue
      if (path === item.path || path.startsWith(`${item.path}/`)) return true
    }
  }
  return false
}

/**
 * ★ **当前应该显示哪个工作区**（2026-11-24）。
 *
 * ⚠️ **为什么不直接用 ``workspaceForPath``**
 * ``workspaceForPath`` 是**纯路径前缀匹配** ⇒ 板块子模块路径
 * （``/performance/pan-workload``）会被判成「绩效管理」工作区
 * ⇒ 点一下就从「系统管理端」跳到「绩效管理」，用户理解为「跳到另一个层级」。
 *
 * ⚠️ **这里的规则**
 * ① 路径**命中板块子模块** ⇒ **保持当前工作区不变**
 *    （板块是挂在当前工作区下的**分组**，不是另一个工作区）
 * ② 否则 ⇒ 按路径前缀归属工作区（原有行为）
 * ③ 都不匹配 ⇒ 回落到第一个有权限的工作区（原有行为）
 *
 * ⚠️ **为什么 ① 要「保持当前」而不是「切到绩效管理」**
 * 用户诉求原文（2026-11-24）：
 * 「在【部门功能板块】这个配置菜单里做配置：…… 侧边栏菜单是动态渲染：
 *   系统读取当前登录人的部门 → 读取这个部门绑定了哪些功能子模块
 *   ⇒ 只有这个部门的人员，侧边栏才会加载出【短视频 → 剪辑工作量】」
 * ⇒ 板块分组是**当前工作区内的附加分组**，不是把用户「踢」到别的端。
 *
 * ⚠️ **为什么需要一个「记忆」而不是每次都算**
 * 首次进入板块页面时（从别处直接敲 URL），当前工作区本来就不是来源工作区
 * ⇒ 需要记住「我是从哪个工作区进来的」。
 */
export function resolveActiveWorkspace(
  path: string,
  moduleMenus: readonly ModuleMenuGroup[] = [],
  fallbackWorkspaces: readonly WorkspaceDefinition[] = [],
  rememberedWorkspaceId?: WorkspaceId,
): WorkspaceDefinition | undefined {
  // ① 命中板块子模块 ⇒ 保持当前工作区
  if (isModuleSubmodulePath(path, moduleMenus)) {
    // ⚠️ **优先用「记住的工作区」**（从哪儿点进来的就回哪儿去）
    const remembered = fallbackWorkspaces.find((w) => w.id === rememberedWorkspaceId)
    if (remembered) return remembered
    // ⚠️ **没有记忆**（直接敲 URL 进来）⇒ 用**默认落地工作区**
    //
    //    ⚠️ 为什么**不**用 ``workspaceForPath(path)``
    //    子模块路径带 ``/performance`` 前缀 ⇒ 会被判成「绩效管理」
    //    ⇒ 那正是用户投诉的「跳到另一个层级」。
    //    用户诉求原文：「HR 在自己工作区就能直接看到，不用切」。
    if (fallbackWorkspaces[0]) return fallbackWorkspaces[0]
    // ⚠️ 连默认工作区都没有 ⇒ 最后才按路径归属兜底
    return workspaceForPath(path)
  }

  // ② 普通路径 ⇒ 按前缀归属
  const matched = workspaceForPath(path)
  if (matched) return matched

  // ③ 都不匹配 ⇒ 回落
  if (rememberedWorkspaceId) {
    const remembered = fallbackWorkspaces.find((w) => w.id === rememberedWorkspaceId)
    if (remembered) return remembered
  }
  return fallbackWorkspaces[0]
}

/**
 * 取当前工作区里**该显示**的菜单项。
 *
 * ⚠️ **两个维度都要过**：
 * - `permissions`（角色维度）—— 没权限的不显示
 * - `modules`（部门维度）—— 部门没开通这个板块的不显示
 *
 * ⚠️ **2026-11-24** —— 「剪辑工作量」已从写死清单里移除
 * ⇒ 它由 ``moduleMenuItems`` 动态渲染。
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
 * ★ **动态板块菜单项**（2026-11-24）。
 *
 * ⚠️ **为什么需要这个函数**
 * ------------------------
 * 用户诉求：「侧边栏菜单**不是写死固定**，是**动态渲染**：
 *   - 系统读取当前登录人的部门；
 *   - 读取这个部门绑定了哪些功能子模块；
 *   - 只有这个部门的人员，侧边栏才会加载出【短视频 → 剪辑工作量】」
 *
 * ⇒ 后端 ``/auth/me`` 下发 ``moduleMenus``（已按**板块 + 岗位角色 + 权限**过滤）
 * ⇒ 前端这里只做**最后一道**权限校验（防止后端漏过滤）与**空组剔除**。
 *
 * ⚠️ **为什么还要再过滤一次权限**
 * 后端已经按 ``permission`` 过滤了，但前端再过一次是**纵深防御**
 * ⇒ 将来后端加了新板块忘了过滤，前端也不会把它放出来。
 *
 * ⚠️ **为什么岗位角色（none）已经不在里面了**
 * 后端判定「不参与」⇒ 整个分组不下发 ⇒ 前端无需再判。
 */
export function moduleMenuItems(
  moduleMenus: readonly ModuleMenuGroup[] = [],
  permissions: readonly string[] = [],
): ModuleMenuItem[] {
  const result: ModuleMenuItem[] = []
  for (const group of moduleMenus) {
    for (const item of group.children ?? []) {
      if (item.permission && !hasPermission(permissions, item.permission)) continue
      result.push(item)
    }
  }
  return result
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
 *
 * ⚠️ **2026-11-24：板块路径映射来自后端下发的目录**
 * 「剪辑工作量」不再写死在 ``WORKSPACES`` 里
 * ⇒ 守卫要靠 ``moduleMenus`` 才知道「这个路径属于哪个板块」
 * ⇒ 传进来的 ``modules`` 里若没有该板块 ⇒ 挡下。
 */
export function canAccessPath(
  path: string,
  _permissions: readonly string[] = [],
  modules: readonly string[] = [],
  moduleMenus: readonly ModuleMenuGroup[] = [],
) {
  // ① 写死清单里的菜单项（通用功能）
  const workspace = workspaceForPath(path)
  if (workspace) {
    const item = workspace.items.find((entry) =>
      entry.path === path || path.startsWith(`${entry.path}/`),
    )
    if (item) return hasModule(modules, item.module)
  }

  // ② ★ 动态板块菜单项（后端下发）
  //    ⇒ 路径属于某个板块子模块 ⇒ 必须该板块对当前用户开通
  for (const group of moduleMenus) {
    for (const item of group.children ?? []) {
      if (item.path === path || path.startsWith(`${item.path}/`)) {
        return hasModule(modules, item.moduleCode)
      }
    }
  }

  // ③ 找不到对应菜单项 ⇒ 放行（详情页 / 兼容别名）
  return true
}

