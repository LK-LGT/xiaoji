import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const leaderRoot = new URL('../src/views/leader/', import.meta.url)
const departmentRoot = new URL('../src/views/department/', import.meta.url)

test('approval detail drawers keep long plan text readable', async () => {
  const weekApproval = await readFile(new URL('WeekPlanApproval.vue', leaderRoot), 'utf8')
  const monthApproval = await readFile(new URL('MonthPlanApproval.vue', departmentRoot), 'utf8')

  assert.match(weekApproval, /approval-detail-table/)
  assert.match(weekApproval, /approval-text-cell/)
  assert.match(weekApproval, /white-space:\s*pre-wrap/)
  assert.match(weekApproval, /overflow-wrap:\s*anywhere/)

  assert.match(monthApproval, /plan-item-list/)
  assert.match(monthApproval, /plan-item-row__content/)
  assert.match(monthApproval, /white-space:\s*pre-wrap/)
  assert.match(monthApproval, /overflow-wrap:\s*anywhere/)
  assert.doesNotMatch(monthApproval, /\.approval-text-cell\s*\{[^}]*overflow-y:\s*auto/s)
})

test('approval list pages constrain remaining long text before opening details', async () => {
  const weekApproval = await readFile(new URL('WeekPlanApproval.vue', leaderRoot), 'utf8')
  const monthApproval = await readFile(new URL('MonthPlanApproval.vue', departmentRoot), 'utf8')

  assert.match(weekApproval, /approval-list-table/)
  assert.match(weekApproval, /approval-list-text/)
  assert.match(weekApproval, /overflow-y:\s*auto/)

  assert.match(monthApproval, /approval-list-table/)
  assert.doesNotMatch(monthApproval, /approval-list-text|label="计划摘要"/)

  assert.match(monthApproval, /row\.leaderComment \|\| '暂未填写审批意见'/)
  assert.match(weekApproval, /row\.approvalComment \|\| '待审批'/)
})

test('actionable month approval omits plan summary and hides the deliverable detail column', async () => {
  const monthApproval = await readFile(new URL('MonthPlanApproval.vue', departmentRoot), 'utf8')
  const approvalList = monthApproval.slice(0, monthApproval.indexOf('<el-drawer'))

  assert.doesNotMatch(approvalList, /label="计划摘要"|row\.content \|\| '未填写计划概述'/)
  assert.doesNotMatch(approvalList, /label="交付物"/)
})

test('month plan approval and read-only views no longer render plan summary', async () => {
  const [monthApproval, dashboard, employeeWork] = await Promise.all([
    readFile(new URL('MonthPlanApproval.vue', departmentRoot), 'utf8'),
    readFile(new URL('DepartmentDashboard.vue', departmentRoot), 'utf8'),
    readFile(new URL('LeaderEmployeeWork.vue', leaderRoot), 'utf8'),
  ])

  assert.doesNotMatch(monthApproval, /计划摘要|label="计划内容"|summary:\s*activeRow\.value\.content/)
  assert.doesNotMatch(dashboard, /<dt>计划摘要<\/dt>|plan\.workContent/)
  assert.doesNotMatch(employeeWork, /month-plan-summary|计划摘要|monthPlanDetail\.content/)
  assert.match(employeeWork, /month-plan-comment-section/)
})

test('month approval pagination supports page size and direct page jumps', async () => {
  const monthApproval = await readFile(new URL('MonthPlanApproval.vue', departmentRoot), 'utf8')

  assert.match(monthApproval, /v-model:page-size="pageSize"/)
  assert.match(monthApproval, /:page-sizes="\[10, 20, 50\]"/)
  assert.match(monthApproval, /layout="total, sizes, prev, pager, next, jumper"/)
  assert.match(monthApproval, /pageSize: pageSize\.value/)
  assert.match(monthApproval, /async function handlePageSizeChange\(size: number\)/)
  assert.match(monthApproval, /currentPage\.value = 1/)
})

test('leader month approval shows filter-aware employee submission ratios', async () => {
  const monthApproval = await readFile(new URL('MonthPlanApproval.vue', departmentRoot), 'utf8')

  assert.match(monthApproval, /\{\{ submittedCount \}\}\/\{\{ totalEmployeeCount \}\}[^\n]*提交\/总人数/)
  assert.match(monthApproval, /\{\{ approvedCount \}\}\/\{\{ totalEmployeeCount \}\}[^\n]*已通过\/总人数/)
  assert.match(monthApproval, /submittedCount\.value = data\.submittedCount/)
  assert.match(monthApproval, /approvedCount\.value = data\.approvedCount/)
  assert.match(monthApproval, /totalEmployeeCount\.value = data\.totalEmployeeCount/)
})
