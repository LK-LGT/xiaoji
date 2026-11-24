import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildCalendarRuleNotice,
  buildDayPlanDateLabel,
  buildDayPlanSummaryItems,
  collectDayPlanIssues,
  computeDayPlanReadiness,
  countBlockingDayPlanIssues,
  DAY_PLAN_RULE_LABELS,
  DAY_PLAN_STATUS_MAP,
  findBlockingDayPlanIssue,
  formatDayPlanTime,
  hasDayPlanDraftContent,
  isDayPlanDirty,
  isDayPlanEditable,
  normalizeDayPlanForm,
  normalizeDayPlanViewMode,
  parseDayPlanVersionSnapshot,
  persistDayPlanViewMode,
  readDayPlanViewMode,
  resolveDayPlanStatusMeta,
  shouldShowDayPlanContentError,
} from '../src/utils/dayPlanEditor.ts'

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 状态映射
// ═══════════════════════════════════════════════════════════════════════════

test('日计划 8 种状态都有中文文案与色板', () => {
  assert.equal(Object.keys(DAY_PLAN_STATUS_MAP).length, 8)
  for (const [status, meta] of Object.entries(DAY_PLAN_STATUS_MAP)) {
    assert.ok(meta.label, `${status} 缺少中文文案`)
    assert.ok(['info', 'warning', 'success', 'danger'].includes(meta.type), `${status} 色板非法`)
  }
  assert.equal(DAY_PLAN_STATUS_MAP.draft.label, '草稿')
  assert.equal(DAY_PLAN_STATUS_MAP.rejected.label, '已驳回')
  assert.equal(DAY_PLAN_STATUS_MAP.confirmed.label, '已确认')
})

test('未知状态回落到「草稿」而不是崩溃', () => {
  assert.deepEqual(resolveDayPlanStatusMeta('unknown_status'), DAY_PLAN_STATUS_MAP.draft)
  assert.deepEqual(resolveDayPlanStatusMeta(undefined), DAY_PLAN_STATUS_MAP.draft)
  assert.deepEqual(resolveDayPlanStatusMeta(''), DAY_PLAN_STATUS_MAP.draft)
})

