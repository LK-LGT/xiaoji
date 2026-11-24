import assert from 'node:assert/strict'
import test from 'node:test'
import {
  ISSUE_GROUP_PREVIEW_LIMITS,
  MAX_ESTIMATED_VALUE,
  OPTIMIZABLE_FIELDS,
  READINESS_FIELDS_PER_ITEM,
  REQUIRED_WEIGHT_TOTAL,
  buildOptimizePreview,
  collectMonthPlanIssues,
  computeMonthPlanReadiness,
  countCompletedReadinessFields,
  countHiddenMonthPlanIssues,
  defaultSelectedOptimizeFields,
  displayOptimizeValue,
  editableNumber,
  groupMonthPlanIssues,
  hasTaskContent,
  isEmptyMonthPlanItem,
  normalizeOptimizeValue,
  numberForSave,
  previewMonthPlanIssueGroups,
  resolveAutoSaveState,
  sameOptimizableValues,
  sumRegularWeights,
  taskNameForSave,
  type MonthPlanEditableItem,
} from '../src/utils/monthPlanEditor.ts'

/**
 * 🧩 **月计划编辑器纯逻辑测试**（CI-5 阶段 2 第三轮）
 *
 * ⚠️ **为什么测纯函数而不是挂载组件**
 *   ``EmployeeMonthPlanEdit.vue`` 有 1512 行，挂载成本极高且失败难定位。
 *   ⇒ 把纯计算抽到 ``src/utils/monthPlanEditor.ts``，这里直接断言计算结果。
 *
 * ⚠️ **断言纪律**：每条断言都要**能区分对错**（不能恒真）。
 *   ⇒ 具体验证边界值、错误消息、顺序、精度，而不是「大于 0」。
 */

/** 构造一个「全部字段齐全」的月计划条目。 */
function fullItem(overrides: Partial<MonthPlanEditableItem> = {}): MonthPlanEditableItem {
  return {
    taskName: '任务名',
    taskContent: '任务内容',
    deliverable: '交付物',
    quality: '质量标准',
    estimatedStartDate: '2026-11-01',
    estimatedEndDate: '2026-11-30',
    performanceWeight: 100,
    estimatedValue: 1000,
    ...overrides,
  }
}

