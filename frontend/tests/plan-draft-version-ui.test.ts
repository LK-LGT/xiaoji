import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { arePlanSnapshotsEqual, compareMonthPlanSnapshots } from '../src/views/employee/components/planDraftVersionComparison.ts'

function source(path: string) {
  return readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
}

const drawer = source('../src/views/employee/components/PlanDraftVersionDrawer.vue')
const versionRail = source('../src/views/employee/components/PlanDraftVersionRail.vue')
const monthDiffView = source('../src/views/employee/components/MonthPlanVersionDiff.vue')
const versionApi = source('../src/api/planDraftVersions.ts')
const month = source('../src/views/employee/EmployeeMonthPlanEdit.vue')
const week = source('../src/views/employee/EmployeeWeekPlanEdit.vue')
const day = source('../src/views/employee/components/EmployeeDayPlanEditor.vue')
const aiApi = source('../src/api/employeeAi.ts')
const planAssistant = source('../src/views/employee/components/PlanAiAssistant.vue')

test('version rail provides a vertical desktop list and mobile selector', () => {
  assert.match(versionRail, /class="version-list"/)
  assert.match(versionRail, /class="mobile-version-select"/)
  assert.match(versionRail, /aria-current/)
  assert.match(versionRail, /emit\('select', version\)/)
  assert.match(versionRail, /emit\('retry'\)/)
  assert.doesNotMatch(versionRail, /listPlanDraftVersionsApi|getPlanDraftVersionApi|restorePlanDraftVersionApi/)
})

