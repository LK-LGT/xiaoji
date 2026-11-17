import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const viewPath = fileURLToPath(new URL('../src/views/employee/EmployeeDashboard.vue', import.meta.url))
const stylePath = fileURLToPath(new URL('../src/styles.css', import.meta.url))
const employeeRoot = new URL('../src/views/employee/', import.meta.url)
const readEmployee = (path: string) => readFileSync(fileURLToPath(new URL(path, employeeRoot)), 'utf8')
const view = readFileSync(viewPath, 'utf8')
const styles = readFileSync(stylePath, 'utf8')

test('employee workbench keeps the calendar focused and removes duplicate detail panels', () => {
  assert.doesNotMatch(view, /class="workbench-header"/)
  assert.doesNotMatch(view, /按周期处理本人日计划/)
  assert.match(view, /class="workbench-month-display"/)
  assert.match(view, /monthDisplay\.month/)
  assert.match(view, /monthDisplay\.year/)
  assert.match(view, /const monthDisplay = computed/)
  assert.match(view, /currentGroup\.value = data\.orgName \?\? ''/)
  assert.match(view, /#header/)
  assert.match(view, /ArrowLeft/)
  assert.match(view, /ArrowRight/)
  assert.match(view, /RefreshRight/)
  assert.match(view, />待处理</)
  assert.match(view, />快捷入口</)
  assert.match(view, />日计划日历</)
  assert.doesNotMatch(view, />所选日期详情</)
  assert.doesNotMatch(view, />月计划与成果</)
  assert.doesNotMatch(view, /workbench-day-detail/)
  assert.doesNotMatch(view, /workbench-month-card/)
  assert.doesNotMatch(view, />今日待办</)
  assert.doesNotMatch(view, /danger-entry/)
  // BUG-R6-26：日历选月不再手写 `day.slice(0, 7)`，统一走 `periodMonthKey`。
  assert.match(view, /const selectedMonth = periodMonthKey\(day\)/)
  assert.match(view, /currentMonth\.value = selectedMonth/)
})

test('employee workbench defines desktop rail, medium toolbar, and narrow calendar overflow', () => {
  assert.match(styles, /\.workbench-month-display/)
  assert.match(styles, /\.workbench-month-display__value/)
  assert.match(styles, /@media \(min-width: 1440px\)/)
  assert.match(styles, /@media \(min-width: 900px\) and \(max-width: 1439px\)/)
  assert.match(styles, /\.workbench-calendar-scroll/)
  assert.match(styles, /min-width:\s*700px/)
  assert.match(styles, /\.workbench-entry-card:disabled/)
})

test('employee-only pages do not render redundant title and subtitle headers', () => {
  for (const path of ['EmployeeDashboard.vue', 'EmployeeAppeals.vue', 'EmployeePerformanceEvidence.vue', 'EmployeeResultCenter.vue']) {
    const source = readEmployee(path)
    assert.doesNotMatch(source, /class="page-title"/)
    assert.doesNotMatch(source, /class="page-subtitle"/)
  }
})

test('shared employee pages keep top headers only in leader routes', () => {
  const cases = [
    ['EmployeeDayPlanList.vue', 'v-if="isLeaderMode" class="page-header'],
    ['EmployeeMonthPlanListRefactored.vue', 'v-if="isLeaderContext" class="page-header'],
    ['EmployeeMonthPlanEdit.vue', 'v-if="isLeaderContext" class="page-header'],
    ['EmployeeWeekPlanList.vue', 'v-if="isLeaderWorkspace" class="page-header'],
    ['EmployeeWeekPlanEdit.vue', 'v-if="isLeaderWorkspace" class="page-header'],
    ['EmployeeWeekPlanDetail.vue', 'v-if="isLeaderWorkspace" class="page-header'],
    ['EmployeeResultSubmit.vue', 'v-if="isLeaderWorkspace" class="result-page-header'],
    ['components/EmployeeDayPlanEditor.vue', 'v-if="!compact && isLeaderRoute" class="page-header'],
  ] as const

  for (const [path, leaderHeader] of cases) {
    const source = readEmployee(path)
    assert.ok(source.includes(leaderHeader))
    assert.doesNotMatch(source, /employee-page-actions/)
  }
})

test('employee actions live in the first business tool area instead of a top action bar', () => {
  assert.match(readEmployee('EmployeeAppeals.vue'), /<div class="section-header">[\s\S]*@click="openCreate"/)
  assert.match(readEmployee('EmployeeDayPlanList.vue'), /class="day-week-browser__nav"[\s\S]*@click="refreshAll"/)
  assert.match(readEmployee('EmployeeMonthPlanListRefactored.vue'), /class="employee-month-browser__head"[\s\S]*@click="goCreateSelectedMonth"/)
  assert.match(readEmployee('EmployeeWeekPlanList.vue'), /class="week-browser__head"[\s\S]*@click="openSelectedWeek"/)
  assert.match(readEmployee('components/EmployeeDayPlanEditor.vue'), /class="day-task-toolbar__actions"[\s\S]*@click="assistDrawerVisible = true"/)
})
