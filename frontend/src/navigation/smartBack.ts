const BLOCKED_BACK_PREFIXES = ['/login', '/change-password', '/no-access']

export function isUsableBackPath(value: unknown, currentFullPath = '') {
  if (typeof value !== 'string' || !value.startsWith('/') || value === currentFullPath) return false
  return !BLOCKED_BACK_PREFIXES.some((prefix) => value === prefix || value.startsWith(`${prefix}?`))
}

export function fallbackForPath(path: string, workspaceHome?: string) {
  const weekResultMatch = path.match(/^\/employee\/week-plans\/(\d+)\/items\/\d+\/results\/submit$/)
  if (weekResultMatch) return `/employee/week-plans/${weekResultMatch[1]}`

  const weekPlanChildMatch = path.match(/^\/employee\/week-plans\/(\d+)(?:\/edit)?$/)
  if (weekPlanChildMatch) return path.endsWith('/edit') ? `/employee/week-plans/${weekPlanChildMatch[1]}` : '/employee/week-plans'

  const monthPlanEditMatch = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/(\d+)\/edit$/)
  if (monthPlanEditMatch) return `/${monthPlanEditMatch[1]}/${monthPlanEditMatch[2]}`

  const monthPlanAiMatch = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/(\d+)\/ai$/)
  if (monthPlanAiMatch) return `/${monthPlanAiMatch[1]}/${monthPlanAiMatch[2]}/edit`

  const monthPlanDetailMatch = path.match(/^\/(employee\/month-plans|leader\/my-month-plans)\/\d+$/)
  if (monthPlanDetailMatch) return `/${monthPlanDetailMatch[1]}`

  const disputeReviewMatch = path.match(/^\/dispute\/cases\/(\d+)\/review-panel$/)
  if (disputeReviewMatch) return `/dispute/cases/${disputeReviewMatch[1]}`

  if (/^\/dispute\/cases\/\d+$/.test(path)) return '/dispute/cases'

  if (path === '/employee/week-plans/new/edit') return '/employee/week-plans'
  if (path === '/employee/month-plans/new/edit') return '/employee/month-plans'
  if (path === '/leader/my-month-plans/new/edit') return '/leader/my-month-plans'
  if (path === '/leader/my-day-plans/edit') return '/leader/my-day-plans'
  if (path === '/employee/month-edit' || path === '/employee/month-detail') return '/employee/month-plans'
  if (path === '/employee/results/submit' || path === '/employee/result-submit') return '/employee/results'
  if (path === '/employee/day-plans/edit' || path === '/employee/daily-plan') return '/employee/day-plans'
  if (path === '/messages') return workspaceHome || '/'

  if (path.startsWith('/employee/')) return '/employee/dashboard'
  if (path.startsWith('/leader/')) return '/leader/workbench'
  if (path.startsWith('/department/')) return '/department/dashboard'
  if (path.startsWith('/system/')) return '/system/dashboard'
  if (path.startsWith('/dispute/')) return '/dispute/dashboard'

  return workspaceHome || '/'
}