test('month diff view owns task counts and field-level changes without plan summary', () => {
  assert.match(monthDiffView, /withDefaults\(defineProps<\{/)
  assert.match(monthDiffView, /heading\?: string/)
  assert.match(monthDiffView, /beforeLabel\?: string/)
  assert.match(monthDiffView, /afterLabel\?: string/)
  assert.match(monthDiffView, /emptyText\?: string/)
  assert.match(monthDiffView, /\{\{ heading \}\}/)
  assert.match(monthDiffView, /\{\{ beforeLabel \}\}/)
  assert.match(monthDiffView, /\{\{ afterLabel \}\}/)
  assert.match(monthDiffView, /:description="emptyText"/)
  assert.match(monthDiffView, /指定版本到当前版本/)
  assert.match(monthDiffView, /month-diff-item--/)
  assert.match(monthDiffView, /历史版本/)
  assert.match(monthDiffView, /当前版本/)
  assert.match(monthDiffView, /当前计划与所选版本一致/)
  assert.match(monthDiffView, /type DiffFilter = 'ALL' \| MonthPlanItemChange\['type'\]/)
  assert.match(monthDiffView, /const activeFilter = ref<DiffFilter>\('ALL'\)/)
  assert.match(monthDiffView, /<el-segmented/)
  assert.match(monthDiffView, /visibleChanges/)
  assert.match(monthDiffView, /expandedKeys/)
  assert.match(monthDiffView, /展开全部/)
  assert.match(monthDiffView, /收起全部/)
  assert.match(monthDiffView, /:aria-expanded="isExpanded\(change\.key\)"/)
  assert.match(monthDiffView, /class="split-diff-cell split-diff-cell--before"/)
  assert.match(monthDiffView, /class="split-diff-cell split-diff-cell--after"/)
  assert.match(monthDiffView, /<Minus \/>/)
  assert.match(monthDiffView, /<Plus \/>/)
  assert.match(monthDiffView, /class="split-diff-header"/)
  assert.match(monthDiffView, /class="split-diff-column split-diff-column--before"/)
  assert.match(monthDiffView, /class="split-diff-column split-diff-column--after"/)
  assert.match(monthDiffView, /class="split-diff-row"/)
  assert.match(monthDiffView, /class="split-diff-cell split-diff-cell--missing"/)
  assert.match(monthDiffView, /此版本无此内容/)
  assert.match(monthDiffView, /grid-template-columns:112px minmax\(0,1fr\) minmax\(0,1fr\)/)
  assert.doesNotMatch(monthDiffView, /计划摘要|diff\.summary|summaryVisible|summary-change/)
})

test('shared draft drawer lists immutable sources and restores through a scoped read endpoint', () => {
  assert.match(drawer, /AI 生成/)
  assert.match(drawer, /AI 优化/)
  assert.match(drawer, /手动保存/)
  assert.match(drawer, /恢复后保存/)
  assert.match(drawer, /restorePlanDraftVersionApi/)
  assert.match(drawer, /:disabled="!editable"/)
  assert.match(drawer, /emit\('restore', version\.snapshot, version\)/)
  assert.match(versionApi, /\/employee\/plan-draft-versions/)
})

test('draft history separates saved versions from AI generation records', () => {
  assert.match(drawer, /已保存版本/)
  assert.match(drawer, /AI 生成记录/)
  assert.match(drawer, /savedVersions/)
  assert.match(drawer, /aiVersions/)
  assert.match(drawer, /visibleVersions/)
  assert.match(drawer, /技术信息/)
  assert.doesNotMatch(drawer, /<pre>{{ formattedSnapshot }}<\/pre>/)
})

test('draft history uses a centered two-pane review workbench', () => {
  assert.match(drawer, /<el-dialog/)
  assert.doesNotMatch(drawer, /<el-drawer/)
  assert.match(drawer, /class="history-review-workbench"/)
  assert.match(drawer, /<PlanDraftVersionRail/)
  assert.match(drawer, /<MonthPlanVersionDiff/)
  assert.match(drawer, /class="review-pane"/)
  assert.match(drawer, /class="review-content"/)
  assert.doesNotMatch(drawer, /version-ribbon|version-node|snapshot-preview/)
  assert.match(drawer, /selectLatestVisibleVersion/)
  assert.match(drawer, /detailLoading/)
})

test('month snapshots ignore persisted ids and normalize numeric and empty values', () => {
  const saved = {
    planMonth: '2026-07',
    summary: '完成重点交付',
    items: [{
      id: 91,
      workType: 'UNKNOWN',
      taskName: '交付版本',
      taskContent: '完成上线',
      deliverable: '发布包',
      quality: null,
      estimatedStartDate: '2026-07-01',
      estimatedEndDate: '2026-07-31',
      performanceWeight: 30,
      estimatedValue: 5,
    }],
  }
  const current = {
    planMonth: '2026-07',
    summary: '完成重点交付',
    items: [{
      workType: 'UNKNOWN',
      taskName: '交付版本',
      taskContent: '完成上线',
      deliverable: '发布包',
      quality: '',
      estimatedStartDate: '2026-07-01',
      estimatedEndDate: '2026-07-31',
      performanceWeight: '30',
      estimatedValue: 5,
    }],
  }

  assert.equal(arePlanSnapshotsEqual('MONTH', saved, current), true)
})

test('month snapshot comparison preserves task order and business content changes', () => {
  const first = {
    planMonth: '2026-07',
    summary: '',
    items: [
      { id: 1, taskName: '任务 A', performanceWeight: 40 },
      { id: 2, taskName: '任务 B', performanceWeight: 60 },
    ],
  }
  const reordered = { ...first, items: [...first.items].reverse() }
  const renamed = { ...first, items: [{ ...first.items[0], taskName: '任务 A+' }, first.items[1]] }

  assert.equal(arePlanSnapshotsEqual('MONTH', first, reordered), false)
  assert.equal(arePlanSnapshotsEqual('MONTH', first, renamed), false)
})

test('month comparison ignores legacy summary and returns task-level changes', () => {
  const historical = {
    summary: '旧摘要',
    items: [
      { id: 1, taskName: '保留任务', deliverable: '旧交付物', performanceWeight: 40 },
      { id: 2, taskName: '删除任务' },
    ],
  }
  const current = {
    summary: '新摘要',
    items: [
      { id: 1, taskName: '保留任务', deliverable: '新交付物', performanceWeight: 40 },
      { taskName: '新增任务' },
    ],
  }

  const diff = compareMonthPlanSnapshots(historical, current)

  assert.deepEqual(diff.counts, { added: 1, modified: 1, removed: 1 })
  assert.deepEqual(diff.items.find((item) => item.type === 'modified')?.fields, [
    { key: 'deliverable', label: '交付物', before: '旧交付物', after: '新交付物' },
  ])
})

test('month comparison anchors unchanged id-less tasks around an insertion', () => {
  const historical = { items: [{ taskName: '任务 A' }, { taskName: '任务 B' }, { taskName: '任务 C' }] }
  const current = { items: [{ taskName: '任务 A' }, { taskName: '新增任务' }, { taskName: '任务 B' }, { taskName: '任务 C' }] }

  const diff = compareMonthPlanSnapshots(historical, current)

  assert.deepEqual(diff.counts, { added: 1, modified: 0, removed: 0 })
  assert.equal(diff.items[0]?.type, 'added')
  assert.equal(diff.items[0]?.title, '新增任务')
})

test('month comparison can pair an id-less AI task with its persisted current task', () => {
  const historical = { items: [{ taskName: 'AI 初稿', taskContent: '旧内容' }] }
  const current = { items: [{ id: 31, taskName: '人工调整', taskContent: '新内容' }] }

  const diff = compareMonthPlanSnapshots(historical, current)

  assert.deepEqual(diff.counts, { added: 0, modified: 1, removed: 0 })
  assert.deepEqual(diff.items[0]?.fields.map((field) => field.key), ['taskName', 'taskContent'])
})

test('month comparison reports task order changes as modifications', () => {
  const historical = { items: [{ id: 1, taskName: '任务 A' }, { id: 2, taskName: '任务 B' }] }
  const current = { items: [{ id: 2, taskName: '任务 B' }, { id: 1, taskName: '任务 A' }] }

  const diff = compareMonthPlanSnapshots(historical, current)

  assert.deepEqual(diff.counts, { added: 0, modified: 2, removed: 0 })
  assert.deepEqual(diff.items[0]?.fields, [
    { key: 'order', label: '任务顺序', before: '第 2 项', after: '第 1 项' },
  ])
})

test('month comparison treats normalized equivalent snapshots as unchanged', () => {
  const historical = { summary: null, items: [{ id: 8, taskName: '任务 A', performanceWeight: 30, estimatedValue: null }] }
  const current = { summary: '', items: [{ id: 8, taskName: '任务 A', performanceWeight: '30', estimatedValue: 0 }] }

  const diff = compareMonthPlanSnapshots(historical, current)

  assert.equal(diff.unchanged, true)
  assert.deepEqual(diff.counts, { added: 0, modified: 0, removed: 0 })
  assert.deepEqual(diff.items, [])
})

test('month snapshot equality ignores legacy summary differences', () => {
  const historical = { planMonth: '2026-07', summary: '旧摘要', items: [{ id: 1, taskName: '任务 A' }] }
  const current = { planMonth: '2026-07', summary: '', items: [{ id: 1, taskName: '任务 A' }] }

  assert.equal(arePlanSnapshotsEqual('MONTH', historical, current), true)
  assert.equal(compareMonthPlanSnapshots(historical, current).unchanged, true)
})

test('week and day snapshots normalize persisted ids without hiding content changes', () => {
  const savedWeek = {
    weekStart: '2026-07-27',
    items: [{ id: 8, monthPlanItemId: 12, content: ' 跟进交付 ', deliverable: null, plannedFinishDate: '2026-07-31' }],
  }
  const currentWeek = {
    weekStart: '2026-07-27',
    items: [{ monthPlanItemId: '12', content: '跟进交付', deliverable: '', plannedFinishDate: '2026-07-31' }],
  }
  const savedDay = { planDate: '2026-07-29', relatedWeekPlanItemIds: [4, 7], content: '处理问题', remark: null }
  const currentDay = { planDate: '2026-07-29', relatedWeekPlanItemIds: ['4', '7'], content: '处理问题', remark: '' }

  assert.equal(arePlanSnapshotsEqual('WEEK', savedWeek, currentWeek), true)
  assert.equal(arePlanSnapshotsEqual('DAY', savedDay, currentDay), true)
  assert.equal(arePlanSnapshotsEqual('DAY', savedDay, { ...currentDay, content: '处理其他问题' }), false)
})

test('draft history keeps readable text sizes', () => {
  assert.doesNotMatch(`${drawer}${versionRail}${monthDiffView}`, /font-size:(?:9|10|11)px/)
  assert.match(monthDiffView, /\.month-diff-item__header strong[^}]*font-size:14px/)
  assert.match(monthDiffView, /\.split-diff-cell p[^}]*font-size:14px/)
})

