import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const viewPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanListRefactored.vue', import.meta.url))
const editorPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanEdit.vue', import.meta.url))
const stylePath = fileURLToPath(new URL('../src/styles.css', import.meta.url))
const extraTaskManagerPath = fileURLToPath(new URL('../src/views/employee/components/EmployeeExtraTaskManager.vue', import.meta.url))
const extraTaskApprovalPath = fileURLToPath(new URL('../src/views/leader/ExtraTaskApproval.vue', import.meta.url))
const workspacePath = fileURLToPath(new URL('../src/navigation/workspaces.ts', import.meta.url))
const routerPath = fileURLToPath(new URL('../src/router/index.ts', import.meta.url))

/**
 * ⚠️⚠️ **小程序对照源在「仓库之外」**（2026-11-25 修复 CI 连续红）
 * ------------------------------------------------------------------
 * 这个仓库（Vue 前端）与小程序（``01-employee-performance``）是**两个独立项目**，
 * 小程序是**同级目录**而不是本仓库的子目录。
 *
 * ⚠️ **为什么这会让 CI 一直红**
 *   ``../../../01-employee-performance/...`` ⇒ **仓库外的路径**
 *   ⇒ 本地有（同级目录）⇒ 测试通过
 *   ⇒ **CI 上没有**（CI 只 checkout 本仓库）
 *   ⇒ ``readFileSync`` 抛 ``ENOENT``
 *   ⇒ **整个测试文件加载失败** ⇒ 该文件里的用例**一个都没跑**
 *      （不是「某个用例失败」，是「文件直接崩」）
 *
 * ⚠️ **为什么不能简单删掉这些对照**
 *   它们的价值是「PC 与小程序行为一致」的守卫 —— 业务明确要求两端对齐。
 *   ⇒ 做法：**在** ⇒ 照常断言；**不在** ⇒ **跳过这一条**，
 *     但**其余断言照跑**（不能因为一个对照源缺失就放过整个文件）。
 *
 * ⚠️ **为什么用 `existsSync` 而不是 try/catch**
 *   顶部 ``readFileSync`` 在**模块加载期**执行 ⇒ try/catch 包不住
 *   ⇒ 必须先探测存在性，再决定读不读。
 */
const mobileRoot = '../../../01-employee-performance/enterprise-plan-results/app/'
const mobileWorkspacesPath = fileURLToPath(new URL(`${mobileRoot}config/workspaces.js`, import.meta.url))
const mobileRoutesPath = fileURLToPath(new URL(`${mobileRoot}config/mobile-routes.js`, import.meta.url))

/** 小程序对照源是否在本机存在（CI 上为 false）。 */
const HAS_MOBILE_SOURCE =
  existsSync(mobileWorkspacesPath) && existsSync(mobileRoutesPath)

/** 小程序对照源内容；**不存在时是空串**（对应断言会被跳过）。 */
const mobileWorkspaces = HAS_MOBILE_SOURCE ? readFileSync(mobileWorkspacesPath, 'utf8') : ''
const mobileRoutes = HAS_MOBILE_SOURCE ? readFileSync(mobileRoutesPath, 'utf8') : ''

const view = readFileSync(viewPath, 'utf8')
const editor = readFileSync(editorPath, 'utf8')
const styles = readFileSync(stylePath, 'utf8')
const workspaces = readFileSync(workspacePath, 'utf8')
const router = readFileSync(routerPath, 'utf8')
const employeeApi = readFileSync(fileURLToPath(new URL('../src/api/employee.ts', import.meta.url)), 'utf8')
const leaderApi = readFileSync(fileURLToPath(new URL('../src/api/leader.ts', import.meta.url)), 'utf8')
const aiReviewApi = readFileSync(fileURLToPath(new URL('../src/api/aiReview.ts', import.meta.url)), 'utf8')

