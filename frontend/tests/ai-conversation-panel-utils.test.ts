import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AI_OPERATION_LABELS,
  AI_VISIBLE_MESSAGE_LIMIT,
  buildAiPeriodLabel,
  buildAiResultSummary,
  canPreviewAiResult,
  countHiddenAiMessages,
  formatAiCompactTime,
  isAiConversationReadonly,
  resolveAiMessageBody,
  resolveAiMessageRoleLabel,
  resolveAiOperationLabel,
  selectVisibleAiMessages,
  type AiConversationMessageLike,
} from '../src/utils/aiConversationPanel.ts'

const msg = (overrides: Partial<AiConversationMessageLike> = {}): AiConversationMessageLike => ({
  role: 'ASSISTANT',
  ...overrides,
})

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 角色与操作码标签
// ═══════════════════════════════════════════════════════════════════════════

test('三种角色都有中文文案', () => {
  assert.equal(resolveAiMessageRoleLabel('USER'), '我')
  assert.equal(resolveAiMessageRoleLabel('ASSISTANT'), 'AI 助手')
  assert.equal(resolveAiMessageRoleLabel('EVENT'), '操作记录')
})

test('操作码映射表覆盖月/周/日全部计划类型', () => {
  assert.ok(AI_OPERATION_LABELS.MONTH_PLAN_DRAFT)
  assert.ok(AI_OPERATION_LABELS.MONTH_PLAN_ITEM_OPTIMIZE)
  assert.ok(AI_OPERATION_LABELS.MONTH_PLAN_CHECK)
  assert.ok(AI_OPERATION_LABELS.WEEK_PLAN_DRAFT)
  assert.ok(AI_OPERATION_LABELS.WEEK_PLAN_ADJUST)
  assert.ok(AI_OPERATION_LABELS.DAY_PLAN_DRAFT)
  assert.ok(AI_OPERATION_LABELS.DAY_PLAN_ADJUST)
})

test('应用类操作码统一映射为「已应用到草稿」', () => {
  assert.equal(resolveAiOperationLabel('APPLY_ALL'), '已应用到草稿')
  assert.equal(resolveAiOperationLabel('APPLY_ITEM'), '已应用到草稿')
  assert.equal(resolveAiOperationLabel('APPLY_FIELDS'), '已应用到草稿')
})

test('未知操作码原样返回', () => {
  assert.equal(resolveAiOperationLabel('SOME_NEW_CODE'), 'SOME_NEW_CODE')
})

