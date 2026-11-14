export type WorkspaceId = 'system' | 'department' | 'leader' | 'employee' | 'dispute' | 'performance'

export interface WorkspaceMenuItem {
  label: string
  path: string
  icon: string
  permission: string
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
      { label: '员工注册', path: '/system/employee-register', icon: '+', permission: 'system:employee:register' },
      { label: '员工管理', path: '/system/employees', icon: '♙', permission: 'system:employee:view' },
      { label: '部门/项目组', path: '/system/orgs', icon: '⌘', permission: 'system:org:view' },
      { label: '角色管理', path: '/system/roles', icon: '◇', permission: 'system:role:view' },
      { label: '权限管理', path: '/system/permissions', icon: '✓', permission: 'system:permission:view' },
      { label: '工作日规则', path: '/system/workday-rules', icon: '◷', permission: 'system:workday:view' },
      { label: '周计划补录', path: '/system/week-plan-standard-supplements', icon: '!', permission: 'system:week-standard:view' },
      { label: '审计日志', path: '/system/audits', icon: '≡', permission: 'system:audit:view' },
      { label: 'AI 配置', path: '/system/ai', icon: '✦', permission: 'system:dashboard:view' },
      { label: '企业知识', path: '/system/knowledge', icon: '◎', permission: 'system:knowledge:view' },
    ],
  },
  {
    id: 'department',
    title: '部门负责人端',
    homePath: '/department/dashboard',
    permission: 'department:dashboard:view',
    matches: ['/department'],
    items: [
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
      { label: '申诉记录', path: '/employee/appeals', icon: '!', permission: 'dashboard:view' },
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
    ],
  },
  {
    id: 'performance',
    title: '绩效管理',
    homePath: '/performance/dashboard',
    permission: 'performance:dashboard:view',
    matches: ['/performance'],
    items: [
      { label: '管理工作台', path: '/performance/dashboard', icon: '★', permission: 'performance:dashboard:view' },
      { label: '员工自评', path: '/performance/self-reviews', icon: '✎', permission: 'performance:self-review:view' },
      { label: '主管评分', path: '/performance/manager-reviews', icon: '✓', permission: 'performance:manager-review:view' },
    ],
  },
]

export function hasPermission(permissions: readonly string[], required?: string) {
  return !required || permissions.includes(required)
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

export function visibleWorkspaceItems(
  workspace: WorkspaceDefinition,
  permissions: readonly string[],
) {
  return workspace.items.filter((item) => hasPermission(permissions, item.permission))
}
