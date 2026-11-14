import type { LocationQueryRaw } from 'vue-router'

export interface ResultRouteQuery {
  [key: string]: unknown
}

const PRESERVED_KEYS = ['planType', 'weekPlanId', 'weekPlanItemId', 'monthPlanId', 'monthPlanItemId', 'month', 'taskId', 'type', 'view']

function copyResultQuery(query: ResultRouteQuery) {
  return Object.fromEntries(PRESERVED_KEYS
    .filter((key) => query[key] !== undefined && query[key] !== '')
    .map((key) => [key, query[key]])) as LocationQueryRaw
}

export function resolveLegacyResultPath(path: string, query: ResultRouteQuery = {}): { path: string; query: LocationQueryRaw } {
  const preserved = copyResultQuery(query)
  if (path === '/leader/results/submit' || path === '/leader/results') {
    return { path: '/leader/results', query: { view: 'workbench', ...preserved } }
  }
  if (path === '/leader/week-result-approval') {
    return { path: '/leader/result-suggest', query: { ...preserved, view: 'approval', type: 'week' } }
  }
  return { path, query: preserved }
}
