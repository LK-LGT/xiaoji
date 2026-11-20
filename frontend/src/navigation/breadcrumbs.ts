export interface BreadcrumbRouteLike {
  path: string
  query?: Record<string, unknown>
}

export interface BreadcrumbItem {
  title: string
  path?: string
}

export interface BreadcrumbNavigation {
  title: string
  breadcrumbs: BreadcrumbItem[]
  parentPath?: string
}

interface NavigationNode {
  title: string
  path: string
  parentPath?: string
}

const ROOT_NODES: Record<string, NavigationNode> = {
  '/system/dashboard': { title: '管理工作台', path: '/system/dashboard' },
  '/department/dashboard': { title: '部门工作台', path: '/department/dashboard' },
  '/leader/workbench': { title: '直属领导工作台', path: '/leader/workbench' },
  '/employee/dashboard': { title: '员工工作台', path: '/employee/dashboard' },
  '/dispute/dashboard': { title: '裁决工作台', path: '/dispute/dashboard' },
}

const SECTION_TITLES: Record<string, string> = {
  '/system/employee-register': '员工注册',
  '/system/employees': '员工管理',
  '/system/orgs': '部门/项目组',
  '/system/roles': '角色管理',
  '/system/permissions': '权限管理',
  '/system/workday-rules': '工作日规则',
  '/system/audits': '审计日志',
  '/system/ai': 'AI 配置',
  '/system/knowledge': '企业知识',
  '/department/plan-approval': '月计划审批',
  '/department/week-plan-approval': '周计划审批',
  '/department/week-result-approval': '周成果审批',
  '/department/daily-review': '日计划点评',
  '/department/result-confirm': '成果审批与查看',
  '/department/todo': '通知待办',
  '/department/template': '交付物模板',
  '/department/standard': '验收标准',
  '/department/score-rule': '参考分规则',
  '/department/department-ledger': '部门台账',
  '/department/export-tasks': '导出任务',
  '/leader/my-month-plans': '我的月计划',
  '/leader/my-week-plans': '我的周计划',
  '/leader/my-day-plans': '我的日计划',
  '/leader/results': '我的成果',
  '/leader/month-plan-approval': '月计划审批',
  '/leader/week-plan-approval': '周计划审批',
  '/leader/daily-review': '日计划点评',
  '/leader/result-suggest': '员工成果',
  '/leader/plan-adjust': '计划调整',
  '/leader/team-ledger': '下属台账',
  '/leader/ai-month-context': '本月计划要求',
  '/leader/knowledge': '文档入库',
  '/employee/month-plans': '月计划',
  '/employee/week-plans': '周计划',
  '/employee/day-plans': '日计划',
  '/employee/results': '成果管理',
  '/employee/performance-evidence': '绩效依据',
  '/employee/appeals': '申诉记录',
  '/dispute/cases': '争议案件',
  '/qa': '绩效问答',
}

const WORKSPACE_HOME_BY_PREFIX: ReadonlyArray<readonly [string, string]> = [
  ['/system', '/system/dashboard'],
  ['/department', '/department/dashboard'],
  ['/leader', '/leader/workbench'],
  ['/employee', '/employee/dashboard'],
  ['/dispute', '/dispute/dashboard'],
  // ⚠️ 顺序有意义：`/qa` 必须排在 `/performance` **之前**吗？—— 不需要，
  //    两者前缀互不包含。但 `/qa` 归属「绩效管理」工作区，
  //    所以它的面包屑上级应该是绩效管理工作台。
  ['/qa', '/performance/dashboard'],
]

const EXACT_NODES: Record<string, NavigationNode> = {
  ...ROOT_NODES,
  ...Object.fromEntries(Object.entries(SECTION_TITLES).map(([path, title]) => [
    path,
    { title, path, parentPath: workspaceHomeForPath(path) },
  ])),
  '/employee/results/submit': {
    title: '成果提交',
    path: '/employee/results/submit',
    parentPath: '/employee/results',
  },
  '/leader/results/submit': {
    title: '成果提交',
    path: '/leader/results/submit',
    parentPath: '/leader/results',
  },
  '/no-access': { title: '暂无工作台权限', path: '/no-access' },
}

const EXACT_ALIASES: Record<string, string> = {
  '/dashboard': '/system/dashboard',
  '/system/users': '/system/employees',
  '/employee/workbench': '/employee/dashboard',
  '/planning/month': '/employee/month-plans',
  '/planning/week': '/employee/week-plans',
  '/planning/day': '/employee/day-plans',
  '/planning/result': '/employee/results',
  '/employee/month-detail': '/employee/month-plans',
  '/employee/result-submit': '/employee/results/submit',
  '/employee/ledger': '/employee/performance-evidence',
  '/employee/appeal': '/employee/appeals',
}

