import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const employeeApiPath = fileURLToPath(new URL('../src/api/employee.ts', import.meta.url))
const supportActionsPath = fileURLToPath(new URL('../src/views/employee/components/EmployeeMonthPlanSupportActions.vue', import.meta.url))
const leaderApprovalPath = fileURLToPath(new URL('../src/views/department/MonthPlanApproval.vue', import.meta.url))
const leaderApiPath = fileURLToPath(new URL('../src/api/leader.ts', import.meta.url))
const employeeApi = readFileSync(employeeApiPath, 'utf8')
const supportActions = readFileSync(supportActionsPath, 'utf8')
const leaderApproval = readFileSync(leaderApprovalPath, 'utf8')
const leaderApi = readFileSync(leaderApiPath, 'utf8')

test('employee api includes month plan template preview and download endpoints', () => {
  assert.match(employeeApi, /EmployeeMonthPlanTemplatePreviewResp/)
  assert.match(employeeApi, /\/employee\/month-plans\/\$\{id\}\/template-preview/)
  assert.match(employeeApi, /\/employee\/month-plans\/\$\{id\}\/template-download/)
  assert.match(employeeApi, /responseType:\s*'blob'/)
  assert.match(supportActions, /模板预览/)
  assert.doesNotMatch(supportActions, /AI检查报告/)
  assert.match(supportActions, /申请暂停或撤销/)
  assert.match(supportActions, /employee-month-support-actions/)
  assert.doesNotMatch(supportActions, /el-dropdown/)
  assert.match(supportActions, /下载 Excel/)
  assert.match(supportActions, /getEmployeeMonthPlanTemplatePreviewApi\(props\.detail\.id\)/)
  assert.match(supportActions, /downloadEmployeeMonthPlanTemplateApi\(props\.detail\.id\)/)
})

test('leader month approval can download the shared month plan Excel template', () => {
  assert.match(leaderApi, /downloadLeaderMonthPlanTemplateApi/)
  assert.match(leaderApi, /\/leader\/month-plan-approvals\/\$\{id\}\/template-download/)
  assert.match(leaderApi, /responseType:\s*'blob'/)
  assert.match(leaderApproval, /下载 Excel/)
  assert.match(leaderApproval, /downloadLeaderMonthPlanTemplateApi\(row\.id\)/)
  assert.match(leaderApproval, /saveBlob\(blob,\s*templateFileName\(row\)\)/)
})
