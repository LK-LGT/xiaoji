import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { defaultWorkspacePath, hasPermission } from '@/navigation/workspaces'
import { resolveLegacyResultPath } from '@/navigation/resultRoutes'
import { useAuthStore } from '@/stores/auth'
import { pinia } from '@/stores'

// 扩展 Vue Router 的路由 Meta 类型
declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    permission?: string
    passwordChange?: boolean
  }
}

const EmployeeDashboard = () => import('@/views/employee/EmployeeDashboard.vue')
const EmployeeMonthPlanEdit = () => import('@/views/employee/EmployeeMonthPlanEdit.vue')
const EmployeeMonthPlanAiWorkspace = () => import('@/views/employee/EmployeeMonthPlanAiWorkspace.vue')
const EmployeeDayPlanEdit = () => import('@/views/employee/EmployeeDayPlanEdit.vue')
const EmployeePerformanceEvidence = () => import('@/views/employee/EmployeePerformanceEvidence.vue')
const EmployeeAppeals = () => import('@/views/employee/EmployeeAppeals.vue')
const EmployeeMonthPlanList = () => import('@/views/employee/EmployeeMonthPlanListRefactored.vue')
const EmployeeDayPlanList = () => import('@/views/employee/EmployeeDayPlanList.vue')
const EmployeeWeekPlanList = () => import('@/views/employee/EmployeeWeekPlanList.vue')
const EmployeeWeekPlanEdit = () => import('@/views/employee/EmployeeWeekPlanEdit.vue')
const EmployeeWeekPlanDetail = () => import('@/views/employee/EmployeeWeekPlanDetail.vue')
const EmployeeResultCenter = () => import('@/views/employee/EmployeeResultCenter.vue')
const EmployeeResultPackageWorkspace = () => import('@/views/employee/EmployeeResultPackageWorkspace.vue')
const EmployeeResultArchiveDetail = () => import('@/views/employee/EmployeeResultArchiveDetail.vue')
const DisputeDashboard = () => import('@/views/dispute/DisputeDashboard.vue')
const DisputeCases = () => import('@/views/dispute/DisputeCases.vue')
const DisputeCaseDetail = () => import('@/views/dispute/DisputeCaseDetail.vue')
const DisputeReviewPanel = () => import('@/views/dispute/DisputeReviewPanel.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('@/views/Login.vue'), meta: { public: true } },
    { path: '/change-password', component: () => import('@/views/ChangePassword.vue'), meta: { passwordChange: true } },
    { path: '/', component: () => import('@/views/NoAccess.vue') },
    { path: '/no-access', component: () => import('@/views/NoAccess.vue') },
    { path: '/messages', component: () => import('@/views/MessageCenter.vue') },
    { path: '/profile', component: () => import('@/views/profile/UserProfile.vue') },
    { path: '/leader/workbench', component: () => import('@/views/leader/LeaderWorkbench.vue'), meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/employees/:ownerId/work', component: () => import('@/views/leader/LeaderEmployeeWork.vue'), meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-month-plans', component: EmployeeMonthPlanList, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-month-plans/new/edit', component: EmployeeMonthPlanEdit, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-month-plans/:id/edit', component: EmployeeMonthPlanEdit, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-month-plans/:id/ai', component: EmployeeMonthPlanAiWorkspace, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-month-plans/:id', redirect: '/leader/my-month-plans' },
    { path: '/leader/my-week-plans', component: EmployeeWeekPlanList, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-week-plans/new/edit', component: EmployeeWeekPlanEdit, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-week-plans/:id/edit', component: EmployeeWeekPlanEdit, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-week-plans/:id', component: EmployeeWeekPlanDetail, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-day-plans', component: EmployeeDayPlanList, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/my-day-plans/edit', component: EmployeeDayPlanEdit, meta: { permission: 'leader:workbench:view' } },
    {
      path: '/leader/results/submit',
      redirect: (to) => resolveLegacyResultPath('/leader/results/submit', to.query),
      meta: { permission: 'leader:workbench:view' },
    },
    { path: '/leader/results/workspace/:type/:planId', component: EmployeeResultPackageWorkspace, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/results/archive/:type/:resultId', component: EmployeeResultArchiveDetail, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/results', component: EmployeeResultCenter, meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/month-plan-approval', component: () => import('@/views/department/MonthPlanApproval.vue'), meta: { permission: 'leader:month-approval:view' } },
    { path: '/leader/daily-review', component: () => import('@/views/leader/DailyReview.vue'), meta: { permission: 'leader:daily-review:view' } },
    { path: '/leader/week-plan-approval', component: () => import('@/views/leader/WeekPlanApproval.vue'), meta: { permission: 'leader:workbench:view' } },
    {
      path: '/leader/week-result-approval',
      redirect: (to) => resolveLegacyResultPath('/leader/week-result-approval', to.query),
      meta: { permission: 'leader:result-suggest:view' },
    },
    { path: '/leader/result-suggest', component: () => import('@/views/leader/ResultSuggest.vue'), meta: { permission: 'leader:result-suggest:view' } },
    { path: '/leader/plan-adjust', component: () => import('@/views/leader/PlanAdjust.vue'), meta: { permission: 'leader:plan-adjust:view' } },
    { path: '/leader/team-ledger', component: () => import('@/views/leader/TeamLedger.vue'), meta: { permission: 'leader:team-ledger:view' } },
    { path: '/leader/ai-month-context', component: () => import('@/views/leader/AiMonthPlanContext.vue'), meta: { permission: 'leader:workbench:view' } },
    { path: '/leader/knowledge', component: () => import('@/views/leader/LeaderKnowledge.vue'), meta: { permission: 'leader:workbench:view' } },
    { path: '/department/dashboard', component: () => import('@/views/department/DepartmentDashboard.vue'), meta: { permission: 'department:dashboard:view' } },
    { path: '/department/employees/:ownerId/work', component: () => import('@/views/leader/LeaderEmployeeWork.vue'), meta: { permission: 'department:dashboard:view' } },
    { path: '/department/plan-approval', component: () => import('@/views/department/MonthPlanApproval.vue'), meta: { permission: 'department:month-approval:view' } },
    { path: '/department/week-plan-approval', component: () => import('@/views/leader/WeekPlanApproval.vue'), meta: { permission: 'department:month-approval:view' } },
    { path: '/department/week-result-approval', component: () => import('@/views/leader/WeekResultApproval.vue'), meta: { permission: 'department:result-confirm:view' } },
    { path: '/department/daily-review', component: () => import('@/views/leader/DailyReview.vue'), meta: { permission: 'department:dashboard:view' } },
    { path: '/department/result-confirm', component: () => import('@/views/department/ResultConfirm.vue'), meta: { permission: 'department:result-confirm:view' } },
    { path: '/department/todo', component: () => import('@/views/department/Todo.vue'), meta: { permission: 'department:todo:view' } },
    { path: '/department/template', component: () => import('@/views/department/DeliverableTemplate.vue'), meta: { permission: 'department:template:view' } },
    { path: '/department/standard', component: () => import('@/views/department/AcceptanceStandard.vue'), meta: { permission: 'department:standard:view' } },
    { path: '/department/score-rule', component: () => import('@/views/department/ScoreRule.vue'), meta: { permission: 'department:score-rule:view' } },
    { path: '/department/department-ledger', component: () => import('@/views/department/DepartmentLedger.vue'), meta: { permission: 'department:department-ledger:view' } },
    { path: '/department/week-plan-ledger', component: () => import('@/views/department/WeekPlanLedger.vue'), meta: { permission: 'department:dashboard:view' } },
    { path: '/department/export-tasks', component: () => import('@/views/department/ExportTasks.vue'), meta: { permission: 'department:export-task:view' } },
    { path: '/system/dashboard', alias: '/dashboard', component: () => import('@/views/system/SystemDashboard.vue'), meta: { permission: 'system:dashboard:view' } },
    { path: '/performance', alias: '/performance/dashboard', component: () => import('@/views/performance/PerformanceDashboard.vue'), meta: { permission: 'performance:dashboard:view' } },
    { path: '/performance/self-reviews', component: () => import('@/views/performance/EmployeeSelfReview.vue'), meta: { permission: 'performance:self-review:view' } },
    { path: '/performance/manager-reviews', component: () => import('@/views/performance/ManagerReview.vue'), meta: { permission: 'performance:manager-review:view' } },
    { path: '/employee/dashboard', alias: '/employee/workbench', component: EmployeeDashboard, meta: { permission: 'dashboard:view' } },
    { path: '/employee/month-detail', redirect: '/employee/month-plans' },
    { path: '/employee/month-plans/:id', redirect: '/employee/month-plans' },
    {
      path: '/employee/month-edit',
      alias: ['/employee/month-plans/new/edit', '/employee/month-plans/:id/edit'],
      component: EmployeeMonthPlanEdit,
      meta: { permission: 'planning:month:view' },
    },
    { path: '/employee/month-plans/:id/ai', component: EmployeeMonthPlanAiWorkspace, meta: { permission: 'planning:month:view' } },
    { path: '/employee/daily-plan', alias: '/employee/day-plans/edit', component: EmployeeDayPlanEdit, meta: { permission: 'planning:day:view' } },
    {
      path: '/employee/result-submit',
      alias: '/employee/results/submit',
      redirect: (to) => {
        const weekPlanId = Number(to.query.weekPlanId)
        if (Number.isInteger(weekPlanId) && weekPlanId > 0) {
          return { path: `/employee/results/workspace/WEEK/${weekPlanId}`, query: { month: to.query.weekMonth, taskId: to.query.weekPlanItemId } }
        }
        const monthPlanId = Number(to.query.monthPlanId)
        if (Number.isInteger(monthPlanId) && monthPlanId > 0) {
          return { path: `/employee/results/workspace/MONTH/${monthPlanId}`, query: { month: to.query.month, taskId: to.query.monthPlanItemId } }
        }
        return { path: '/employee/results', query: { view: 'workbench' } }
      },
      meta: { permission: 'planning:result:view' },
    },
    { path: '/employee/ledger', alias: '/employee/performance-evidence', component: EmployeePerformanceEvidence, meta: { permission: 'dashboard:view' } },
    { path: '/employee/appeal', alias: '/employee/appeals', component: EmployeeAppeals, meta: { permission: 'dashboard:view' } },
    { path: '/employee/month-plans', alias: '/planning/month', component: EmployeeMonthPlanList, meta: { permission: 'planning:month:view' } },
    { path: '/employee/week-plans', alias: '/planning/week', component: EmployeeWeekPlanList, meta: { permission: 'dashboard:view' } },
    { path: '/employee/week-plans/new/edit', component: EmployeeWeekPlanEdit, meta: { permission: 'dashboard:view' } },
    { path: '/employee/week-plans/:id/edit', component: EmployeeWeekPlanEdit, meta: { permission: 'dashboard:view' } },
    {
      path: '/employee/week-plans/:weekPlanId/items/:itemId/results/submit',
      redirect: (to) => ({
        path: '/employee/results/submit',
        query: { planType: 'WEEK', weekPlanId: String(to.params.weekPlanId), weekPlanItemId: String(to.params.itemId) },
      }),
      meta: { permission: 'dashboard:view' },
    },
    { path: '/employee/week-plans/:id', component: EmployeeWeekPlanDetail, meta: { permission: 'dashboard:view' } },
    { path: '/employee/day-plans', alias: '/planning/day', component: EmployeeDayPlanList, meta: { permission: 'planning:day:view' } },
    { path: '/employee/results/workspace/:type/:planId', component: EmployeeResultPackageWorkspace, meta: { permission: 'planning:result:view' } },
    { path: '/employee/results/archive/:type/:resultId', component: EmployeeResultArchiveDetail, meta: { permission: 'planning:result:view' } },
    { path: '/employee/results', alias: '/planning/result', component: EmployeeResultCenter, meta: { permission: 'planning:result:view' } },
    { path: '/system/employee-register', component: () => import('@/views/system/EmployeeRegister.vue'), meta: { permission: 'system:employee:register' } },
    { path: '/system/employees', alias: '/system/users', component: () => import('@/views/system/EmployeeManagement.vue'), meta: { permission: 'system:employee:view' } },
    { path: '/system/orgs', component: () => import('@/views/system/OrgManagement.vue'), meta: { permission: 'system:org:view' } },
    { path: '/system/roles', component: () => import('@/views/system/RoleManagement.vue'), meta: { permission: 'system:role:view' } },
    { path: '/system/permissions', component: () => import('@/views/system/PermissionManagement.vue'), meta: { permission: 'system:permission:view' } },
    { path: '/system/workday-rules', component: () => import('@/views/system/WorkdayRules.vue'), meta: { permission: 'system:workday:view' } },
    { path: '/system/week-plan-standard-supplements', component: () => import('@/views/system/WeekPlanStandardSupplement.vue'), meta: { permission: 'system:week-standard:view' } },
    { path: '/system/audits', component: () => import('@/views/system/AuditLog.vue'), meta: { permission: 'system:audit:view' } },
    { path: '/system/ai', component: () => import('@/views/system/AiManagement.vue'), meta: { permission: 'system:dashboard:view' } },
    { path: '/system/knowledge', component: () => import('@/views/system/EnterpriseKnowledge.vue'), meta: { permission: 'system:knowledge:view' } },
    { path: '/dispute/dashboard', component: DisputeDashboard, meta: { permission: 'dispute:dashboard:view' } },
    { path: '/dispute/cases', component: DisputeCases, meta: { permission: 'dispute:case:view' } },
    { path: '/dispute/cases/:id', component: DisputeCaseDetail, meta: { permission: 'dispute:case:view' } },
    { path: '/dispute/cases/:id/review-panel', component: DisputeReviewPanel, meta: { permission: 'dispute:case:view' } },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const token = localStorage.getItem('planning_access_token')
  if (!token) return { path: '/login', query: { redirect: to.fullPath } }
  const authStore = useAuthStore(pinia)
  if (!authStore.user) {
    try { await authStore.loadMe() }
    catch { return { path: '/login', query: { redirect: to.fullPath } } }
  }
  if (authStore.user?.forceChangePassword && to.path !== '/change-password') return '/change-password'
  const permissions = authStore.user?.permissions || []
  const home = defaultWorkspacePath(permissions)
  if (!authStore.user?.forceChangePassword && to.path === '/change-password') return home || '/no-access'
  if (to.path === '/') return home || '/no-access'
  if (to.path === '/no-access') return home || true
  const requiredPermission = typeof to.meta.permission === 'string' ? to.meta.permission : undefined
  if (!hasPermission(permissions, requiredPermission)) {
    ElMessage.warning('当前账号无权访问该页面')
    return home || '/no-access'
  }
  return true
})

export default router