test('month navigation renders seven calendar months including months without plans', () => {
  assert.match(view, /Array\.from\(\{ length: 7 \}/)
  assert.match(view, /shiftMonth\(currentMonth\.value, index - 3\)/)
  assert.match(view, /statusLabel: plan \? status\.label : '未创建'/)
  assert.match(view, /title: plan\?\.title \|\| '该月暂无计划'/)
  assert.doesNotMatch(view, /v-if="monthCards\.length"/)
  assert.doesNotMatch(view, /@dblclick/)
})

test('month switching reloads selected-month data without reloading the plan index', () => {
  assert.match(view, /watch\(currentMonth, loadMonth\)/)
  assert.match(view, /const \[all, current\] = await Promise\.all/)
})

test('selected month is an action summary instead of a duplicate detail page', () => {
  assert.match(view, /本月行动摘要/)
  assert.doesNotMatch(view, /selectedPlanDetail\.summary/)
  assert.doesNotMatch(view, /employee-month-workspace__meta/)
  assert.match(view, /primaryActionLabel/)
  assert.match(view, /runPrimaryAction/)
  assert.match(view, /return '编辑'/)
  assert.doesNotMatch(view, /继续编制/)
  assert.match(view, /employee-month-workspace__action-buttons/)
  assert.match(view, /employee-month-workspace__summary-meta/)
  assert.match(view, /employee-month-workspace__hint/)
  assert.doesNotMatch(view, /<p><strong>\{\{ primaryActionLabel/)
  assert.match(view, /employee-month-readonly-table/)
  assert.doesNotMatch(view, /查看全部任务/)
  assert.doesNotMatch(view, /previewTaskRows/)
  assert.match(view, /:data="taskRows"/)
  assert.match(view, /String\(\$index \+ 1\)/)
  assert.doesNotMatch(view, /SELECTED PLAN/)
})

test('new month plan entry opens the existing active plan instead of creating a duplicate', () => {
  assert.match(view, /@click="goCreateSelectedMonth"/)
  assert.match(view, /selectedPlan \? '打开月计划' : '新建月计划'/)
  assert.match(view, /plan\.status !== 'CANCELED'/)
  assert.match(view, /if \(selectedPlan\.value\) return goEdit\(selectedPlan\.value\.id\)/)
  assert.match(editor, /listMonthPlansApi\(\{ mine: true, planMonth: form\.planMonth \}\)/)
  assert.match(editor, /await router\.replace\(editPath\(planId\)\)/)
})

test('regular month tasks use the editor field map in a read-only preview table', () => {
  assert.match(view, /class="employee-month-readonly-table-wrap"/)
  assert.match(view, /label="工作计划内容"/)
  assert.match(view, /row\.taskContent/)
  assert.match(view, /label="权重（%\uff09"/)
  assert.match(view, /row\.deliverable/)
  assert.match(view, /row\.quality/)
  assert.match(view, /label="完成时间段"/)
  assert.match(view, /row\.estimatedStartDate/)
  assert.match(view, /row\.estimatedEndDate/)
  assert.match(view, /label="预估价值"/)
  assert.match(view, /row\.estimatedValue/)
  assert.match(styles, /\.employee-month-readonly-table\s*\{[^}]*min-width:\s*1146px/s)
  assert.match(styles, /\.readonly-task-text\s*\{[^}]*white-space:\s*pre-wrap/s)
})

test('selected month has a compact, non-scrolling hierarchy', () => {
  assert.match(styles, /\.employee-month-browser__list\s*\{[^}]*grid-template-columns:\s*repeat\(7,\s*minmax\(0,\s*1fr\)\)/s)
  assert.match(styles, /\.employee-month-browser__list\s*\{[^}]*overflow-x:\s*hidden/s)
  assert.match(styles, /\.employee-month-card\.is-active\s*\{[^}]*border-color:/s)
  assert.match(styles, /\.employee-month-card\.is-empty/)
  assert.match(styles, /\.employee-month-workspace__head\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s*auto/s)
  assert.match(styles, /\.employee-month-workspace__action-buttons\s*\{[^}]*justify-content:\s*flex-end/s)
})

test('narrow screens wrap month cards without horizontal scrolling', () => {
  assert.match(styles, /@media \(max-width:\s*980px\)/)
  assert.match(styles, /@media \(max-width:\s*980px\)[\s\S]*?\.employee-month-browser__list\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
  assert.doesNotMatch(styles, /\.employee-month-browser__list\s*\{[^}]*overflow-x:\s*auto/s)
  assert.doesNotMatch(styles, /\.employee-month-browser__list\s*\{[^}]*scroll-snap-type:\s*x/)
})

test('extra-task business is absent from PC and mobile navigation and APIs', () => {
  // ⚠️ **小程序对照源不在时只查 PC 侧**（CI 上没有那个同级目录）
  const pcSources = [view, workspaces, router, employeeApi, leaderApi, aiReviewApi]
  const sources = HAS_MOBILE_SOURCE
    ? [...pcSources, mobileWorkspaces, mobileRoutes]
    : pcSources
  for (const source of sources) {
    assert.doesNotMatch(source, /额外任务|EXTRA_TASK|extra-task|extraTask|ExtraTask/)
  }
  assert.equal(existsSync(extraTaskManagerPath), false)
  assert.equal(existsSync(extraTaskApprovalPath), false)
})

test('小程序对照源缺失时应明确跳过而不是误报通过', () => {
  /**
   * ⚠️ **为什么要有这条自检**
   * 上面那条用例在「小程序源缺失」时**只查了 PC 侧**。
   * ⇒ 如果哪天有人把对照逻辑整个删掉、把 ``HAS_MOBILE_SOURCE``
   *   永远设成 false，这条用例会**「绿着但什么都没查」**。
   * ⇒ 所以必须有这条：**当对照源存在时，断言它确实被纳入了比对集合**。
   */
  if (!HAS_MOBILE_SOURCE) {
    // CI 环境（无对照源）⇒ 确认是「有意跳过」而不是「静默失效」
    assert.ok(
      mobileWorkspaces === '' && mobileRoutes === '',
      '对照源缺失时内容应为空串（否则说明读取路径写错了）',
    )
    return
  }
  // 本机有对照源 ⇒ 断言它确实非空（说明真的读到了，不是空串蒙混过关）
  assert.ok(mobileWorkspaces.length > 0, '小程序 workspaces.js 应非空')
  assert.ok(mobileRoutes.length > 0, '小程序 mobile-routes.js 应非空')
})
