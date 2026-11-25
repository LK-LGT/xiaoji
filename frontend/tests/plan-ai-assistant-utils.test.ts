import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildDayPlanAiDraft,
  buildWeekPlanAiDraft,
  normalizeAiStringArray,
  normalizeRelatedWeekPlanItemIds,
  normalizeWeekPlanAiItem,
  normalizeWeekPlanAiItems,
  resolveAiAssistantEmptyDescription,
  resolveAiComposerPlaceholder,
  resolveAiComposerSendLabel,
  resolveAiDraftMissingMessage,
  resolveWeekEndDate,
  shouldAdjustAiDraft,
} from '../src/utils/planAiAssistant.ts'

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 结束日计算
// ═══════════════════════════════════════════════════════════════════════════

test('周结束日：开始日 + 6 天', () => {
  assert.equal(resolveWeekEndDate('2026-11-23'), '2026-11-29')
})

test('周结束日：跨月', () => {
  assert.equal(resolveWeekEndDate('2026-11-27'), '2026-12-03')
})

test('周结束日：跨年', () => {
  assert.equal(resolveWeekEndDate('2026-12-29'), '2027-01-04')
})

test('周结束日：结果总是补零', () => {
  assert.equal(resolveWeekEndDate('2026-01-01'), '2026-01-07')
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 归一化
// ═══════════════════════════════════════════════════════════════════════════

test('字符串数组归一：正常数组', () => {
  assert.deepEqual(normalizeAiStringArray(['a', 'b']), ['a', 'b'])
})

test('字符串数组归一：非数组 ⇒ 空数组（不抛错）', () => {
  assert.deepEqual(normalizeAiStringArray(null), [])
  assert.deepEqual(normalizeAiStringArray(undefined), [])
  assert.deepEqual(normalizeAiStringArray('abc'), [])
  assert.deepEqual(normalizeAiStringArray(123), [])
})

test('字符串数组归一：过滤空串', () => {
  assert.deepEqual(normalizeAiStringArray(['a', '', 'b']), ['a', 'b'])
})

test('字符串数组归一：非字符串元素转字符串', () => {
  assert.deepEqual(normalizeAiStringArray([1, 2]), ['1', '2'])
})

test('周条目归一：主键是字符串 UUID（BUG-R6-14：不塌成 NaN）', () => {
  const item = normalizeWeekPlanAiItem({ id: 'abc-123', monthPlanItemId: 'item-uuid' }, '2026-11-29')
  assert.equal(item?.id, 'abc-123')
  assert.equal(item?.monthPlanItemId, 'item-uuid')
})

test('周条目归一：主键缺失 ⇒ undefined（不是空串）', () => {
  const item = normalizeWeekPlanAiItem({}, '2026-11-29')
  assert.equal(item?.id, undefined)
})

test('周条目归一：monthPlanItemId 为 null ⇒ null', () => {
  const item = normalizeWeekPlanAiItem({ monthPlanItemId: null }, '2026-11-29')
  assert.equal(item?.monthPlanItemId, null)
})

test('周条目归一：monthPlanItemId 为空串 ⇒ null', () => {
  const item = normalizeWeekPlanAiItem({ monthPlanItemId: '   ' }, '2026-11-29')
  assert.equal(item?.monthPlanItemId, null)
})

test('周条目归一：非对象 ⇒ null', () => {
  assert.equal(normalizeWeekPlanAiItem(null, '2026-11-29'), null)
  assert.equal(normalizeWeekPlanAiItem('str', '2026-11-29'), null)
  assert.equal(normalizeWeekPlanAiItem(123, '2026-11-29'), null)
})

test('周条目归一：缺字段 ⇒ 空串', () => {
  const item = normalizeWeekPlanAiItem({}, '2026-11-29')
  assert.equal(item?.content, '')
  assert.equal(item?.deliverable, '')
  assert.equal(item?.quality, '')
})

test('周条目归一：没有完成日 ⇒ 用兜底值', () => {
  const item = normalizeWeekPlanAiItem({}, '2026-11-29')
  assert.equal(item?.plannedFinishDate, '2026-11-29')
})

test('周条目归一：有自己的完成日 ⇒ 用自己的', () => {
  const item = normalizeWeekPlanAiItem({ plannedFinishDate: '2026-12-01' }, '2026-11-29')
  assert.equal(item?.plannedFinishDate, '2026-12-01')
})

test('周条目列表归一：过滤掉非对象的项', () => {
  const items = normalizeWeekPlanAiItems([{ id: 'a' }, null, 'x', { id: 'b' }], '2026-11-29')
  assert.deepEqual(items.map((i) => i.id), ['a', 'b'])
})

test('周条目列表归一：非数组 ⇒ 空数组', () => {
  assert.deepEqual(normalizeWeekPlanAiItems(null, '2026-11-29'), [])
  assert.deepEqual(normalizeWeekPlanAiItems({ a: 1 }, '2026-11-29'), [])
})

test('关联周任务 ID 归一：转字符串 + 去空白 + 去空', () => {
  assert.deepEqual(normalizeRelatedWeekPlanItemIds(['  a  ', '', 'b']), ['a', 'b'])
})

test('关联周任务 ID 归一：数字 ID 保留为字符串（BUG-R6-14）', () => {
  assert.deepEqual(normalizeRelatedWeekPlanItemIds([123]), ['123'])
})

test('关联周任务 ID 归一：非数组 ⇒ 空数组', () => {
  assert.deepEqual(normalizeRelatedWeekPlanItemIds(null), [])
  assert.deepEqual(normalizeRelatedWeekPlanItemIds('abc'), [])
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 草稿构造
// ═══════════════════════════════════════════════════════════════════════════

test('周草稿构造：正常载荷', () => {
  const draft = buildWeekPlanAiDraft(
    {
      suggestionId: 'sg-1',
      payload: {
        items: [{ id: 'a', content: 'c' }],
        warnings: ['w1'],
        missingContext: ['m1'],
        notice: '注意',
        draftVersionId: 3,
        draftVersionNo: 7,
      },
    },
    '2026-11-29',
  )
  assert.ok(draft)
  assert.equal(draft.suggestionId, 'sg-1')
  assert.equal(draft.items.length, 1)
  assert.deepEqual(draft.warnings, ['w1'])
  assert.deepEqual(draft.missingContext, ['m1'])
  assert.equal(draft.notice, '注意')
  assert.equal(draft.draftVersionId, 3)
  assert.equal(draft.draftVersionNo, 7)
})

test('周草稿构造：没有可应用条目 ⇒ null', () => {
  assert.equal(buildWeekPlanAiDraft({ payload: { items: [] } }, '2026-11-29'), null)
  assert.equal(buildWeekPlanAiDraft({ payload: {} }, '2026-11-29'), null)
})

test('周草稿构造：载荷里没有 suggestionId ⇒ 用消息上的', () => {
  const draft = buildWeekPlanAiDraft(
    { suggestionId: 'msg-sg', payload: { items: [{ id: 'a' }] } },
    '2026-11-29',
  )
  assert.equal(draft?.suggestionId, 'msg-sg')
})

test('周草稿构造：都没有 suggestionId ⇒ 空串', () => {
  const draft = buildWeekPlanAiDraft({ payload: { items: [{ id: 'a' }] } }, '2026-11-29')
  assert.equal(draft?.suggestionId, '')
})

test('周草稿构造：版本号缺失 ⇒ 0', () => {
  const draft = buildWeekPlanAiDraft({ payload: { items: [{ id: 'a' }] } }, '2026-11-29')
  assert.equal(draft?.draftVersionId, 0)
  assert.equal(draft?.draftVersionNo, 0)
})

test('日草稿构造：正常载荷', () => {
  const draft = buildDayPlanAiDraft({
    suggestionId: 'sg-1',
    payload: {
      content: '今天做这个',
      remark: '备注',
      relatedWeekPlanItemIds: ['a'],
      warnings: ['w'],
    },
  })
  assert.ok(draft)
  assert.equal(draft.content, '今天做这个')
  assert.equal(draft.remark, '备注')
  assert.deepEqual(draft.relatedWeekPlanItemIds, ['a'])
  assert.deepEqual(draft.warnings, ['w'])
})

test('日草稿构造：content 不是字符串 ⇒ null', () => {
  assert.equal(buildDayPlanAiDraft({ payload: { content: 123 } }), null)
  assert.equal(buildDayPlanAiDraft({ payload: {} }), null)
  assert.equal(buildDayPlanAiDraft({}), null)
})

test('日草稿构造：remark 不是字符串 ⇒ 空串', () => {
  const draft = buildDayPlanAiDraft({ payload: { content: 'x', remark: 123 } })
  assert.equal(draft?.remark, '')
})

test('日草稿构造：content 是空字符串也是合法的（区别于不是字符串）', () => {
  const draft = buildDayPlanAiDraft({ payload: { content: '' } })
  assert.ok(draft)
  assert.equal(draft?.content, '')
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 提示文案
// ═══════════════════════════════════════════════════════════════════════════

test('没有可应用内容的提示：周 / 日', () => {
  assert.equal(resolveAiDraftMissingMessage('week'), '这条 AI 消息没有可应用的周计划建议')
  assert.equal(resolveAiDraftMissingMessage('day'), '这条 AI 消息没有可应用的日计划建议')
})

test('空态描述：周 / 日', () => {
  assert.equal(resolveAiAssistantEmptyDescription('week'), '描述本周工作重点和交付要求，生成周计划建议')
  assert.equal(resolveAiAssistantEmptyDescription('day'), '描述今日工作重点和交付要求，生成日计划建议')
})

test('输入框占位：有草稿 ⇒ 继续补充', () => {
  assert.equal(resolveAiComposerPlaceholder('week', true), '继续补充要求或调整当前建议')
  assert.equal(resolveAiComposerPlaceholder('day', true), '继续补充要求或调整当前建议')
})

test('输入框占位：无草稿 ⇒ 按模式', () => {
  assert.equal(resolveAiComposerPlaceholder('week', false), '描述本周工作重点和交付要求')
  assert.equal(resolveAiComposerPlaceholder('day', false), '描述今日工作重点和交付要求')
})

test('发送按钮：有草稿 ⇒ 继续调整', () => {
  assert.equal(resolveAiComposerSendLabel('week', true), '继续调整')
  assert.equal(resolveAiComposerSendLabel('day', true), '继续调整')
})

test('发送按钮：无草稿 ⇒ 生成xxx', () => {
  assert.equal(resolveAiComposerSendLabel('week', false), '生成周计划')
  assert.equal(resolveAiComposerSendLabel('day', false), '生成日计划')
})

test('走调整还是生成：有草稿 ⇒ 调整', () => {
  assert.equal(shouldAdjustAiDraft(true), true)
  assert.equal(shouldAdjustAiDraft(false), false)
})