test('空操作码返回空串', () => {
  assert.equal(resolveAiOperationLabel(''), '')
  assert.equal(resolveAiOperationLabel(undefined), '')
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 消息正文渲染
// ═══════════════════════════════════════════════════════════════════════════

test('事件消息只显示操作码文案', () => {
  const body = resolveAiMessageBody(msg({
    role: 'EVENT',
    operationCode: 'MONTH_PLAN_DRAFT',
    textContent: '原始正文',
  }))
  assert.equal(body, '生成月计划')
})

test('⚠️ 事件消息没有可映射的码时回落「操作已记录」', () => {
  // ⚠️ **精确对齐重构前行为**：没有 operationCode 时会用正文当码去查表，
  //    查不到就**原样返回正文**（不是丢弃）。
  assert.equal(resolveAiMessageBody(msg({ role: 'EVENT', textContent: '随便什么' })), '随便什么')
  // ⚠️ 正文也为空 ⇒ 才回落「操作已记录」。
  assert.equal(resolveAiMessageBody(msg({ role: 'EVENT', textContent: '' })), '操作已记录')
  assert.equal(resolveAiMessageBody(msg({ role: 'EVENT' })), '操作已记录')
})

test('正文为空时显示操作码文案', () => {
  const body = resolveAiMessageBody(msg({ operationCode: 'PREVIEW', textContent: '' }))
  assert.equal(body, '已查看建议')
})

test('⚠️ 正文为空且没有操作码时回落「内容已记录」', () => {
  assert.equal(resolveAiMessageBody(msg({ textContent: '' })), '内容已记录')
  assert.equal(resolveAiMessageBody(msg({ textContent: '   ' })), '内容已记录')
})

test('有正文时显示正文', () => {
  const body = resolveAiMessageBody(msg({ operationCode: 'PREVIEW', textContent: '这是正文' }))
  assert.equal(body, '这是正文')
})

test('⚠️ 正文本身就是未映射的机器码时要换成中文', () => {
  const body = resolveAiMessageBody(msg({ operationCode: 'PREVIEW', textContent: 'PREVIEW' }))
  assert.equal(body, '已查看建议')
})

test('正文首尾空白被裁掉', () => {
  const body = resolveAiMessageBody(msg({ textContent: '  正文  ' }))
  assert.equal(body, '正文')
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 时间格式化
// ═══════════════════════════════════════════════════════════════════════════

test('紧凑时间只取时分', () => {
  assert.equal(formatAiCompactTime('2026-11-24T08:30:15'), '08:30')
  assert.equal(formatAiCompactTime('2026-11-24 08:30'), '08:30')
})

test('⚠️ 长度不足 16 位时原样返回（不截出乱码）', () => {
  assert.equal(formatAiCompactTime('08:30'), '08:30')
  assert.equal(formatAiCompactTime('2026-11-24'), '2026-11-24')
})

test('空时间返回空串', () => {
  assert.equal(formatAiCompactTime(''), '')
  assert.equal(formatAiCompactTime(undefined), '')
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 结果摘要
// ═══════════════════════════════════════════════════════════════════════════

test('items 载荷逐条列出内容 / 交付物 / 质量', () => {
  const summary = buildAiResultSummary(msg({
    payload: {
      items: [
        { content: '写代码', deliverable: '提交记录', quality: '通过' },
        { content: '写测试' },
      ],
    },
  }))
  assert.ok(summary.includes('1. 写代码'))
  assert.ok(summary.includes('交付物：提交记录'))
  assert.ok(summary.includes('质量：通过'))
  assert.ok(summary.includes('2. 写测试'))
  assert.ok(!summary.includes('交付物：\n'))
})

test('items 内容缺失时回落「AI 未返回工作内容」', () => {
  const summary = buildAiResultSummary(msg({ payload: { items: [{}] } }))
  assert.ok(summary.includes('AI 未返回工作内容'))
})

test('⚠️ items 是空数组时提示「AI 未返回计划建议」', () => {
  assert.equal(buildAiResultSummary(msg({ payload: { items: [] } })), 'AI 未返回计划建议')
})

test('issues 载荷只报条数', () => {
  assert.equal(
    buildAiResultSummary(msg({ payload: { issues: [1, 2, 3] } })),
    '共 3 条检查提示',
  )
  assert.equal(
    buildAiResultSummary(msg({ payload: { issues: [] } })),
    '共 0 条检查提示',
  )
})

test('item 载荷提示单条优化建议', () => {
  assert.equal(buildAiResultSummary(msg({ payload: { item: { id: 'x' } } })), '1 条计划事项优化建议')
})

test('content 载荷直接用内容', () => {
  assert.equal(buildAiResultSummary(msg({ payload: { content: '建议正文' } })), '建议正文')
})

test('⚠️ 都没有时回落正文，再没有才用兜底文案', () => {
  assert.equal(buildAiResultSummary(msg({ payload: {}, textContent: '原始正文' })), '原始正文')
  assert.equal(buildAiResultSummary(msg({ payload: {} })), '结构化建议已保存')
})

test('⚠️ items 优先于 issues（分派顺序固定）', () => {
  const summary = buildAiResultSummary(msg({
    payload: { items: [{ content: 'A' }], issues: [1, 2] },
  }))
  assert.ok(summary.includes('1. A'))
  assert.ok(!summary.includes('条检查提示'))
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 可预览判定
// ═══════════════════════════════════════════════════════════════════════════

test('AI 助手发的带 items 载荷的消息可预览', () => {
  assert.equal(canPreviewAiResult(msg({ payload: { items: [] } })), true)
})

test('AI 助手发的带 content 载荷的消息可预览', () => {
  assert.equal(canPreviewAiResult(msg({ payload: { content: 'x' } })), true)
})

test('⚠️ 用户自己发的消息不可预览', () => {
  assert.equal(canPreviewAiResult(msg({ role: 'USER', payload: { content: 'x' } })), false)
})

test('⚠️ 事件消息不可预览', () => {
  assert.equal(canPreviewAiResult(msg({ role: 'EVENT', payload: { content: 'x' } })), false)
})

test('没有载荷不可预览', () => {
  assert.equal(canPreviewAiResult(msg({ payload: null })), false)
  assert.equal(canPreviewAiResult(msg({})), false)
})

test('只有 issues 载荷不可预览', () => {
  assert.equal(canPreviewAiResult(msg({ payload: { issues: [1] } })), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 消息可见性
// ═══════════════════════════════════════════════════════════════════════════

test('可见消息只保留最近 5 条', () => {
  assert.equal(AI_VISIBLE_MESSAGE_LIMIT, 5)
  const messages = Array.from({ length: 9 }, (_, index) => msg({ textContent: String(index) }))
  const visible = selectVisibleAiMessages(messages)
  assert.equal(visible.length, 5)
  assert.equal(visible[4].textContent, '8')
})

test('消息不足 5 条时全部可见', () => {
  const messages = Array.from({ length: 3 }, (_, index) => msg({ textContent: String(index) }))
  assert.equal(selectVisibleAiMessages(messages).length, 3)
  assert.equal(countHiddenAiMessages(messages), 0)
})

test('被折叠的消息条数正确', () => {
  const messages = Array.from({ length: 9 }, () => msg())
  assert.equal(countHiddenAiMessages(messages), 4)
})

test('空消息列表折叠数为 0', () => {
  assert.equal(countHiddenAiMessages([]), 0)
  assert.deepEqual(selectVisibleAiMessages([]), [])
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. ⭐ 周期标签与只读态
// ═══════════════════════════════════════════════════════════════════════════

test('周期标签按计划类型区分', () => {
  assert.equal(buildAiPeriodLabel('MONTH', '2026-11'), '2026-11 月计划')
  assert.equal(buildAiPeriodLabel('WEEK', '2026-11-24'), '2026-11-24 周计划')
  assert.equal(buildAiPeriodLabel('DAY', '2026-11-24'), '2026-11-24 日计划')
})

test('未知计划类型回落「日计划」', () => {
  assert.equal(buildAiPeriodLabel('WEEKLY', '2026-11'), '2026-11 日计划')
})

test('⚠️ 只有「看历史且会话已结束」才只读', () => {
  assert.equal(isAiConversationReadonly({ status: 'ENDED' }), true)
  assert.equal(isAiConversationReadonly({ status: 'ACTIVE' }), false)
  assert.equal(isAiConversationReadonly(undefined), false)
  assert.equal(isAiConversationReadonly({}), false)
})
