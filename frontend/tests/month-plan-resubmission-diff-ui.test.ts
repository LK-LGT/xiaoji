import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { compareMonthPlanSnapshots } from '../src/views/employee/components/planDraftVersionComparison.ts'

function source(path: string) {
  return readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
}

const approval = source('../src/views/department/MonthPlanApproval.vue')
const departmentApi = source('../src/api/department.ts')
const leaderApi = source('../src/api/leader.ts')
const adapters = source('../src/views/performanceAdapters.ts')

test('leader detail API isolates the rejection baseline from shared approval lists', () => {
  assert.match(departmentApi, /export interface MonthPlanRejectionBaseline/)
  assert.match(departmentApi, /export interface LeaderMonthPlanApprovalDetail/)
  assert.match(departmentApi, /comparisonStatus: 'NONE' \| 'AVAILABLE' \| 'UNAVAILABLE'/)
  assert.match(leaderApi, /LeaderMonthPlanApprovalDetail/)
  assert.match(leaderApi, /MonthPlanApprovalItem/)
  assert.match(leaderApi, /MonthPlanApprovalPage/)
  assert.match(leaderApi, /http\.get<unknown, LeaderMonthPlanApprovalDetail>/)
  assert.match(departmentApi, /workType: string/)
})

test('approval adapter keeps every field required by month snapshot comparison', () => {
  assert.match(adapters, /version: item\.version/)
  assert.match(adapters, /workType: planItem\.workType/)
  assert.match(adapters, /quality: planItem\.quality/)
})

test('leader approval defaults to the latest rejection-to-resubmission diff', () => {
  assert.match(approval, /import MonthPlanVersionDiff from/)
  assert.match(approval, /compareMonthPlanSnapshots/)
  assert.match(approval, /response\.plan/)
  assert.match(approval, /nextComparisonStatus = response\.comparisonStatus/)
  assert.match(approval, /nextRejectionBaseline = response\.rejectionBaseline/)
  assert.match(approval, /comparisonStatus === 'AVAILABLE'/)
  assert.match(approval, /effect="plain">再次提交<\/el-tag>/)
  assert.match(approval, /上次驳回意见/)
  assert.match(approval, /<MonthPlanVersionDiff/)
  assert.match(approval, /heading="上次提交到本次提交"/)
  assert.match(approval, /before-label="上次提交"/)
  assert.match(approval, /after-label="本次提交"/)
  assert.match(approval, /empty-text="本次提交与上次被驳回版本一致"/)
  assert.match(approval, /comparisonStatus === 'UNAVAILABLE'/)
  assert.match(approval, /变更对比暂不可用/)
  assert.match(approval, /class="resubmission-review__title"/)
  assert.match(approval, /V\{\{ rejectionBaseline\.versionNo \}\}[\s\S]*V\{\{ activeRow\.version \}\}/)
  assert.match(approval, /class="current-plan-divider"/)
  assert.ok(approval.indexOf('rejection-context') < approval.indexOf('<MonthPlanVersionDiff'))
  assert.ok(approval.indexOf('<MonthPlanVersionDiff') < approval.indexOf('current-plan-divider'))
})

test('shared comparison detects task changes and ignores legacy summaries', () => {
  const previous = {
    planMonth: '2026-08',
    summary: '完成重点项目',
    items: [
      { id: 1, workType: 'UNKNOWN', taskName: '需求确认', taskContent: '确认范围', deliverable: '清单', quality: '完整', estimatedStartDate: '2026-08-01', estimatedEndDate: '2026-08-10', performanceWeight: 40, estimatedValue: 8000 },
      { id: 2, workType: 'UNKNOWN', taskName: '项目交付', taskContent: '完成上线', deliverable: '上线包', quality: '通过验收', estimatedStartDate: '2026-08-11', estimatedEndDate: '2026-08-28', performanceWeight: 60, estimatedValue: 12000 },
    ],
  }
  const current = {
    planMonth: '2026-08',
    summary: '完成重点项目并复盘',
    items: [
      { ...previous.items[0], quality: '评审无遗漏' },
      { id: 3, workType: 'UNKNOWN', taskName: '项目复盘', taskContent: '沉淀经验', deliverable: '复盘文档', quality: '完成评审', estimatedStartDate: '2026-08-20', estimatedEndDate: '2026-08-30', performanceWeight: 60, estimatedValue: 5000 },
    ],
  }

  const diff = compareMonthPlanSnapshots(previous, current)

  assert.equal('summary' in diff, false)
  assert.deepEqual(diff.counts, { added: 1, modified: 1, removed: 1 })
})