test('month history renders a directional field-level change list', () => {
  assert.match(drawer, /compareMonthPlanSnapshots/)
  assert.match(drawer, /<MonthPlanVersionDiff :diff="monthDiff"/)
  assert.match(monthDiffView, /指定版本到当前版本/)
  assert.match(monthDiffView, /month-diff-item--added/)
  assert.match(monthDiffView, /month-diff-item--modified/)
  assert.match(monthDiffView, /month-diff-item--removed/)
  assert.match(monthDiffView, /split-diff-row/)
  assert.match(monthDiffView, /当前计划与所选版本一致/)
})

test('history detail can retry the selected version after a failed load', () => {
  assert.match(drawer, /selectedVersionId\.value === version\.id && selected\.value\?\.id === version\.id/)
  assert.match(drawer, /selected\.value = undefined/)
})

test('month editor keeps persisted task ids in the local comparison snapshot', () => {
  assert.match(month, /currentVersionSnapshot[\s\S]*items: form\.items\.map\(\(\{ uiKey: _uiKey, \.\.\.item \}\) => \(\{ \.\.\.item \}\)\)/)
})

test('month week and day explicit saves request versions while internal saves opt out', () => {
  // ⚠️ 月计划的 `saveDraft` 签名已扩为三参：
  //    `saveDraft(showMessage = true, recordVersion = false, syncRoute = true)`
  //    （第三参 syncRoute 用于「保存后不回写路由」）
  //
  //    **要断言的语义没变** —— 显式保存仍然要记版本（第二参 = true），
  //    自动保存 / 内部保存仍然不记版本（recordVersion: false）。
  //    原断言写死 `saveDraft(true, true)`（两参、无尾逗号），
  //    扩参后匹配不上，故改为「不依赖参数个数」的写法。
  assert.match(month, /saveDraft\(true, true(, false)?\)/)
  assert.match(month, /recordVersion: false/)
  assert.match(week, /persistDraft\(true\)/)
  assert.match(week, /persistDraft\(false\)/)
  assert.match(day, /buildPayload\(true\)/)
  assert.match(day, /buildPayload\(false\)/)
})

test('restore remains local and blocks auto or submit preparation until explicit save', () => {
  for (const editor of [month, week, day]) {
    assert.match(editor, /restoredFromVersionId/)
    assert.match(editor, /请先保存已恢复的历史版本/)
    assert.match(editor, /历史版本/)
    assert.match(editor, /PlanDraftVersionDrawer/)
  }
  assert.match(month, /restoredFromVersionId\.value \|\| !form\.planMonth/)
})

test('AI results expose immediate version badges and month optimization sends the complete form', () => {
  assert.match(aiApi, /draftVersionId: number/)
  assert.match(aiApi, /draftVersionNo: number/)
  assert.match(month, /currentForm:/)
  assert.match(month, /targetItemIndex: index/)
  assert.match(planAssistant, /已保存为 V/)
})
