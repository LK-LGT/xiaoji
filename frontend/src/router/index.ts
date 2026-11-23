import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { defaultWorkspacePath, hasPermission } from '@/navigation/workspaces'
import { resolveLegacyResultPath } from '@/navigation/resultRoutes'
import { useAuthStore } from '@/stores/auth'
import { pinia } from '@/stores'
import { withAppShell } from '@/router/layout'

// 扩展 Vue Router 的路由 Meta 类型
declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    permission?: string
    passwordChange?: boolean
  }
}

// ---------------------------------------------------------------------------
// 路由组件一律使用「静态 import」。
//
// 止血原因：此前所有页面都是 () => import(...) 懒加载，在
// @vitejs/plugin-vue 5.2.4 + vite 6.4.3 组合下偶发出现模块 default 为
// undefined 的情况，Vue Router 解析出来的组件是 undefined，<router-view />
// 直接渲染成空注释节点（<!---->），表现为「左侧菜单能点、右侧整页空白」。
//
// 静态 import 让 Vite 在模块图里一次性解析出真实的组件对象，
// 从根上绕开动态 chunk 的 default 解析问题。
// 后续业务稳定后可以再按需切回懒加载，但必须配合 Suspense + 错误兜底。
// ---------------------------------------------------------------------------

import Login from '@/views/Login.vue'
import ChangePassword from '@/views/ChangePassword.vue'
import FeishuCallback from '@/views/FeishuCallback.vue'
import NoAccess from '@/views/NoAccess.vue'
import MessageCenter from '@/views/MessageCenter.vue'
import UserProfile from '@/views/profile/UserProfile.vue'

import EmployeeDashboard from '@/views/employee/EmployeeDashboard.vue'
import EmployeeMonthPlanEdit from '@/views/employee/EmployeeMonthPlanEdit.vue'
import EmployeeMonthPlanAiWorkspace from '@/views/employee/EmployeeMonthPlanAiWorkspace.vue'
import EmployeeDayPlanEdit from '@/views/employee/EmployeeDayPlanEdit.vue'
import EmployeePerformanceEvidence from '@/views/employee/EmployeePerformanceEvidence.vue'
import EmployeeAppeals from '@/views/employee/EmployeeAppeals.vue'
import EmployeeMonthPlanList from '@/views/employee/EmployeeMonthPlanListRefactored.vue'
import EmployeeDayPlanList from '@/views/employee/EmployeeDayPlanList.vue'
import EmployeeWeekPlanList from '@/views/employee/EmployeeWeekPlanList.vue'
import EmployeeWeekPlanEdit from '@/views/employee/EmployeeWeekPlanEdit.vue'
import EmployeeWeekPlanDetail from '@/views/employee/EmployeeWeekPlanDetail.vue'
import EmployeeResultCenter from '@/views/employee/EmployeeResultCenter.vue'
import EmployeeResultPackageWorkspace from '@/views/employee/EmployeeResultPackageWorkspace.vue'
import EmployeeResultArchiveDetail from '@/views/employee/EmployeeResultArchiveDetail.vue'

import LeaderWorkbench from '@/views/leader/LeaderWorkbench.vue'
import LeaderEmployeeWork from '@/views/leader/LeaderEmployeeWork.vue'
import DailyReview from '@/views/leader/DailyReview.vue'
import WeekPlanApproval from '@/views/leader/WeekPlanApproval.vue'
import WeekResultApproval from '@/views/leader/WeekResultApproval.vue'
import ResultSuggest from '@/views/leader/ResultSuggest.vue'
import PlanAdjust from '@/views/leader/PlanAdjust.vue'
import TeamLedger from '@/views/leader/TeamLedger.vue'
import AiMonthPlanContext from '@/views/leader/AiMonthPlanContext.vue'
import LeaderKnowledge from '@/views/leader/LeaderKnowledge.vue'

import DepartmentDashboard from '@/views/department/DepartmentDashboard.vue'
import MonthPlanApproval from '@/views/department/MonthPlanApproval.vue'
import ResultConfirm from '@/views/department/ResultConfirm.vue'
import Todo from '@/views/department/Todo.vue'
import DeliverableTemplate from '@/views/department/DeliverableTemplate.vue'
import AcceptanceStandard from '@/views/department/AcceptanceStandard.vue'
import ScoreRule from '@/views/department/ScoreRule.vue'
import DepartmentLedger from '@/views/department/DepartmentLedger.vue'
import ExportTasks from '@/views/department/ExportTasks.vue'

