import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildPlanDraftPreviewEntries,
  buildPlanDraftVersionGroupOptions,
  findPlanDraftVersionById,
  formatPlanDraftFullTime,
  formatPlanDraftSnapshot,
  groupPlanDraftVersions,
  isAiPlanDraftVersion,
  isPlanDraftChanged,
  isSavedPlanDraftVersion,
  selectVisiblePlanDraftVersions,
  type PlanDraftVersionSummaryLike,
} from '../src/utils/planDraftVersionDrawer.ts'

function makeVersion(
  id: string,
  sourceType: PlanDraftVersionSummaryLike['sourceType'],
): PlanDraftVersionSummaryLike {
  return { id, sourceType }
}

const VERSIONS: PlanDraftVersionSummaryLike[] = [
  makeVersion('v1', 'MANUAL_SAVE'),
  makeVersion('v2', 'AI_GENERATE'),
  makeVersion('v3', 'RESTORED_SAVE'),
  makeVersion('v4', 'AI_OPTIMIZE'),
]

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 版本分组
// ═══════════════════════════════════════════════════════════════════════════

test('来源判定：手动保存 / 恢复后保存 ⇒ 已保存', () => {
  assert.equal(isSavedPlanDraftVersion('MANUAL_SAVE'), true)
  assert.equal(isSavedPlanDraftVersion('RESTORED_SAVE'), true)
  assert.equal(isSavedPlanDraftVersion('AI_GENERATE'), false)
})

test('来源判定：AI 生成 / AI 优化 ⇒ AI 记录', () => {
  assert.equal(isAiPlanDraftVersion('AI_GENERATE'), true)
  assert.equal(isAiPlanDraftVersion('AI_OPTIMIZE'), true)
  assert.equal(isAiPlanDraftVersion('MANUAL_SAVE'), false)
})

test('分组：手动保存和恢复后保存归到已保存组', () => {
  const grouped = groupPlanDraftVersions(VERSIONS)
  assert.deepEqual(grouped.saved.map((v) => v.id), ['v1', 'v3'])
})

test('分组：AI 生成和 AI 优化归到 AI 组', () => {
  const grouped = groupPlanDraftVersions(VERSIONS)
  assert.deepEqual(grouped.ai.map((v) => v.id), ['v2', 'v4'])
})

test('分组：保持原顺序（不排序）', () => {
  const versions = [
    makeVersion('a', 'AI_GENERATE'),
    makeVersion('b', 'MANUAL_SAVE'),
    makeVersion('c', 'AI_OPTIMIZE'),
  ]
  assert.deepEqual(groupPlanDraftVersions(versions).ai.map((v) => v.id), ['a', 'c'])
})

test('可见版本：按当前分组取', () => {
  assert.deepEqual(selectVisiblePlanDraftVersions(VERSIONS, 'saved').map((v) => v.id), ['v1', 'v3'])
  assert.deepEqual(selectVisiblePlanDraftVersions(VERSIONS, 'ai').map((v) => v.id), ['v2', 'v4'])
})

test('分组选项：带各组计数', () => {
  assert.deepEqual(buildPlanDraftVersionGroupOptions(VERSIONS), [
    { label: '已保存版本 2', value: 'saved' },
    { label: 'AI 生成记录 2', value: 'ai' },
  ])
})

test('分组选项：没有版本 ⇒ 都是 0', () => {
  assert.deepEqual(buildPlanDraftVersionGroupOptions([]), [
    { label: '已保存版本 0', value: 'saved' },
    { label: 'AI 生成记录 0', value: 'ai' },
  ])
})

