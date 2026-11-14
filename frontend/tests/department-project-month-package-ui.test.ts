import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const dashboardUrl = new URL('../src/views/department/DepartmentDashboard.vue', import.meta.url)
const appUrl = new URL('../src/App.vue', import.meta.url)
const departmentApiUrl = new URL('../src/api/department.ts', import.meta.url)
const approvalUrl = new URL('../src/views/department/MonthPlanApproval.vue', import.meta.url)
const resultConfirmUrl = new URL('../src/views/department/ResultConfirm.vue', import.meta.url)
const resultPackageUrl = new URL('../src/components/ProjectResultPackageTable.vue', import.meta.url)

test('department dashboard gates complete project month plans by approval progress', async () => {
  const [dashboard, api] = await Promise.all([
    readFile(dashboardUrl, 'utf8'),
    readFile(departmentApiUrl, 'utf8'),
  ])

  assert.match(dashboard, /项目组月计划/)
  assert.match(dashboard, /已通过\/总人数/)
  assert.match(dashboard, /需项目组所有成员计划审批通过后开放/)
  assert.match(dashboard, /按计划归属月份统计/)
  assert.match(dashboard, /member\.statusLabel/)
  assert.match(dashboard, /:disabled="!row\.contentAvailable"/)
  assert.match(dashboard, /getProjectMonthPlanPackageApi/)
  assert.match(dashboard, /el-collapse-item v-for="plan in packageDetail\.plans"/)
  assert.match(api, /\/department\/project-month-plan-packages/)
})

test('department dashboard exposes an obvious Chinese month filter', async () => {
  const [dashboard, app] = await Promise.all([
    readFile(dashboardUrl, 'utf8'),
    readFile(appUrl, 'utf8'),
  ])

  assert.match(dashboard, /class="package-month-filter"/)
  assert.match(dashboard, /计划月份/)
  assert.match(dashboard, /同步筛选月计划与成果/)
  assert.match(dashboard, /format="YYYY年MM月"/)
  assert.match(app, /el-config-provider :locale="zhCn"/)
  assert.match(app, /locale\/lang\/zh-cn/)
})

test('department month plan approval only handles plans assigned to the department owner', async () => {
  const approval = await readFile(approvalUrl, 'utf8')

  assert.doesNotMatch(approval, /部门全部/)
  assert.doesNotMatch(approval, /pageMonthPlanApprovalsApi/)
  assert.match(approval, /pageDepartmentLeaderMonthPlanApprovalsApi/)
  assert.match(approval, /审批 Mentor、直属领导等提交给本人的月计划/)
})

test('department result page approves leaders and opens the whole group only after everyone passes', async () => {
  const [resultConfirm, resultPackage, api] = await Promise.all([
    readFile(resultConfirmUrl, 'utf8'),
    readFile(resultPackageUrl, 'utf8'),
    readFile(departmentApiUrl, 'utf8'),
  ])

  assert.match(resultConfirm, /领导成果审批/)
  assert.match(resultConfirm, /项目组完整成果/)
  assert.match(resultConfirm, /普通员工成果由直属领导审批通过，组内领导成果由部门负责人审批通过/)
  assert.match(resultConfirm, /listResultConfirmsApi/)
  assert.match(resultConfirm, /listProjectResultPackagesApi/)
  assert.doesNotMatch(resultConfirm, /label="员工比例"/)
  assert.doesNotMatch(resultConfirm, /label="自动等级"/)
  assert.doesNotMatch(resultConfirm, /label="直属领导建议"/)
  assert.match(resultPackage, /需项目组所有员工和领导的成果全部审批通过后开放/)
  assert.match(resultPackage, /:disabled="!row\.contentAvailable"/)
  assert.match(api, /\/department\/project-result-packages/)
})

test('department dashboard uses an organization workbench instead of a full-width hierarchy block', async () => {
  const dashboard = await readFile(dashboardUrl, 'utf8')

  assert.match(dashboard, /部门工作台/)
  assert.match(dashboard, /class="department-workbench"/)
  assert.match(dashboard, /class="department-org-sidebar"/)
  assert.match(dashboard, /class="department-members"/)
  assert.match(dashboard, /visibleMonthPlanCount/)
  assert.match(dashboard, /visibleResultCount/)
  assert.match(dashboard, /v-model:page-size="employeePageSize"/)
  assert.match(dashboard, /layout="total, sizes, prev, pager, next, jumper"/)
  assert.match(dashboard, /hasEmployeePagination/)
  assert.match(dashboard, /v-if="hasEmployeePagination" class="pagination-row department-employee-pagination"/)
  assert.match(dashboard, /department-workbench--single-page/)
  assert.match(dashboard, /\.department-workbench--single-page \.department-org-sidebar \{[\s\S]*align-self: stretch;/)
  assert.match(dashboard, /\.department-workbench \{ order: 1; \}/)
  assert.match(dashboard, /\.project-plan-package \{[\s\S]*order: 2;/)
  assert.doesNotMatch(dashboard, /class="department-org-hierarchy"/)
})