/** 构造一个「完全空」的月计划条目。 */
function emptyItem(overrides: Partial<MonthPlanEditableItem> = {}): MonthPlanEditableItem {
  return {
    taskName: '',
    taskContent: '',
    deliverable: '',
    quality: '',
    estimatedStartDate: '',
    estimatedEndDate: '',
    performanceWeight: null,
    estimatedValue: null,
    ...overrides,
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 权重合计
// ═══════════════════════════════════════════════════════════════════════════

test('sumRegularWeights · 空数组 ⇒ 0', () => {
  assert.equal(sumRegularWeights([]), 0)
})

test('sumRegularWeights · null 权重按 0 计', () => {
  assert.equal(sumRegularWeights([{ performanceWeight: null }, { performanceWeight: null }]), 0)
})

test('sumRegularWeights · 浮点求和保留两位小数（0.1+0.2 ≠ 0.30000000000000004）', () => {
  // ⚠️ 直接求和会得到 0.30000000000000004，toFixed(2) 后是 0.3
  assert.equal(sumRegularWeights([{ performanceWeight: 0.1 }, { performanceWeight: 0.2 }]), 0.3)
})

test('sumRegularWeights · 三个 33.3333 累加后四舍五入到 100', () => {
  const total = sumRegularWeights([
    { performanceWeight: 33.3333 },
    { performanceWeight: 33.3333 },
    { performanceWeight: 33.3334 },
  ])
  assert.equal(total, 100)
  assert.equal(total === REQUIRED_WEIGHT_TOTAL, true)
})

test('sumRegularWeights · 负数权重会被累加（调用方需自行校验）', () => {
  assert.equal(sumRegularWeights([{ performanceWeight: 150 }, { performanceWeight: -50 }]), 100)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 校验规则
// ═══════════════════════════════════════════════════════════════════════════

test('collectMonthPlanIssues · 空月份 ⇒ 提示选月份', () => {
  const issues = collectMonthPlanIssues('', [])
  assert.equal(issues[0].id, 'plan-month')
  assert.equal(issues[0].message, '请选择计划月份')
  assert.equal(issues[0].field, 'planMonth')
})

test('collectMonthPlanIssues · 空任务 ⇒ 提示至少新增一项', () => {
  const issues = collectMonthPlanIssues('2026-11', [])
  // ⚠️ 月份已填 ⇒ 第一个问题就是「至少新增一项任务」
  assert.equal(issues[0].id, 'items-empty')
  assert.equal(issues[0].message, '请至少新增一项计划任务')
})

test('collectMonthPlanIssues · 月份和任务都缺 ⇒ 两个计划级问题按序出现', () => {
  const issues = collectMonthPlanIssues('', [])
  assert.equal(issues[0].id, 'plan-month')
  assert.equal(issues[1].id, 'items-empty')
})

test('collectMonthPlanIssues · 月份和任务都齐 ⇒ 无计划级问题', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem()])
  assert.equal(issues.some((issue) => issue.id === 'plan-month'), false)
  assert.equal(issues.some((issue) => issue.id === 'items-empty'), false)
})

test('collectMonthPlanIssues · 5 个必填字段各报一条（含中文标签）', () => {
  const issues = collectMonthPlanIssues('2026-11', [emptyItem()])
  const fieldIssues = issues.filter((issue) => issue.itemIndex === 0)
  const fields = fieldIssues.map((issue) => issue.field)
  assert.deepEqual(fields.slice(0, 5), [
    'taskContent',
    'deliverable',
    'quality',
    'estimatedStartDate',
    'estimatedEndDate',
  ])
  assert.equal(fieldIssues[0].message, '任务 1：请填写任务内容')
  assert.equal(fieldIssues[1].message, '任务 1：请填写交付物')
  assert.equal(fieldIssues[2].message, '任务 1：请填写质量')
  assert.equal(fieldIssues[3].message, '任务 1：请填写预估开始日期')
  assert.equal(fieldIssues[4].message, '任务 1：请填写预估结束日期')
})

test('collectMonthPlanIssues · 空白字符串也算未填（trim 生效）', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ taskContent: '   ' })])
  const issue = issues.find((item) => item.field === 'taskContent')
  assert.ok(issue)
  assert.equal(issue!.message, '任务 1：请填写任务内容')
})

test('collectMonthPlanIssues · 权重为 0 ⇒ 报无效权重', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ performanceWeight: 0 })])
  const issue = issues.find((item) => item.field === 'performanceWeight' && item.itemIndex === 0)
  assert.ok(issue)
  assert.equal(issue!.message, '任务 1：请填写有效绩效权重')
})

test('collectMonthPlanIssues · 权重为 null ⇒ 报无效权重', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ performanceWeight: null })])
  assert.equal(issues.some((item) => item.id === '0-performanceWeight'), true)
})

test('collectMonthPlanIssues · 权重为负数 ⇒ 报无效权重', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ performanceWeight: -1 })])
  assert.equal(issues.some((item) => item.id === '0-performanceWeight'), true)
})

test('collectMonthPlanIssues · 权重超过 100 ⇒ 报无效权重', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ performanceWeight: 100.01 })])
  assert.equal(issues.some((item) => item.id === '0-performanceWeight'), true)
})

test('collectMonthPlanIssues · 权重恰好 100 ⇒ 不报权重问题', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ performanceWeight: 100 })])
  assert.equal(issues.some((item) => item.id === '0-performanceWeight'), false)
})

test('collectMonthPlanIssues · 预估价值为负 ⇒ 报区间错误', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ estimatedValue: -1 })])
  const issue = issues.find((item) => item.field === 'estimatedValue')
  assert.ok(issue)
  assert.equal(issue!.message, `任务 1：预估价值需在 0 至 ${MAX_ESTIMATED_VALUE} 元之间`)
})

test('collectMonthPlanIssues · 预估价值超上限 ⇒ 报区间错误', () => {
  const issues = collectMonthPlanIssues('2026-11', [
    fullItem({ estimatedValue: MAX_ESTIMATED_VALUE + 1 }),
  ])
  assert.equal(issues.some((item) => item.id === '0-estimatedValue'), true)
})

