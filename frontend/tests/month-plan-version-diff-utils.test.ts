import assert from 'node:assert/strict'
import test from 'node:test'
import {
  areAllVisibleMonthPlanDiffExpanded,
  buildDefaultExpandedMonthPlanDiffKeys,
  buildMonthPlanDiffFilterOptions,
  buildMonthPlanDiffTaskDetailEntries,
  isMonthPlanDiffExpanded,
  MONTH_PLAN_DIFF_CHANGE_LABELS,
  resolveMonthPlanDiffChangeLabel,
  resolveMonthPlanDiffChangeSummary,
  selectVisibleMonthPlanDiffChanges,
  toggleAllVisibleMonthPlanDiffChanges,
  toggleMonthPlanDiffChange,
  type MonthPlanDiffLike,
} from '../src/utils/monthPlanVersionDiff.ts'

const DIFF: MonthPlanDiffLike = {
  items: [
    {
      key: 'k1',
      type: 'modified',
      title: '改过的任务',
      fields: [{ label: '任务内容' }, { label: '考核标准' }],
    },
    { key: 'k2', type: 'added', title: '新任务', fields: [] },
    { key: 'k3', type: 'removed', title: '删掉的任务', fields: [] },
  ],
  counts: { modified: 1, added: 1, removed: 1 },
}

const LABELS = { afterLabel: '当前版本', beforeLabel: '历史版本' }

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 筛选
// ═══════════════════════════════════════════════════════════════════════════

test('筛选选项：带各类型计数', () => {
  assert.deepEqual(buildMonthPlanDiffFilterOptions(DIFF), [
    { label: '全部 3', value: 'ALL' },
    { label: '修改 1', value: 'modified' },
    { label: '新增 1', value: 'added' },
    { label: '删除 1', value: 'removed' },
  ])
})

test('筛选选项：没有变更时全部是 0', () => {
  const options = buildMonthPlanDiffFilterOptions({ items: [], counts: { modified: 0, added: 0, removed: 0 } })
  assert.equal(options[0].label, '全部 0')
})

test('按筛选过滤：修改', () => {
  assert.deepEqual(selectVisibleMonthPlanDiffChanges(DIFF.items, 'modified').map((c) => c.key), ['k1'])
})

test('按筛选过滤：新增 / 删除', () => {
  assert.deepEqual(selectVisibleMonthPlanDiffChanges(DIFF.items, 'added').map((c) => c.key), ['k2'])
  assert.deepEqual(selectVisibleMonthPlanDiffChanges(DIFF.items, 'removed').map((c) => c.key), ['k3'])
})

