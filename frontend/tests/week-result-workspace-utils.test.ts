import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildEditableWeekResultRow,
  formatWeekResultPeriod,
  isWeekResultRowLocked,
  resolveFocusWeekResultRow,
  resolveMonthEditableWeekResultRow,
  resolveWeekMonthFromQuery,
  resolveWeekPlanIdFromQuery,
  resolveWeekResultCompletion,
  resolveWeekResultDeliverable,
  resolveWeekResultStatusMeta,
  resolveWeekResultValidationMessage,
  validateWeekResultRow,
  type EditableWeekResultRowLike,
} from '../src/utils/weekResultWorkspace.ts'

function makeRow(overrides: Partial<EditableWeekResultRowLike> = {}): EditableWeekResultRowLike {
  return {
    weekPlanId: 'wp-1',
    weekStart: '2026-11-23',
    task: { id: 't-1', content: '任务内容', deliverable: '标准交付物' },
    latestResult: null,
    status: 'NOT_SUBMITTED',
    completionStatus: '',
    deliverable: '',
    remark: '',
    hasEvidence: false,
    file: null,
    ...overrides,
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 完成情况 / 交付物回填
// ═══════════════════════════════════════════════════════════════════════════

test('完成情况回填：已完成 ⇒ 用成果内容', () => {
  assert.equal(resolveWeekResultCompletion({ completionStatus: 'COMPLETED', content: '写完了' }), '写完了')
})

test('完成情况回填：已完成但没内容 ⇒ 显示「已完成」', () => {
  assert.equal(resolveWeekResultCompletion({ completionStatus: 'COMPLETED' }), '已完成')
})

test('完成情况回填：已完成但内容是空串 ⇒ 显示「已完成」', () => {
  assert.equal(resolveWeekResultCompletion({ completionStatus: 'COMPLETED', content: '' }), '已完成')
})

test('完成情况回填：未完成 ⇒ 用原始状态码', () => {
  assert.equal(resolveWeekResultCompletion({ completionStatus: 'IN_PROGRESS', content: '做了一半' }), 'IN_PROGRESS')
})

test('完成情况回填：没状态 ⇒ 用内容', () => {
  assert.equal(resolveWeekResultCompletion({ content: '做了一半' }), '做了一半')
})

test('完成情况回填：什么都没有 ⇒ 空串', () => {
  assert.equal(resolveWeekResultCompletion(null), '')
  assert.equal(resolveWeekResultCompletion(undefined), '')
  assert.equal(resolveWeekResultCompletion({}), '')
})

test('交付物回填：优先用成果里的交付物', () => {
  const row = makeRow({
    latestResult: { deliverable: '用户填的交付物' },
    task: { id: 't-1', deliverable: '任务的标准交付物' },
  })
  assert.equal(resolveWeekResultDeliverable(row), '用户填的交付物')
})

test('交付物回填：成果没填 ⇒ 用任务的标准交付物', () => {
  const row = makeRow({ latestResult: null, task: { id: 't-1', deliverable: '任务的标准交付物' } })
  assert.equal(resolveWeekResultDeliverable(row), '任务的标准交付物')
})

test('交付物回填：两处都没有 ⇒ 空串', () => {
  const row = makeRow({ latestResult: null, task: { id: 't-1' } })
  assert.equal(resolveWeekResultDeliverable(row), '')
})

test('行锁定：已提交 / 已通过 ⇒ 锁定', () => {
  assert.equal(isWeekResultRowLocked({ status: 'SUBMITTED' }), true)
  assert.equal(isWeekResultRowLocked({ status: 'APPROVED' }), true)
})

test('行锁定：已驳回 / 未提交 ⇒ 不锁定', () => {
  assert.equal(isWeekResultRowLocked({ status: 'REJECTED' }), false)
  assert.equal(isWeekResultRowLocked({ status: 'NOT_SUBMITTED' }), false)
})

test('构造可编辑行：回填完成情况 / 交付物 / 备注', () => {
  const row = makeRow({
    latestResult: {
      status: 'REJECTED',
      completionStatus: 'COMPLETED',
      content: '已完成',
      deliverable: '交付物A',
      remark: '备注B',
      evidences: [{ id: 'e1' }],
    },
  })
  const editable = buildEditableWeekResultRow(row)
  assert.equal(editable.status, 'REJECTED')
  assert.equal(editable.completionStatus, '已完成')
  assert.equal(editable.deliverable, '交付物A')
  assert.equal(editable.remark, '备注B')
  assert.equal(editable.hasEvidence, true)
  assert.equal(editable.file, null)
})

test('构造可编辑行：没有成果 ⇒ 未提交 / 无证据', () => {
  const editable = buildEditableWeekResultRow(makeRow())
  assert.equal(editable.status, 'NOT_SUBMITTED')
  assert.equal(editable.hasEvidence, false)
})

test('构造可编辑行：证据数组为空 ⇒ 无证据', () => {
  const editable = buildEditableWeekResultRow(makeRow({ latestResult: { evidences: [] } }))
  assert.equal(editable.hasEvidence, false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 状态展示
// ═══════════════════════════════════════════════════════════════════════════

test('状态展示：四种已知状态', () => {
  assert.deepEqual(resolveWeekResultStatusMeta('SUBMITTED'), { label: '待审批', type: 'warning' })
  assert.deepEqual(resolveWeekResultStatusMeta('APPROVED'), { label: '已通过', type: 'success' })
  assert.deepEqual(resolveWeekResultStatusMeta('REJECTED'), { label: '已驳回', type: 'danger' })
  assert.deepEqual(resolveWeekResultStatusMeta('NOT_SUBMITTED'), { label: '未提交', type: 'info' })
})

test('状态展示：未知状态原样显示状态码', () => {
  assert.deepEqual(resolveWeekResultStatusMeta('SOMETHING_NEW'), { label: 'SOMETHING_NEW', type: 'info' })
})

test('状态展示：空状态 ⇒ 未提交', () => {
  assert.deepEqual(resolveWeekResultStatusMeta(''), { label: '未提交', type: 'info' })
})

test('周期文案：起 至 止', () => {
  assert.equal(formatWeekResultPeriod('2026-11-23', '2026-11-29'), '2026-11-23 至 2026-11-29')
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 焦点行定位
// ═══════════════════════════════════════════════════════════════════════════

test('焦点行：优先路由指定的计划 ID', () => {
  const rows = [
    makeRow({ weekPlanId: 'wp-1' }),
    makeRow({ weekPlanId: 'wp-2' }),
  ]
  assert.equal(resolveFocusWeekResultRow(rows, 'wp-2')?.weekPlanId, 'wp-2')
})

test('焦点行：没指定 ⇒ 第一个未锁定的行', () => {
  const rows = [
    makeRow({ weekPlanId: 'wp-1', status: 'SUBMITTED' }),
    makeRow({ weekPlanId: 'wp-2', status: 'NOT_SUBMITTED' }),
  ]
  assert.equal(resolveFocusWeekResultRow(rows, '')?.weekPlanId, 'wp-2')
})

test('焦点行：全锁定 ⇒ 第一行', () => {
  const rows = [
    makeRow({ weekPlanId: 'wp-1', status: 'APPROVED' }),
    makeRow({ weekPlanId: 'wp-2', status: 'SUBMITTED' }),
  ]
  assert.equal(resolveFocusWeekResultRow(rows, 'nope')?.weekPlanId, 'wp-1')
})

test('焦点行：空列表 ⇒ undefined', () => {
  assert.equal(resolveFocusWeekResultRow([], 'wp-1'), undefined)
})

test('月份内定位：优先未锁定的行', () => {
  const rows = [
    makeRow({ weekPlanId: 'wp-1', planMonth: '2026-11', status: 'SUBMITTED' } as never),
    makeRow({ weekPlanId: 'wp-2', planMonth: '2026-11', status: 'NOT_SUBMITTED' } as never),
  ]
  assert.equal(resolveMonthEditableWeekResultRow(rows, '2026-11')?.weekPlanId, 'wp-2')
})

test('月份内定位：该月全锁 ⇒ 该月第一行', () => {
  const rows = [
    makeRow({ weekPlanId: 'wp-1', planMonth: '2026-11', status: 'APPROVED' } as never),
    makeRow({ weekPlanId: 'wp-2', planMonth: '2026-11', status: 'SUBMITTED' } as never),
  ]
  assert.equal(resolveMonthEditableWeekResultRow(rows, '2026-11')?.weekPlanId, 'wp-1')
})

test('月份内定位：该月不存在 ⇒ undefined', () => {
  const rows = [makeRow({ weekPlanId: 'wp-1', planMonth: '2026-11' } as never)]
  assert.equal(resolveMonthEditableWeekResultRow(rows, '2026-10'), undefined)
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 路由参数
// ═══════════════════════════════════════════════════════════════════════════

test('从 query 取月份：普通值', () => {
  assert.equal(resolveWeekMonthFromQuery({ weekMonth: '2026-11' }), '2026-11')
})

test('从 query 取月份：数组取第一个', () => {
  assert.equal(resolveWeekMonthFromQuery({ weekMonth: ['2026-11', '2026-12'] }), '2026-11')
})

test('从 query 取月份：非法 ⇒ 空串', () => {
  assert.equal(resolveWeekMonthFromQuery({ weekMonth: '2026-1' }), '')
  assert.equal(resolveWeekMonthFromQuery({}), '')
})

test('从 query 取计划 ID：数字 ID 也能匹配（BUG-R6-14）', () => {
  assert.equal(resolveWeekPlanIdFromQuery({ weekPlanId: 123 }), '123')
})

test('从 query 取计划 ID：数组取第一个', () => {
  assert.equal(resolveWeekPlanIdFromQuery({ weekPlanId: ['a', 'b'] }), 'a')
})

test('从 query 取计划 ID：缺失 ⇒ 空串', () => {
  assert.equal(resolveWeekPlanIdFromQuery({}), '')
  assert.equal(resolveWeekPlanIdFromQuery(undefined), '')
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 提交前校验
// ═══════════════════════════════════════════════════════════════════════════

test('校验：锁定行最优先', () => {
  const row = makeRow({ status: 'SUBMITTED', completionStatus: '', deliverable: '' })
  assert.equal(validateWeekResultRow(row), 'LOCKED')
})

test('校验：缺完成情况', () => {
  const row = makeRow({ completionStatus: '   ', deliverable: '交付物' })
  assert.equal(validateWeekResultRow(row), 'MISSING_COMPLETION')
})

test('校验：缺交付物', () => {
  const row = makeRow({ completionStatus: '已完成', deliverable: '  ' })
  assert.equal(validateWeekResultRow(row), 'MISSING_DELIVERABLE')
})

test('校验：都填了 ⇒ 通过', () => {
  const row = makeRow({ completionStatus: '已完成', deliverable: '交付物' })
  assert.equal(validateWeekResultRow(row), null)
})

test('校验文案：三种错误各有文案', () => {
  assert.equal(resolveWeekResultValidationMessage('LOCKED'), '该周成果已提交，不能重复提交')
  assert.equal(resolveWeekResultValidationMessage('MISSING_COMPLETION'), '请填写完成情况')
  assert.equal(resolveWeekResultValidationMessage('MISSING_DELIVERABLE'), '请填写交付物')
})