test('collectMonthPlanIssues · 预估价值恰好等于上限 ⇒ 不报错', () => {
  const issues = collectMonthPlanIssues('2026-11', [
    fullItem({ estimatedValue: MAX_ESTIMATED_VALUE }),
  ])
  assert.equal(issues.some((item) => item.id === '0-estimatedValue'), false)
})

test('collectMonthPlanIssues · 结束日期早于开始日期 ⇒ 报顺序错误', () => {
  const issues = collectMonthPlanIssues('2026-11', [
    fullItem({ estimatedStartDate: '2026-11-30', estimatedEndDate: '2026-11-01' }),
  ])
  const issue = issues.find((item) => item.id === '0-estimatedEndDate-order')
  assert.ok(issue)
  assert.equal(issue!.message, '任务 1：预估结束日期不能早于开始日期')
})

test('collectMonthPlanIssues · 开始日期等于结束日期 ⇒ 不报顺序错误', () => {
  const issues = collectMonthPlanIssues('2026-11', [
    fullItem({ estimatedStartDate: '2026-11-01', estimatedEndDate: '2026-11-01' }),
  ])
  assert.equal(issues.some((item) => item.id === '0-estimatedEndDate-order'), false)
})

test('collectMonthPlanIssues · 只填了开始日期 ⇒ 不报顺序错误（缺结束日期另报）', () => {
  const issues = collectMonthPlanIssues('2026-11', [
    fullItem({ estimatedEndDate: '' }),
  ])
  assert.equal(issues.some((item) => item.id === '0-estimatedEndDate-order'), false)
  assert.equal(issues.some((item) => item.id === '0-estimatedEndDate'), true)
})

test('collectMonthPlanIssues · 权重合计不等于 100 ⇒ 报合计问题', () => {
  const issues = collectMonthPlanIssues('2026-11', [fullItem({ performanceWeight: 60 })])
  const issue = issues.find((item) => item.id === 'weight-total')
  assert.ok(issue)
  assert.equal(issue!.message, '常规任务权重合计需为 100%，当前为 60%')
  assert.equal(issue!.itemIndex, undefined)
})

test('collectMonthPlanIssues · 权重合计等于 100 ⇒ 不报合计问题', () => {
  const issues = collectMonthPlanIssues('2026-11', [
    fullItem({ performanceWeight: 50 }),
    fullItem({ performanceWeight: 50 }),
  ])
  assert.equal(issues.some((item) => item.id === 'weight-total'), false)
})

test('collectMonthPlanIssues · 没有任务时**不**报权重合计问题', () => {
  const issues = collectMonthPlanIssues('2026-11', [])
  assert.equal(issues.some((item) => item.id === 'weight-total'), false)
})

test('collectMonthPlanIssues · 问题顺序：计划月份 → 空任务 → 任务字段 → 权重合计', () => {
  const issues = collectMonthPlanIssues('', [emptyItem()])
  const ids = issues.map((issue) => issue.id)
  assert.equal(ids[0], 'plan-month')
  assert.equal(ids[1], '0-taskContent')
  assert.equal(ids[ids.length - 1], 'weight-total')
})