test('按筛选过滤：全部返回原数组引用', () => {
  assert.equal(selectVisibleMonthPlanDiffChanges(DIFF.items, 'ALL'), DIFF.items)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 展开态
// ═══════════════════════════════════════════════════════════════════════════

test('展开态：contains 判定', () => {
  assert.equal(isMonthPlanDiffExpanded(new Set(['a']), 'a'), true)
  assert.equal(isMonthPlanDiffExpanded(new Set(['a']), 'b'), false)
})

test('全展开判定：全部可见变更都展开 ⇒ true', () => {
  assert.equal(areAllVisibleMonthPlanDiffExpanded(DIFF.items, new Set(['k1', 'k2', 'k3'])), true)
})

test('全展开判定：只展开一部分 ⇒ false', () => {
  assert.equal(areAllVisibleMonthPlanDiffExpanded(DIFF.items, new Set(['k1'])), false)
})

test('全展开判定：空列表 ⇒ false（不是 true）', () => {
  assert.equal(areAllVisibleMonthPlanDiffExpanded([], new Set()), false)
})

test('切换单条：未展开 ⇒ 展开', () => {
  const next = toggleMonthPlanDiffChange(new Set(['a']), 'b')
  assert.deepEqual([...next].sort(), ['a', 'b'])
})

test('切换单条：已展开 ⇒ 收起', () => {
  const next = toggleMonthPlanDiffChange(new Set(['a', 'b']), 'b')
  assert.deepEqual([...next], ['a'])
})

test('切换单条：返回新 Set（不改原 Set）', () => {
  const origin = new Set(['a'])
  const next = toggleMonthPlanDiffChange(origin, 'b')
  assert.notEqual(next, origin)
  assert.deepEqual([...origin], ['a'])
})

test('切换全部：未全展开 ⇒ 全部展开', () => {
  const next = toggleAllVisibleMonthPlanDiffChanges(DIFF.items, new Set(['k1']))
  assert.deepEqual([...next].sort(), ['k1', 'k2', 'k3'])
})

test('切换全部：已全展开 ⇒ 全部收起', () => {
  const next = toggleAllVisibleMonthPlanDiffChanges(DIFF.items, new Set(['k1', 'k2', 'k3']))
  assert.deepEqual([...next], [])
})

test('默认展开：只有「修改」默认展开', () => {
  const keys = buildDefaultExpandedMonthPlanDiffKeys(DIFF)
  assert.deepEqual([...keys], ['k1'])
})

test('默认展开：没有修改项 ⇒ 空集合', () => {
  const keys = buildDefaultExpandedMonthPlanDiffKeys({ items: [], counts: { modified: 0, added: 0, removed: 0 } })
  assert.equal(keys.size, 0)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 变更摘要
// ═══════════════════════════════════════════════════════════════════════════

test('变更摘要：修改 ⇒ 列出改动的字段名', () => {
  assert.equal(
    resolveMonthPlanDiffChangeSummary(DIFF.items[0], LABELS),
    '已修改：任务内容、考核标准',
  )
})

test('变更摘要：修改但没有字段 ⇒ 「已修改：」', () => {
  const change = { key: 'k', type: 'modified' as const, title: 't', fields: [] }
  assert.equal(resolveMonthPlanDiffChangeSummary(change, LABELS), '已修改：')
})

test('变更摘要：新增 ⇒ 优先用事项内容', () => {
  const change = {
    key: 'k',
    type: 'added' as const,
    title: 't',
    fields: [],
    after: { taskContent: '新的任务内容' },
  }
  assert.equal(resolveMonthPlanDiffChangeSummary(change, LABELS), '新的任务内容')
})

test('变更摘要：新增但没内容 ⇒ 用交付物', () => {
  const change = {
    key: 'k',
    type: 'added' as const,
    title: 't',
    fields: [],
    after: { deliverable: '交付物X' },
  }
  assert.equal(resolveMonthPlanDiffChangeSummary(change, LABELS), '交付物X')
})

test('变更摘要：新增且完全没内容 ⇒ 回落「{标签}任务」', () => {
  const change = { key: 'k', type: 'added' as const, title: 't', fields: [] }
  assert.equal(resolveMonthPlanDiffChangeSummary(change, LABELS), '当前版本任务')
})

test('变更摘要：删除 ⇒ 回落「{标签}任务」', () => {
  const change = { key: 'k', type: 'removed' as const, title: 't', fields: [] }
  assert.equal(resolveMonthPlanDiffChangeSummary(change, LABELS), '历史版本任务')
})

test('变更摘要：删除时优先用 before 的内容', () => {
  const change = {
    key: 'k',
    type: 'removed' as const,
    title: 't',
    fields: [],
    before: { taskContent: '被删的内容' },
  }
  assert.equal(resolveMonthPlanDiffChangeSummary(change, LABELS), '被删的内容')
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 任务详情条目
// ═══════════════════════════════════════════════════════════════════════════

test('任务详情：没有事项 ⇒ 空数组', () => {
  const entries = buildMonthPlanDiffTaskDetailEntries(undefined, {
    formatStandard: () => '',
    formatPeriod: () => '',
  })
  assert.deepEqual(entries, [])
})

test('任务详情：六项齐全且顺序固定', () => {
  const entries = buildMonthPlanDiffTaskDetailEntries(
    {
      workType: 'TASK',
      taskContent: '做点事',
      deliverable: '交付物',
      quality: '高质量',
      estimatedStartDate: '2026-11-01',
      estimatedEndDate: '2026-11-30',
      performanceWeight: 20,
      estimatedValue: 1234.5,
    },
    { formatStandard: () => '标准', formatPeriod: () => '周期' },
  )
  assert.deepEqual(entries.map((e) => e.label), [
    '工作类型',
    '任务内容',
    '考核标准（交付物、质量）',
    '完成时间段',
    '绩效权重',
    '预估价值',
  ])
  assert.equal(entries[0].value, '任务')
  assert.equal(entries[4].value, '20%')
})

test('任务详情：工作类型未知 ⇒ 原样显示', () => {
  const entries = buildMonthPlanDiffTaskDetailEntries(
    { workType: 'SOMETHING' },
    { formatStandard: () => '', formatPeriod: () => '' },
  )
  assert.equal(entries[0].value, 'SOMETHING')
})

test('任务详情：工作类型缺失 ⇒ 未分类', () => {
  const entries = buildMonthPlanDiffTaskDetailEntries(
    {},
    { formatStandard: () => '', formatPeriod: () => '' },
  )
  assert.equal(entries[0].value, '未分类')
})

test('任务详情：任务内容缺失 ⇒ 未填写', () => {
  const entries = buildMonthPlanDiffTaskDetailEntries(
    {},
    { formatStandard: () => '', formatPeriod: () => '' },
  )
  assert.equal(entries[1].value, '未填写')
})

test('任务详情：预估价值用千分位', () => {
  const entries = buildMonthPlanDiffTaskDetailEntries(
    { estimatedValue: 1234567 },
    { formatStandard: () => '', formatPeriod: () => '' },
  )
  assert.equal(entries[5].value, '¥1,234,567')
})

test('任务详情：预估价值缺失 ⇒ ¥0', () => {
  const entries = buildMonthPlanDiffTaskDetailEntries(
    {},
    { formatStandard: () => '', formatPeriod: () => '' },
  )
  assert.equal(entries[5].value, '¥0')
})

test('任务详情：格式化函数收到的是原始值和兜底文案', () => {
  const calls: unknown[][] = []
  buildMonthPlanDiffTaskDetailEntries(
    { deliverable: 'D', quality: 'Q', estimatedStartDate: 'S', estimatedEndDate: 'E' },
    {
      formatStandard: (d, q, f) => { calls.push([d, q, f]); return '' },
      formatPeriod: (s, e, f) => { calls.push([s, e, f]); return '' },
    },
  )
  assert.deepEqual(calls[0], ['D', 'Q', '未填写'])
  assert.deepEqual(calls[1], ['S', 'E', '未填写'])
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 变更类型文案
// ═══════════════════════════════════════════════════════════════════════════

test('变更类型文案：三种已知类型', () => {
  assert.equal(resolveMonthPlanDiffChangeLabel('added'), '新增')
  assert.equal(resolveMonthPlanDiffChangeLabel('modified'), '修改')
  assert.equal(resolveMonthPlanDiffChangeLabel('removed'), '删除')
})

test('变更类型文案：未知类型原样返回', () => {
  assert.equal(resolveMonthPlanDiffChangeLabel('weird' as never), 'weird')
})

test('变更类型文案表：三种类型齐全', () => {
  assert.deepEqual(Object.keys(MONTH_PLAN_DIFF_CHANGE_LABELS).sort(), ['added', 'modified', 'removed'])
})