import SystemDashboard from '@/views/system/SystemDashboard.vue'
import EmployeeRegister from '@/views/system/EmployeeRegister.vue'
import EmployeeManagement from '@/views/system/EmployeeManagement.vue'
import OrgManagement from '@/views/system/OrgManagement.vue'
import RoleManagement from '@/views/system/RoleManagement.vue'
import PermissionManagement from '@/views/system/PermissionManagement.vue'
import WorkdayRules from '@/views/system/WorkdayRules.vue'
import WeekPlanStandardSupplement from '@/views/system/WeekPlanStandardSupplement.vue'
import AuditLog from '@/views/system/AuditLog.vue'
import AiManagement from '@/views/system/AiManagement.vue'
import PerformanceAnalytics from '@/views/system/PerformanceAnalytics.vue'
import EnterpriseKnowledge from '@/views/system/EnterpriseKnowledge.vue'
import FeishuSyncManagement from '@/views/system/FeishuSyncManagement.vue'
import PanVerificationManagement from '@/views/system/PanVerificationManagement.vue'

import SchemeFilling from '@/views/performance/SchemeFilling.vue'
import SchemeManagement from '@/views/performance/SchemeManagement.vue'
import PerformanceDashboard from '@/views/performance/PerformanceDashboard.vue'
import EmployeeSelfReview from '@/views/performance/EmployeeSelfReview.vue'
import ManagerReview from '@/views/performance/ManagerReview.vue'
import PanWorkloadPanel from '@/views/performance/PanWorkloadPanel.vue'
import PanWorkloadEntry from '@/views/performance/PanWorkloadEntry.vue'
import PanWorkloadPage from '@/views/performance/PanWorkloadPage.vue'
import QaPanel from '@/views/qa/QaPanel.vue'