test('collectMonthPlanIssues · 多任务时 itemIndex 逐个递增', () => {
  const issues = collectMonthPlanIssues('2026-11', [emptyItem(), emptyItem()])
  const indexes = [...new Set(issues.filter((i) => i.itemIndex !== undefined).map((i) => i.itemIndex))]
  assert.deepEqual(indexes, [0, 1])
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 问题分组与折叠
// ═══════════════════════════════════════════════════════════════════════════

test('groupMonthPlanIssues · 无 itemIndex 归「计划信息」，有 itemIndex 归「任务内容」', () => {
  const groups = groupMonthPlanIssues([
    { id: 'plan-month', field: 'planMonth', message: '请选择计划月份' },
    { id: '0-taskContent', itemIndex: 0, field: 'taskContent', message: '任务 1：请填写任务内容' },
  ])
  assert.equal(groups.length, 2)
  assert.equal(groups[0].key, 'plan')
  assert.equal(groups[0].issues.length, 1)
  assert.equal(groups[1].key, 'tasks')
  assert.equal(groups[1].issues.length, 1)
})

test('groupMonthPlanIssues · 空分组被过滤掉', () => {
  const groups = groupMonthPlanIssues([
    { id: '0-taskContent', itemIndex: 0, field: 'taskContent', message: 'x' },
  ])
  assert.equal(groups.length, 1)
  assert.equal(groups[0].key, 'tasks')
})

test('previewMonthPlanIssueGroups · 折叠时计划组只留 3 条', () => {
  const issues = Array.from({ length: 6 }, (_, index) => ({
    id: `plan-${index}`,
    field: 'planMonth',
    message: `问题 ${index}`,
  }))
  const groups = previewMonthPlanIssueGroups(issues)
  assert.equal(groups[0].issues.length, ISSUE_GROUP_PREVIEW_LIMITS.plan)
  assert.equal(groups[0].total, 6)
})

test('previewMonthPlanIssueGroups · 折叠时任务组只留 4 条', () => {
  const issues = Array.from({ length: 9 }, (_, index) => ({
    id: `task-${index}`,
    itemIndex: 0,
    field: 'taskContent',
    message: `问题 ${index}`,
  }))
  const groups = previewMonthPlanIssueGroups(issues)
  assert.equal(groups[0].issues.length, ISSUE_GROUP_PREVIEW_LIMITS.tasks)
  assert.equal(groups[0].total, 9)
})

test('previewMonthPlanIssueGroups · 展开时全部显示', () => {
  const issues = Array.from({ length: 9 }, (_, index) => ({
    id: `task-${index}`,
    itemIndex: 0,
    field: 'taskContent',
    message: `问题 ${index}`,
  }))
  const groups = previewMonthPlanIssueGroups(issues, true)
  assert.equal(groups[0].issues.length, 9)
})

test('previewMonthPlanIssueGroups · 不足上限时全部显示且 total 不变', () => {
  const groups = previewMonthPlanIssueGroups([
    { id: 'a', itemIndex: 0, field: 'taskContent', message: 'x' },
  ])
  assert.equal(groups[0].issues.length, 1)
  assert.equal(groups[0].total, 1)
})

test('countHiddenMonthPlanIssues · 折叠隐藏数 = 总数 − 上限', () => {
  const issues = Array.from({ length: 6 }, (_, index) => ({
    id: `plan-${index}`,
    field: 'planMonth',
    message: `问题 ${index}`,
  }))
  assert.equal(countHiddenMonthPlanIssues(issues), 6 - ISSUE_GROUP_PREVIEW_LIMITS.plan)
})

test('countHiddenMonthPlanIssues · 不超过上限 ⇒ 隐藏 0 条', () => {
  const issues = Array.from({ length: 2 }, (_, index) => ({
    id: `plan-${index}`,
    field: 'planMonth',
    message: `问题 ${index}`,
  }))
  assert.equal(countHiddenMonthPlanIssues(issues), 0)
})

test('countHiddenMonthPlanIssues · 两组分别统计后求和', () => {
  const issues = [
    ...Array.from({ length: 5 }, (_, index) => ({
      id: `plan-${index}`,
      field: 'planMonth',
      message: `计划 ${index}`,
    })),
    ...Array.from({ length: 7 }, (_, index) => ({
      id: `task-${index}`,
      itemIndex: 0,
      field: 'taskContent',
      message: `任务 ${index}`,
    })),
  ]
  assert.equal(
    countHiddenMonthPlanIssues(issues),
    (5 - ISSUE_GROUP_PREVIEW_LIMITS.plan) + (7 - ISSUE_GROUP_PREVIEW_LIMITS.tasks),
  )
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 准备度打分
// ═══════════════════════════════════════════════════════════════════════════

test('computeMonthPlanReadiness · 没有任务 ⇒ 0', () => {
  assert.equal(computeMonthPlanReadiness('2026-11', [], '2026-11'), 0)
})

test('computeMonthPlanReadiness · 全部字段填齐 + 权重 100 ⇒ 100 分', () => {
  const score = computeMonthPlanReadiness('2026-11', [fullItem()], '2026-11')
  assert.equal(score, 100)
})

test('computeMonthPlanReadiness · 计划月份早于当前月 ⇒ 丢掉 10 分', () => {
  const current = '2026-11'
  const past = computeMonthPlanReadiness('2026-10', [fullItem()], current)
  const now = computeMonthPlanReadiness('2026-11', [fullItem()], current)
  assert.equal(now - past, 10)
})

test('computeMonthPlanReadiness · 计划月份晚于当前月 ⇒ 仍算就绪', () => {
  const score = computeMonthPlanReadiness('2026-12', [fullItem()], '2026-11')
  assert.equal(score, 100)
})

test('computeMonthPlanReadiness · 权重为 0 ⇒ 权重段得 0 分', () => {
  const score = computeMonthPlanReadiness('2026-11', [fullItem({ performanceWeight: 0 })], '2026-11')
  // 字段段：6/7（权重不算完成）⇒ 65 * 6/7；权重段：0；计划段：10
  const expected = Math.round(10 + (6 / 7) * 65)
  assert.equal(score, expected)
  assert.ok(score < 100)
})

test('computeMonthPlanReadiness · 权重越接近 100 分数越高', () => {
  const close = computeMonthPlanReadiness('2026-11', [fullItem({ performanceWeight: 99 })], '2026-11')
  const far = computeMonthPlanReadiness('2026-11', [fullItem({ performanceWeight: 50 })], '2026-11')
  assert.ok(close > far)
})

test('computeMonthPlanReadiness · 预估价值为 0 仍算「已填」（>= 0）', () => {
  const withZero = computeMonthPlanReadiness('2026-11', [fullItem({ estimatedValue: 0 })], '2026-11')
  const withValue = computeMonthPlanReadiness('2026-11', [fullItem({ estimatedValue: 500 })], '2026-11')
  assert.equal(withZero, withValue)
})

test('computeMonthPlanReadiness · 预估价值为负 ⇒ 该字段不算完成', () => {
  const negative = computeMonthPlanReadiness('2026-11', [fullItem({ estimatedValue: -1 })], '2026-11')
  const positive = computeMonthPlanReadiness('2026-11', [fullItem({ estimatedValue: 500 })], '2026-11')
  assert.ok(negative < positive)
})

test('computeMonthPlanReadiness · 结果始终落在 0–100', () => {
  const cases = [
    computeMonthPlanReadiness('2026-01', [emptyItem()], '2026-11'),
    computeMonthPlanReadiness('2026-11', [fullItem({ performanceWeight: 999 })], '2026-11'),
    computeMonthPlanReadiness('', [], '2026-11'),
  ]
  for (const score of cases) {
    assert.ok(score >= 0 && score <= 100, `分数越界：${score}`)
  }
})

test('computeMonthPlanReadiness · 多任务时按比例计算字段完整度', () => {
  // ⚠️ 两个任务各权重 100 ⇒ 合计 200，权重段会扣分 ⇒ 不能直接比相等。
  //    这里固定权重，只验证「字段完整度按任务数比例放大 ⇒ 分数不变」。
  const one = computeMonthPlanReadiness('2026-11', [fullItem()], '2026-11')
  const two = computeMonthPlanReadiness(
    '2026-11',
    [fullItem({ performanceWeight: 50 }), fullItem({ performanceWeight: 50 })],
    '2026-11',
  )
  assert.equal(one, 100)
  assert.equal(two, 100)
})

test('computeMonthPlanReadiness · 任务翻倍但权重超 100 ⇒ 权重段扣分，总分下降', () => {
  const one = computeMonthPlanReadiness('2026-11', [fullItem()], '2026-11')
  const two = computeMonthPlanReadiness('2026-11', [fullItem(), fullItem()], '2026-11')
  assert.ok(two < one)
})

test('countCompletedReadinessFields · 全填 ⇒ 7 项', () => {
  assert.equal(countCompletedReadinessFields(fullItem()), READINESS_FIELDS_PER_ITEM)
})

test('countCompletedReadinessFields · 全空 ⇒ 1 项（预估价值为 null 时 Number(null)=0 仍算「已填」）', () => {
  // ⚠️ 这是**原组件就有的语义**（`Number(item.estimatedValue) >= 0`）：
  //    `null` → `Number(null)` = `0` → `0 >= 0` 成立 ⇒ 预估价值字段**恒算已填**。
  //    抽纯函数时**刻意保持一致**，避免悄悄改变打分。
  assert.equal(countCompletedReadinessFields(emptyItem()), 1)
})

test('countCompletedReadinessFields · 只填 5 个文本字段 ⇒ 6 项（含恒算已填的预估价值）', () => {
  const item = emptyItem({
    taskContent: '内容',
    deliverable: '交付物',
    quality: '质量',
    estimatedStartDate: '2026-11-01',
    estimatedEndDate: '2026-11-30',
  })
  // 5 个文本字段 + 恒算已填的预估价值 = 6（权重为 null 不算完成）
  assert.equal(countCompletedReadinessFields(item), 6)
})

test('countCompletedReadinessFields · 权重为 0 不算完成', () => {
  const item = fullItem({ performanceWeight: 0 })
  assert.equal(countCompletedReadinessFields(item), READINESS_FIELDS_PER_ITEM - 1)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. AI 优化字段对比
// ═══════════════════════════════════════════════════════════════════════════

test('normalizeOptimizeValue · 数值字段 ⇒ Number', () => {
  assert.equal(normalizeOptimizeValue('40', 'performanceWeight'), 40)
  assert.equal(normalizeOptimizeValue(40, 'performanceWeight'), 40)
})

test('normalizeOptimizeValue · 数值字段的非法值 ⇒ 0', () => {
  assert.equal(normalizeOptimizeValue('abc', 'estimatedValue'), 0)
  assert.equal(normalizeOptimizeValue(null, 'performanceWeight'), 0)
})

test('normalizeOptimizeValue · 文本字段 ⇒ trim 后字符串', () => {
  assert.equal(normalizeOptimizeValue('  交付物  ', 'deliverable'), '交付物')
})

test('normalizeOptimizeValue · 文本字段的 null ⇒ 空串', () => {
  assert.equal(normalizeOptimizeValue(null, 'quality'), '')
})

test('sameOptimizableValues · 完全相同 ⇒ true', () => {
  const a = fullItem()
  const b = fullItem()
  assert.equal(sameOptimizableValues(a, b), true)
})

test('sameOptimizableValues · 文本只有首尾空格 ⇒ 仍算相同', () => {
  const a = fullItem()
  const b = fullItem({ taskContent: '  任务内容  ' })
  assert.equal(sameOptimizableValues(a, b), true)
})

test('sameOptimizableValues · 数值 40 与 "40" ⇒ 算相同', () => {
  const a = fullItem({ performanceWeight: 40 })
  const b = fullItem({ performanceWeight: '40' as unknown as number })
  assert.equal(sameOptimizableValues(a, b), true)
})

test('sameOptimizableValues · 内容不同 ⇒ false', () => {
  const a = fullItem()
  const b = fullItem({ taskContent: '别的内容' })
  assert.equal(sameOptimizableValues(a, b), false)
})

test('sameOptimizableValues · 非优化字段不同（taskName）⇒ 仍算相同', () => {
  const a = fullItem()
  const b = fullItem({ taskName: '完全不同的任务名' })
  assert.equal(sameOptimizableValues(a, b), true)
})

test('buildOptimizePreview · 覆盖全部可优化字段', () => {
  const preview = buildOptimizePreview(fullItem(), fullItem())
  assert.equal(preview.length, OPTIMIZABLE_FIELDS.length)
  assert.deepEqual(
    preview.map((field) => field.key),
    OPTIMIZABLE_FIELDS.map((field) => field.key),
  )
})

test('buildOptimizePreview · 相同内容 ⇒ 全部 changed=false', () => {
  const preview = buildOptimizePreview(fullItem(), fullItem())
  assert.equal(preview.every((field) => field.changed === false), true)
})

test('buildOptimizePreview · 内容不同 ⇒ 对应字段 changed=true', () => {
  const preview = buildOptimizePreview(fullItem(), fullItem({ taskContent: '新内容' }))
  const changed = preview.filter((field) => field.changed)
  assert.equal(changed.length, 1)
  assert.equal(changed[0].key, 'taskContent')
})

test('buildOptimizePreview · 数值 40 与 "40" ⇒ changed=false（不误报）', () => {
  const preview = buildOptimizePreview(
    fullItem({ performanceWeight: 40 }),
    fullItem({ performanceWeight: '40' as unknown as number }),
  )
  const weight = preview.find((field) => field.key === 'performanceWeight')
  assert.equal(weight!.changed, false)
})

test('buildOptimizePreview · 保留原值和建议值', () => {
  const preview = buildOptimizePreview(
    fullItem({ taskContent: '原内容' }),
    fullItem({ taskContent: '新内容' }),
  )
  const field = preview.find((item) => item.key === 'taskContent')!
  assert.equal(field.originalValue, '原内容')
  assert.equal(field.suggestedValue, '新内容')
})

test('buildOptimizePreview · 权重与价值字段标签存在', () => {
  const preview = buildOptimizePreview(fullItem(), fullItem())
  const weight = preview.find((field) => field.key === 'performanceWeight')!
  const value = preview.find((field) => field.key === 'estimatedValue')!
  assert.equal(weight.label, '绩效权重')
  assert.equal(value.label, '预估价值（元）')
})

test('defaultSelectedOptimizeFields · 只勾选有变化的字段', () => {
  const preview = buildOptimizePreview(
    fullItem(),
    fullItem({ taskContent: '新内容', quality: '新质量' }),
  )
  assert.deepEqual(defaultSelectedOptimizeFields(preview), ['taskContent', 'quality'])
})

test('defaultSelectedOptimizeFields · 没有变化 ⇒ 空数组', () => {
  const preview = buildOptimizePreview(fullItem(), fullItem())
  assert.deepEqual(defaultSelectedOptimizeFields(preview), [])
})

test('displayOptimizeValue · 权重 ⇒ 百分比', () => {
  assert.equal(displayOptimizeValue(40, 'performanceWeight'), '40%')
})

test('displayOptimizeValue · 权重为 null ⇒ 未填写', () => {
  assert.equal(displayOptimizeValue(null, 'performanceWeight'), '未填写')
})

test('displayOptimizeValue · 价值 ⇒ 千分位 + 两位小数', () => {
  assert.equal(displayOptimizeValue(12345.6, 'estimatedValue'), '¥12,345.60')
})

test('displayOptimizeValue · 价值为 null ⇒ 未填写', () => {
  assert.equal(displayOptimizeValue(null, 'estimatedValue'), '未填写')
})

test('displayOptimizeValue · 文本字段 ⇒ 原样字符串', () => {
  assert.equal(displayOptimizeValue('交付物', 'deliverable'), '交付物')
})

test('displayOptimizeValue · 文本字段为 undefined ⇒ 暂无', () => {
  assert.equal(displayOptimizeValue(undefined, 'deliverable'), '暂无')
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. 数值规整与任务名兜底
// ═══════════════════════════════════════════════════════════════════════════

test('editableNumber · 0 ⇒ null（表示未填）', () => {
  assert.equal(editableNumber(0), null)
})

test('editableNumber · 非 0 数字原样返回', () => {
  assert.equal(editableNumber(42.5), 42.5)
})

test('editableNumber · undefined / null ⇒ null', () => {
  assert.equal(editableNumber(undefined), null)
  assert.equal(editableNumber(null), null)
})

test('editableNumber · 非数字字符串 ⇒ null', () => {
  assert.equal(editableNumber('abc'), null)
})

test('numberForSave · null / undefined ⇒ 0', () => {
  assert.equal(numberForSave(null), 0)
  assert.equal(numberForSave(undefined), 0)
})

test('numberForSave · 非数字 ⇒ 0', () => {
  assert.equal(numberForSave('abc'), 0)
})

test('numberForSave · 0 保留为 0（保存态不区分未填）', () => {
  assert.equal(numberForSave(0), 0)
})

test('taskNameForSave · 已有任务名 ⇒ 原样返回', () => {
  assert.equal(taskNameForSave({ taskName: '任务名', taskContent: '内容' }), '任务名')
})

test('taskNameForSave · 任务名为空白 ⇒ 用任务内容兜底', () => {
  assert.equal(taskNameForSave({ taskName: '   ', taskContent: '任务内容' }), '任务内容')
})

test('taskNameForSave · 兜底时折叠连续空白', () => {
  assert.equal(taskNameForSave({ taskName: '', taskContent: '多  空格   内容' }), '多 空格 内容')
})

test('taskNameForSave · 兜底截断到 30 字', () => {
  const content = 'あ'.repeat(50)
  assert.equal(taskNameForSave({ taskName: '', taskContent: content }).length, 30)
})

test('taskNameForSave · 任务名优先于任务内容', () => {
  assert.equal(taskNameForSave({ taskName: '名字', taskContent: '内容内容内容' }), '名字')
})

test('hasTaskContent · 有内容 ⇒ true', () => {
  assert.equal(hasTaskContent({ taskContent: '内容' }), true)
})

test('hasTaskContent · 空白 ⇒ false', () => {
  assert.equal(hasTaskContent({ taskContent: '   ' }), false)
})

test('hasTaskContent · undefined ⇒ false', () => {
  assert.equal(hasTaskContent({ taskContent: undefined as unknown as string }), false)
})

test('isEmptyMonthPlanItem · 全空 ⇒ true', () => {
  assert.equal(isEmptyMonthPlanItem(emptyItem()), true)
})

test('isEmptyMonthPlanItem · 只填了权重 ⇒ false（不算空行）', () => {
  assert.equal(isEmptyMonthPlanItem(emptyItem({ performanceWeight: 10 })), false)
})

test('isEmptyMonthPlanItem · 只填了任务名 ⇒ false', () => {
  assert.equal(isEmptyMonthPlanItem(emptyItem({ taskName: '名字' })), false)
})

test('isEmptyMonthPlanItem · 只填了预估价值 ⇒ false', () => {
  assert.equal(isEmptyMonthPlanItem(emptyItem({ estimatedValue: 0 })), false)
})

test('isEmptyMonthPlanItem · 只填了开始日期 ⇒ false', () => {
  assert.equal(isEmptyMonthPlanItem(emptyItem({ estimatedStartDate: '2026-11-01' })), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. 自动保存状态
// ═══════════════════════════════════════════════════════════════════════════

test('resolveAutoSaveState · 不可编辑 ⇒ 只读', () => {
  assert.deepEqual(resolveAutoSaveState('idle', false, false), { text: '只读', type: 'info' })
})

test('resolveAutoSaveState · readonly 状态 ⇒ 只读', () => {
  assert.deepEqual(resolveAutoSaveState('readonly', true, false), { text: '只读', type: 'info' })
})

test('resolveAutoSaveState · 保存中', () => {
  assert.deepEqual(resolveAutoSaveState('saving', true, true), { text: '自动保存中', type: 'warning' })
})

test('resolveAutoSaveState · 已保存', () => {
  assert.deepEqual(resolveAutoSaveState('saved', true, false), { text: '已自动保存', type: 'success' })
})

test('resolveAutoSaveState · 失败', () => {
  assert.deepEqual(resolveAutoSaveState('error', true, false), { text: '自动保存失败', type: 'danger' })
})

test('resolveAutoSaveState · 空闲 + 有改动 ⇒ 有未保存修改', () => {
  assert.deepEqual(resolveAutoSaveState('idle', true, true), { text: '有未保存修改', type: 'info' })
})

test('resolveAutoSaveState · 空闲 + 无改动 ⇒ 尚未修改', () => {
  assert.deepEqual(resolveAutoSaveState('idle', true, false), { text: '尚未修改', type: 'info' })
})

test('resolveAutoSaveState · 只读优先级高于「保存中」', () => {
  assert.equal(resolveAutoSaveState('saving', false, false).text, '只读')
})