function workspaceHomeForPath(path: string) {
  return WORKSPACE_HOME_BY_PREFIX.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`))?.[1]
}

function firstQueryValue(value: unknown) {
  if (Array.isArray(value)) return value[0]
  return value
}

function positiveId(value: unknown) {
  const candidate = firstQueryValue(value)
  return typeof candidate === 'string' && /^\d+$/.test(candidate) ? candidate : undefined
}

function dynamicNode(path: string, query: Record<string, unknown> = {}): NavigationNode | undefined {
  if (path === '/employee/results/submit' && firstQueryValue(query.planType) === 'WEEK') {
    const weekPlanId = positiveId(query.weekPlanId)
    if (weekPlanId) {
      return { title: '成果提交', path, parentPath: `/employee/week-plans/${weekPlanId}` }
    }
  }

  let resultMatch = path.match(/^\/(employee|leader)\/results\/workspace\/(MONTH|WEEK)\/\d+$/)
  if (resultMatch) {
    return { title: '成果填写', path, parentPath: `/${resultMatch[1]}/results` }
  }

  resultMatch = path.match(/^\/(employee|leader)\/results\/archive\/(MONTH|WEEK)\/\d+$/)
  if (resultMatch) {
    return { title: '成果档案详情', path, parentPath: `/${resultMatch[1]}/results` }
  }

  let match = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/new\/edit$/)
  if (match) {
    const basePath = `/${match[1]}`
    return { title: '新建月计划', path, parentPath: basePath }
  }

  match = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/(\d+)\/ai$/)
  if (match) {
    return { title: '月计划 AI 助手', path, parentPath: `/${match[1]}/${match[2]}/edit` }
  }

  match = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/new\/ai$/)
  if (match) {
    return { title: '月计划 AI 助手', path, parentPath: `/${match[1]}/new/edit` }
  }

  match = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/(\d+)\/edit$/)
  if (match) {
    return { title: '月计划编制', path, parentPath: `/${match[1]}` }
  }

  match = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/(\d+)$/)
  if (match) {
    return { title: '月计划详情', path, parentPath: `/${match[1]}` }
  }

  match = path.match(/^\/(employee\/week-plans|leader\/my-week-plans)\/new\/edit$/)
  if (match) {
    return { title: '新建周计划', path, parentPath: `/${match[1]}` }
  }

  match = path.match(/^\/(employee\/week-plans|leader\/my-week-plans)\/(\d+)\/edit$/)
  if (match) {
    return { title: '周计划编制', path, parentPath: `/${match[1]}/${match[2]}` }
  }

  match = path.match(/^\/(employee\/week-plans|leader\/my-week-plans)\/(\d+)$/)
  if (match) {
    return { title: '周计划详情', path, parentPath: `/${match[1]}` }
  }

  match = path.match(/^\/dispute\/cases\/(\d+)\/review-panel$/)
  if (match) {
    return { title: '案件评审', path, parentPath: `/dispute/cases/${match[1]}` }
  }

  match = path.match(/^\/dispute\/cases\/(\d+)$/)
  if (match) {
    return { title: '案件详情', path, parentPath: '/dispute/cases' }
  }

  match = path.match(/^\/(leader|department)\/employees\/[^/]+\/work$/)
  if (match) {
    const parentPath = match[1] === 'leader' ? '/leader/team-ledger' : '/department/department-ledger'
    return { title: '员工工作记录', path, parentPath }
  }

  if (path === '/employee/month-edit') {
    return { title: '月计划编制', path, parentPath: '/employee/month-plans' }
  }

  if (path === '/employee/daily-plan' || path === '/employee/day-plans/edit') {
    return { title: '日计划编制', path, parentPath: '/employee/day-plans' }
  }

  if (path === '/leader/my-day-plans/edit') {
    return { title: '日计划编制', path, parentPath: '/leader/my-day-plans' }
  }

  return undefined
}

function resolveNode(path: string, query?: Record<string, unknown>, workspaceHome?: string) {
  const canonicalPath = EXACT_ALIASES[path] || path
  const resolved = dynamicNode(canonicalPath, query) || EXACT_NODES[canonicalPath]
  if (resolved) return resolved

  if ((canonicalPath === '/messages' || canonicalPath === '/profile') && workspaceHome) {
    return {
      title: canonicalPath === '/messages' ? '消息中心' : '用户中心',
      path: canonicalPath,
      parentPath: workspaceHome,
    }
  }

  return undefined
}

export function resolveBreadcrumbNavigation(
  route: BreadcrumbRouteLike,
  workspaceHome?: string,
): BreadcrumbNavigation {
  const currentNode = resolveNode(route.path, route.query, workspaceHome)
  if (!currentNode) {
    return { title: '成果计划', breadcrumbs: [{ title: '成果计划' }] }
  }

  const chain: NavigationNode[] = []
  const visited = new Set<string>()
  let node: NavigationNode | undefined = currentNode

  while (node && chain.length < 12 && !visited.has(node.path)) {
    chain.push(node)
    visited.add(node.path)
    node = node.parentPath ? resolveNode(node.parentPath, undefined, workspaceHome) : undefined
  }

  const ordered = chain.reverse()
  const breadcrumbs = ordered.map<BreadcrumbItem>((item, index) => ({
    title: item.title,
    ...(index < ordered.length - 1 ? { path: item.path } : {}),
  }))
  const parentPath = breadcrumbs.length > 1 ? breadcrumbs[breadcrumbs.length - 2].path : undefined

  return { title: currentNode.title, breadcrumbs, parentPath }
}
