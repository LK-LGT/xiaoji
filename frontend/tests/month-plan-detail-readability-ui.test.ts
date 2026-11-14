import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
const list = read('../src/views/employee/EmployeeMonthPlanListRefactored.vue')
const router = read('../src/router/index.ts')
const detailPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanDetail.vue', import.meta.url))

test('standalone month-plan detail page is removed', () => {
  assert.equal(existsSync(detailPath), false)
  assert.doesNotMatch(router, /EmployeeMonthPlanDetail/)
  assert.match(router, /path: '\/employee\/month-plans\/:id', redirect: '\/employee\/month-plans'/)
  assert.match(router, /path: '\/leader\/my-month-plans\/:id', redirect: '\/leader\/my-month-plans'/)
})

test('month-plan list shows all tasks and keeps actions inline', () => {
  assert.match(list, /:data="taskRows"/)
  assert.match(list, /employee-month-readonly-table/)
  assert.match(list, /当前页面直接展示全部任务/)
  assert.doesNotMatch(list, /查看计划详情/)
  assert.doesNotMatch(list, /detailPath/)
})