test('按 ID 找版本：找到 / 找不到 / ID 为空', () => {
  assert.equal(findPlanDraftVersionById(VERSIONS, 'v2')?.id, 'v2')
  assert.equal(findPlanDraftVersionById(VERSIONS, 'nope'), undefined)
  assert.equal(findPlanDraftVersionById(VERSIONS, undefined), undefined)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 预览条目
// ═══════════════════════════════════════════════════════════════════════════

test('预览条目：周计划 ⇒ 开始日 + 任务数', () => {
  const entries = buildPlanDraftPreviewEntries('WEEK', {
    weekStart: '2026-11-23',
    items: [{ id: 1 }, { id: 2 }],
  })
  assert.deepEqual(entries, [
    { label: '周开始日', value: '2026-11-23' },
    { label: '任务数量', value: '2 项' },
  ])
})

test('预览条目：周计划缺开始日 ⇒ -', () => {
  const entries = buildPlanDraftPreviewEntries('WEEK', {})
  assert.equal(entries[0].value, '-')
})

test('预览条目：周计划 items 不是数组 ⇒ 0 项', () => {
  const entries = buildPlanDraftPreviewEntries('WEEK', { items: 'not-an-array' })
  assert.equal(entries[1].value, '0 项')
})

test('预览条目：日计划 ⇒ 日期 + 关联数 + 内容', () => {
  const entries = buildPlanDraftPreviewEntries('DAY', {
    planDate: '2026-11-24',
    relatedWeekPlanItemIds: ['a', 'b', 'c'],
    content: '做点事',
  })
  assert.deepEqual(entries, [
    { label: '计划日期', value: '2026-11-24' },
    { label: '关联周任务', value: '3 项' },
    { label: '工作内容', value: '做点事' },
  ])
})

test('预览条目：日计划缺内容 ⇒ 未填写', () => {
  const entries = buildPlanDraftPreviewEntries('DAY', {})
  assert.equal(entries[2].value, '未填写')
})

test('预览条目：没有快照 ⇒ 也不抛错', () => {
  const entries = buildPlanDraftPreviewEntries('WEEK', undefined)
  assert.equal(entries[0].value, '-')
})

test('格式化快照：美化 JSON', () => {
  const formatted = formatPlanDraftSnapshot({ a: 1 })
  assert.equal(formatted, '{\n  "a": 1\n}')
})

test('格式化快照：没有快照 ⇒ 空串', () => {
  assert.equal(formatPlanDraftSnapshot(undefined), '')
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 时间格式化
// ═══════════════════════════════════════════════════════════════════════════

test('完整时间：T 换成空格并截断到分钟', () => {
  assert.equal(formatPlanDraftFullTime('2026-11-24T10:30:00'), '2026-11-24 10:30')
})

test('完整时间：空值 ⇒ -', () => {
  assert.equal(formatPlanDraftFullTime(''), '-')
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 变更判定
// ═══════════════════════════════════════════════════════════════════════════

test('变更判定：月计划 ⇒ 看 diff 的 unchanged', () => {
  assert.equal(isPlanDraftChanged({
    planType: 'MONTH',
    hasSnapshot: true,
    monthDiffUnchanged: false,
    snapshotsEqual: true,
  }), true)
})

test('变更判定：月计划没变', () => {
  assert.equal(isPlanDraftChanged({
    planType: 'MONTH',
    hasSnapshot: true,
    monthDiffUnchanged: true,
    snapshotsEqual: false,
  }), false)
})

test('变更判定：周计划 ⇒ 看快照全等', () => {
  assert.equal(isPlanDraftChanged({
    planType: 'WEEK',
    hasSnapshot: true,
    monthDiffUnchanged: true,
    snapshotsEqual: false,
  }), true)
})

test('变更判定：日计划没变', () => {
  assert.equal(isPlanDraftChanged({
    planType: 'DAY',
    hasSnapshot: true,
    monthDiffUnchanged: false,
    snapshotsEqual: true,
  }), false)
})

test('变更判定：没有选中版本 ⇒ 没变', () => {
  assert.equal(isPlanDraftChanged({
    planType: 'MONTH',
    hasSnapshot: false,
    monthDiffUnchanged: false,
    snapshotsEqual: false,
  }), false)
})