test('只有「草稿」和「已驳回」可编辑', () => {
  assert.equal(isDayPlanEditable('draft'), true)
  assert.equal(isDayPlanEditable('rejected'), true)
  for (const status of ['submitted', 'approved', 'confirmed', 'paused', 'canceled', 'archived', 'unknown']) {
    assert.equal(isDayPlanEditable(status), false, `${status} 不应可编辑`)
  }
  assert.equal(isDayPlanEditable(undefined), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 日期与时间
// ═══════════════════════════════════════════════════════════════════════════

test('短日期取月日并把横杠换成斜杠', () => {
  assert.equal(buildDayPlanDateLabel('2026-11-24').startsWith('11/24'), true)
  assert.equal(buildDayPlanDateLabel(''), '-- ')
})

test('星期几按本地日期计算（不因时区挪日期）', () => {
  assert.ok(buildDayPlanDateLabel('2026-11-24').endsWith('星期二'))
  assert.ok(buildDayPlanDateLabel('2026-11-22').endsWith('星期日'))
  assert.ok(buildDayPlanDateLabel('2026-11-28').endsWith('星期六'))
})

test('空日期不产生星期文案', () => {
  assert.equal(buildDayPlanDateLabel('').trim(), '--')
})

test('时间戳截到分钟', () => {
  assert.equal(formatDayPlanTime('2026-11-24T08:30:15'), '2026-11-24 08:30')
  assert.equal(formatDayPlanTime(''), '')
  assert.equal(formatDayPlanTime(undefined), '')
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 排班规则提示
// ═══════════════════════════════════════════════════════════════════════════

test('默认工作日规则不提示（那是常态不是例外）', () => {
  assert.equal(buildCalendarRuleNotice(null), '')
  assert.equal(buildCalendarRuleNotice(undefined), '')
  assert.equal(
    buildCalendarRuleNotice({ ruleType: 'WORKDAY', explicit: false }),
    '',
  )
})

test('显式工作日规则要提示', () => {
  const notice = buildCalendarRuleNotice({ ruleType: 'WORKDAY', explicit: true })
  assert.ok(notice.startsWith('工作日：'))
})

test('强制填报与非强制填报都提示且措辞不同', () => {
  const forced = buildCalendarRuleNotice({ ruleType: 'HOLIDAY', forceReport: true })
  const optional = buildCalendarRuleNotice({ ruleType: 'HOLIDAY', forceReport: false })
  assert.ok(forced.includes('当日需要填报日计划'))
  assert.ok(optional.includes('当日不强制填报日计划'))
  assert.notEqual(forced, optional)
})

test('未知排班类型原样显示类型名', () => {
  const notice = buildCalendarRuleNotice({ ruleType: 'SOME_NEW_TYPE' })
  assert.ok(notice.startsWith('SOME_NEW_TYPE：'))
})

test('规则描述追加在末尾', () => {
  const notice = buildCalendarRuleNotice({
    ruleType: 'LEAVE',
    forceReport: false,
    description: '年假',
  })
  assert.ok(notice.includes('；年假'))
})

test('规则标签表覆盖 6 种排班类型', () => {
  assert.equal(Object.keys(DAY_PLAN_RULE_LABELS).length, 6)
  assert.equal(DAY_PLAN_RULE_LABELS.WORKDAY, '工作日')
  assert.equal(DAY_PLAN_RULE_LABELS.SPECIAL_SHIFT, '特殊排班')
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 脏检查快照
// ═══════════════════════════════════════════════════════════════════════════

test('关联任务 ID 顺序不同不算改动', () => {
  const a = normalizeDayPlanForm({ planDate: '2026-11-24', relatedWeekPlanItemIds: ['b', 'a'], content: 'x' })
  const b = normalizeDayPlanForm({ planDate: '2026-11-24', relatedWeekPlanItemIds: ['a', 'b'], content: 'x' })
  assert.equal(a, b)
})

test('内容首尾空白不算改动', () => {
  const a = normalizeDayPlanForm({ planDate: '2026-11-24', relatedWeekPlanItemIds: [], content: ' 干活 ' })
  const b = normalizeDayPlanForm({ planDate: '2026-11-24', relatedWeekPlanItemIds: [], content: '干活' })
  assert.equal(a, b)
})

test('快照能区分真实改动', () => {
  const a = normalizeDayPlanForm({ planDate: '2026-11-24', relatedWeekPlanItemIds: [], content: 'a' })
  const b = normalizeDayPlanForm({ planDate: '2026-11-24', relatedWeekPlanItemIds: [], content: 'b' })
  assert.notEqual(a, b)
})

test('还没保存过时不算「脏」', () => {
  const form = { planDate: '2026-11-24', relatedWeekPlanItemIds: [], content: 'x' }
  // ⚠️ 快照为空 ⇒ 还没保存过 ⇒ 不算「脏」（否则刚进页面就提示「有未保存修改」）。
  assert.equal(isDayPlanDirty(form, ''), false)
  // ⚠️ 快照非空但与当前不同 ⇒ 确实是「脏」。
  assert.equal(isDayPlanDirty(form, 'different'), true)
})

test('保存过之后内容变了才算「脏」', () => {
  const form = { planDate: '2026-11-24', relatedWeekPlanItemIds: [], content: 'x' }
  const snapshot = normalizeDayPlanForm(form)
  assert.equal(isDayPlanDirty(form, snapshot), false)
  assert.equal(isDayPlanDirty({ ...form, content: 'y' }, snapshot), true)
})

test('草稿内容判定覆盖三种来源', () => {
  assert.equal(hasDayPlanDraftContent({ content: '', relatedWeekPlanItemIds: [], remark: '' }), false)
  assert.equal(hasDayPlanDraftContent({ content: '   ', relatedWeekPlanItemIds: [], remark: '' }), false)
  assert.equal(hasDayPlanDraftContent({ content: 'x', relatedWeekPlanItemIds: [], remark: '' }), true)
  assert.equal(hasDayPlanDraftContent({ content: '', relatedWeekPlanItemIds: ['a'], remark: '' }), true)
  assert.equal(hasDayPlanDraftContent({ content: '', relatedWeekPlanItemIds: [], remark: 'y' }), true)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 提交前检查
// ═══════════════════════════════════════════════════════════════════════════

test('空内容产生一条阻塞型问题', () => {
  const issues = collectDayPlanIssues('   ')
  assert.equal(issues.length, 1)
  assert.equal(issues[0].key, 'content')
  assert.equal(issues[0].blocking, true)
  assert.equal(issues[0].targetId, 'day-plan-content')
})

test('有内容就没有问题', () => {
  assert.deepEqual(collectDayPlanIssues('今天干了活'), [])
})

test('阻塞型问题可以被找到与计数', () => {
  const issues = collectDayPlanIssues('')
  assert.ok(findBlockingDayPlanIssue(issues))
  assert.equal(countBlockingDayPlanIssues(issues), 1)
  assert.equal(countBlockingDayPlanIssues(collectDayPlanIssues('x')), 0)
})

test('准备度只有 0 和 100 两个值', () => {
  assert.equal(computeDayPlanReadiness(''), 0)
  assert.equal(computeDayPlanReadiness('   '), 0)
  assert.equal(computeDayPlanReadiness('x'), 100)
})

test('内容错误只在触发检查后才显示', () => {
  assert.equal(shouldShowDayPlanContentError('', false), false)
  assert.equal(shouldShowDayPlanContentError('', true), true)
  assert.equal(shouldShowDayPlanContentError('x', true), false)
})

test('摘要项反映填写状态与工作要点数', () => {
  assert.deepEqual(buildDayPlanSummaryItems('', 0), [
    { label: '工作内容', value: '未填写' },
    { label: '工作要点', value: '0 项' },
  ])
  assert.deepEqual(buildDayPlanSummaryItems('干活', 3), [
    { label: '工作内容', value: '已填写' },
    { label: '工作要点', value: '3 项' },
  ])
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 视图模式持久化
// ═══════════════════════════════════════════════════════════════════════════

test('只认「cards」，其余回落「table」', () => {
  const fake = { getItem: () => 'cards' } as unknown as Storage
  assert.equal(readDayPlanViewMode(fake), 'cards')
  const table = { getItem: () => 'table' } as unknown as Storage
  assert.equal(readDayPlanViewMode(table), 'table')
  const dirty = { getItem: () => 'weird' } as unknown as Storage
  assert.equal(readDayPlanViewMode(dirty), 'table')
})

test('存储不可用时静默回落「table」', () => {
  assert.equal(readDayPlanViewMode(null), 'table')
  const throwing = { getItem: () => { throw new Error('denied') } } as unknown as Storage
  assert.equal(readDayPlanViewMode(throwing), 'table')
})

test('视图模式切换值统一收敛', () => {
  assert.equal(normalizeDayPlanViewMode('cards'), 'cards')
  assert.equal(normalizeDayPlanViewMode('table'), 'table')
  assert.equal(normalizeDayPlanViewMode(undefined), 'table')
  assert.equal(normalizeDayPlanViewMode(true), 'table')
  assert.equal(normalizeDayPlanViewMode(1), 'table')
})

test('持久化失败静默返回 false', () => {
  const ok = { setItem: () => {} } as unknown as Storage
  assert.equal(persistDayPlanViewMode('cards', ok), true)
  const throwing = { setItem: () => { throw new Error('denied') } } as unknown as Storage
  assert.equal(persistDayPlanViewMode('cards', throwing), false)
  assert.equal(persistDayPlanViewMode('cards', null), true)
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. ⭐ 历史版本恢复
// ═══════════════════════════════════════════════════════════════════════════

test('历史版本快照取出关联 ID 与内容', () => {
  const result = parseDayPlanVersionSnapshot({
    relatedWeekPlanItemIds: ['a', 'b'],
    content: '原内容',
  })
  assert.deepEqual(result.relatedWeekPlanItemIds, ['a', 'b'])
  assert.equal(result.content, '原内容')
})

test('历史版本快照里的数字 ID 转成字符串并去掉空串', () => {
  const result = parseDayPlanVersionSnapshot({
    relatedWeekPlanItemIds: [1, '  ', 2, ' b '],
  })
  assert.deepEqual(result.relatedWeekPlanItemIds, ['1', '2', 'b'])
})

test('历史版本快照缺字段时不崩', () => {
  const result = parseDayPlanVersionSnapshot({})
  assert.deepEqual(result.relatedWeekPlanItemIds, [])
  assert.equal(result.content, '')
})

test('历史版本快照里非数组的 ID 一律当空', () => {
  const result = parseDayPlanVersionSnapshot({ relatedWeekPlanItemIds: 'not-an-array' })
  assert.deepEqual(result.relatedWeekPlanItemIds, [])
})

test('历史版本快照里 content 为 null 时得到空串', () => {
  const result = parseDayPlanVersionSnapshot({ content: null })
  assert.equal(result.content, '')
})