import ReviewCenter from '@/views/dispute/ReviewCenter.vue'
import DisputeDashboard from '@/views/dispute/DisputeDashboard.vue'
import DisputeCases from '@/views/dispute/DisputeCases.vue'
import DisputeCaseDetail from '@/views/dispute/DisputeCaseDetail.vue'
import DisputeReviewPanel from '@/views/dispute/DisputeReviewPanel.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // --- 独立页面（不套后台框架） ---
    { path: '/login', component: Login, meta: { public: true } },
    { path: '/change-password', component: ChangePassword, meta: { passwordChange: true } },
    // ⚠️ FS-2：飞书免登 OAuth 回调页。
    //    必须 `public: true` —— 用户此刻**还没有本系统 token**
    //    （token 是回调页里用 code 换出来的），走全局守卫会被拦回 /login，
    //    形成「回调 → 被踢去登录 → 回调永不发生」的死循环。
    { path: '/feishu/callback', component: FeishuCallback, meta: { public: true } },

    // --- 后台框架页面 ---
    withAppShell('/', NoAccess),
    withAppShell('/no-access', NoAccess),
    withAppShell('/messages', MessageCenter),
    withAppShell('/employee/review-center', ReviewCenter),
    withAppShell('/dispute/review-center', ReviewCenter),
    withAppShell('/profile', UserProfile),

    // --- 领导工作台 ---
    withAppShell('/leader/workbench', LeaderWorkbench, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/employees/:ownerId/work', LeaderEmployeeWork, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-month-plans', EmployeeMonthPlanList, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-month-plans/new/edit', EmployeeMonthPlanEdit, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-month-plans/:id/edit', EmployeeMonthPlanEdit, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-month-plans/:id/ai', EmployeeMonthPlanAiWorkspace, { permission: 'leader:workbench:view' }),
    { path: '/leader/my-month-plans/:id', redirect: '/leader/my-month-plans' },
    withAppShell('/leader/my-week-plans', EmployeeWeekPlanList, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-week-plans/new/edit', EmployeeWeekPlanEdit, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-week-plans/:id/edit', EmployeeWeekPlanEdit, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-week-plans/:id', EmployeeWeekPlanDetail, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-day-plans', EmployeeDayPlanList, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/my-day-plans/edit', EmployeeDayPlanEdit, { permission: 'leader:workbench:view' }),
    {
      path: '/leader/results/submit',
      redirect: (to) => resolveLegacyResultPath('/leader/results/submit', to.query),
      meta: { permission: 'leader:workbench:view' },
    },
    withAppShell('/leader/results/workspace/:type/:planId', EmployeeResultPackageWorkspace, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/results/archive/:type/:resultId', EmployeeResultArchiveDetail, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/results', EmployeeResultCenter, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/month-plan-approval', MonthPlanApproval, { permission: 'leader:month-approval:view' }),
    withAppShell('/leader/daily-review', DailyReview, { permission: 'leader:daily-review:view' }),
    withAppShell('/leader/week-plan-approval', WeekPlanApproval, { permission: 'leader:workbench:view' }),
    {
      path: '/leader/week-result-approval',
      redirect: (to) => resolveLegacyResultPath('/leader/week-result-approval', to.query),
      meta: { permission: 'leader:result-suggest:view' },
    },
    withAppShell('/leader/result-suggest', ResultSuggest, { permission: 'leader:result-suggest:view' }),
    withAppShell('/leader/plan-adjust', PlanAdjust, { permission: 'leader:plan-adjust:view' }),
    withAppShell('/leader/team-ledger', TeamLedger, { permission: 'leader:team-ledger:view' }),
    withAppShell('/leader/ai-month-context', AiMonthPlanContext, { permission: 'leader:workbench:view' }),
    withAppShell('/leader/knowledge', LeaderKnowledge, { permission: 'leader:workbench:view' }),

    // --- 部门管理 ---
    withAppShell('/department/dashboard', DepartmentDashboard, { permission: 'department:dashboard:view' }),
    withAppShell('/department/employees/:ownerId/work', LeaderEmployeeWork, { permission: 'department:dashboard:view' }),
    withAppShell('/department/plan-approval', MonthPlanApproval, { permission: 'department:month-approval:view' }),
    withAppShell('/department/week-plan-approval', WeekPlanApproval, { permission: 'department:month-approval:view' }),
    withAppShell('/department/week-result-approval', WeekResultApproval, { permission: 'department:result-confirm:view' }),
    withAppShell('/department/daily-review', DailyReview, { permission: 'department:dashboard:view' }),
    withAppShell('/department/result-confirm', ResultConfirm, { permission: 'department:result-confirm:view' }),
    withAppShell('/department/todo', Todo, { permission: 'department:todo:view' }),
    withAppShell('/department/template', DeliverableTemplate, { permission: 'department:template:view' }),
    withAppShell('/department/standard', AcceptanceStandard, { permission: 'department:standard:view' }),
    withAppShell('/department/score-rule', ScoreRule, { permission: 'department:score-rule:view' }),
    withAppShell('/department/department-ledger', DepartmentLedger, { permission: 'department:department-ledger:view' }),
    withAppShell('/department/export-tasks', ExportTasks, { permission: 'department:export-task:view' }),

    // --- 员工工作台 ---
    withAppShell('/employee/dashboard', EmployeeDashboard, { permission: 'dashboard:view' }, '/employee/workbench'),
    { path: '/employee/month-detail', redirect: '/employee/month-plans' },
    { path: '/employee/month-plans/:id', redirect: '/employee/month-plans' },
    withAppShell('/employee/month-edit', EmployeeMonthPlanEdit, { permission: 'planning:month:view' }, ['/employee/month-plans/new/edit', '/employee/month-plans/:id/edit']),
    withAppShell('/employee/month-plans/:id/ai', EmployeeMonthPlanAiWorkspace, { permission: 'planning:month:view' }),
    withAppShell('/employee/daily-plan', EmployeeDayPlanEdit, { permission: 'planning:day:view' }, '/employee/day-plans/edit'),
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
    withAppShell('/employee/ledger', EmployeePerformanceEvidence, { permission: 'dashboard:view' }, '/employee/performance-evidence'),
    withAppShell('/employee/appeal', EmployeeAppeals, { permission: 'dashboard:view' }, '/employee/appeals'),
    withAppShell('/employee/month-plans', EmployeeMonthPlanList, { permission: 'planning:month:view' }, '/planning/month'),
    withAppShell('/employee/week-plans', EmployeeWeekPlanList, { permission: 'dashboard:view' }, '/planning/week'),
    withAppShell('/employee/week-plans/new/edit', EmployeeWeekPlanEdit, { permission: 'dashboard:view' }),
    withAppShell('/employee/week-plans/:id/edit', EmployeeWeekPlanEdit, { permission: 'dashboard:view' }),
    {
      path: '/employee/week-plans/:weekPlanId/items/:itemId/results/submit',
      redirect: (to) => ({
        path: '/employee/results/submit',
        query: { planType: 'WEEK', weekPlanId: String(to.params.weekPlanId), weekPlanItemId: String(to.params.itemId) },
      }),
      meta: { permission: 'dashboard:view' },
    },
    withAppShell('/employee/week-plans/:id', EmployeeWeekPlanDetail, { permission: 'dashboard:view' }),
    withAppShell('/employee/day-plans', EmployeeDayPlanList, { permission: 'planning:day:view' }, '/planning/day'),
    withAppShell('/employee/results/workspace/:type/:planId', EmployeeResultPackageWorkspace, { permission: 'planning:result:view' }),
    withAppShell('/employee/results/archive/:type/:resultId', EmployeeResultArchiveDetail, { permission: 'planning:result:view' }),
    withAppShell('/employee/results', EmployeeResultCenter, { permission: 'planning:result:view' }, '/planning/result'),

    withAppShell('/system/performance-schemes', SchemeManagement, { permission: 'performance:schemes:manage' }),
    withAppShell('/department/performance-schemes', SchemeManagement, { permission: 'performance:schemes:view' }),

    withAppShell('/performance/scheme-filling', SchemeFilling, { permission: 'performance:dashboard:view' }),
    // --- 绩效 ---
    withAppShell('/performance', PerformanceDashboard, { permission: 'performance:dashboard:view' }, '/performance/dashboard'),
    withAppShell('/performance/self-reviews', EmployeeSelfReview, { permission: 'performance:self-review:view' }),
    withAppShell('/performance/manager-reviews', ManagerReview, { permission: 'performance:manager-review:view' }),

    // --- ★ BP-2：网盘剪辑工作量 ---
    // ⚠️ **员工页与主管页是同一个路由**，靠权限位区分内容 ——
    //    员工进来看到「我的工作量 + 填报 + 提异议」，
    //    主管进来看到「按人汇总 + 待裁定工单」。
    //    ⇒ 页面内部按权限切换组件，避免两套路由两套菜单。
    withAppShell('/performance/pan-workload', PanWorkloadPage, { permission: 'pan:workload:view' }, '/performance/dashboard'),

    // --- 问答 ---
    // ⚠️ 全角色可见：问答的数据范围由**后端**按权限收敛
    //    （`/v1/qa/tasks` 只返回有权问答的任务），
    //    前端权限位只负责「谁能打开这个页面」。
    withAppShell('/qa', QaPanel, { permission: 'performance:dashboard:view' }),

    // --- 系统管理 ---
    withAppShell('/system/dashboard', SystemDashboard, { permission: 'system:dashboard:view' }, '/dashboard'),
    withAppShell('/system/employee-register', EmployeeRegister, { permission: 'system:employee:register' }),
    withAppShell('/system/employees', EmployeeManagement, { permission: 'system:employee:view' }, '/system/users'),
    withAppShell('/system/orgs', OrgManagement, { permission: 'system:org:view' }),
    withAppShell('/system/roles', RoleManagement, { permission: 'system:role:view' }),
    withAppShell('/system/permissions', PermissionManagement, { permission: 'system:permission:view' }),
    withAppShell('/system/workday-rules', WorkdayRules, { permission: 'system:workday:view' }),
    withAppShell('/system/week-plan-standard-supplements', WeekPlanStandardSupplement, { permission: 'system:week-standard:view' }),
    withAppShell('/system/audits', AuditLog, { permission: 'system:audit:view' }),
    withAppShell('/system/ai', AiManagement, { permission: 'system:dashboard:view' }),
    withAppShell('/system/performance-analytics', PerformanceAnalytics, { permission: 'system:dashboard:view' }),
    withAppShell('/system/knowledge', EnterpriseKnowledge, { permission: 'system:knowledge:view' }),
    withAppShell('/system/feishu-sync', FeishuSyncManagement, { permission: 'system:feishu-sync:manage' }),
    withAppShell('/system/pan-verification', PanVerificationManagement, { permission: 'system:pan-verification:manage' }),

    // --- 争议处理 ---
    withAppShell('/dispute/dashboard', DisputeDashboard, { permission: 'dispute:dashboard:view' }),
    withAppShell('/dispute/cases', DisputeCases, { permission: 'dispute:case:view' }),
    withAppShell('/dispute/cases/:id', DisputeCaseDetail, { permission: 'dispute:case:view' }),
    withAppShell('/dispute/cases/:id/review-panel', DisputeReviewPanel, { permission: 'dispute:case:view' }),
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
