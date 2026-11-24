import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildArchiveApprovalEvents,
  buildArchivePrintDetails,
  buildArchiveVersionRows,
  formatArchiveDateTime,
  formatArchiveFileSize,
  formatArchiveMoney,
  formatArchiveNumber,
  formatArchiveOptionalMoney,
  hasLoadingArchiveTask,
  hasRejectedArchiveTask,
  resolveArchiveTaskState,
  resolveArchiveTaskStatusMeta,
  resolveSelectedArchiveTask,
  selectArchiveResultTasks,
  selectFailedArchiveTaskIds,
  selectFailedArchiveTasks,
  sumArchivePersonalPerformance,
  type ArchiveTaskDetailState,
  type ArchiveTaskLike,
} from '../src/utils/monthResultArchive.ts'

// ═══════════════════════════════════════════════════════════════════════════
// 辅助
// ═══════════════════════════════════════════════════════════════════════════

const task = (id: string, overrides: Partial<ArchiveTaskLike> = {}): ArchiveTaskLike => ({
  id,
  status: 'SUBMITTED',
  latestVersion: { id: `${id}-v` },
  ...overrides,
})

const detailState = (
  state: ArchiveTaskDetailState['state'],
  detail: ArchiveTaskDetailState['detail'] = null,
): ArchiveTaskDetailState => ({ state, detail, error: state === 'failed' ? 'boom' : '' })

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 任务筛选
// ═══════════════════════════════════════════════════════════════════════════

test('没有 latestVersion 的任务不展示', () => {
  const tasks = [task('a'), task('b', { latestVersion: null }), task('c', { latestVersion: undefined })]
  const visible = selectArchiveResultTasks(tasks)
  assert.deepEqual(visible.map((item) => item.id), ['a'])
})

test('空任务列表不展示任何东西', () => {
  assert.deepEqual(selectArchiveResultTasks([]), [])
})

test('选中任务优先取用户点选的那个', () => {
  const tasks = [task('a'), task('b')]
  assert.equal(resolveSelectedArchiveTask(tasks, 'b')?.id, 'b')
})

test('未点选时回落到第一个任务', () => {
  const tasks = [task('a'), task('b')]
  assert.equal(resolveSelectedArchiveTask(tasks, null)?.id, 'a')
})

test('⚠️ 点选的任务被过滤掉时回落到第一个（不能出现空详情页）', () => {
  const tasks = [task('a'), task('b', { latestVersion: null })]
  assert.equal(resolveSelectedArchiveTask(tasks, 'b')?.id, 'a')
})

test('全部任务都无版本时选中结果为 null', () => {
  const tasks = [task('a', { latestVersion: null })]
  assert.equal(resolveSelectedArchiveTask(tasks, 'a'), null)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 失败任务
// ═══════════════════════════════════════════════════════════════════════════

test('只有 failed 状态算失败，加载中不算', () => {
  const tasks = [task('a'), task('b'), task('c')]
  const details = new Map<string, ArchiveTaskDetailState>([
    ['a', detailState('failed')],
    ['b', detailState('loading')],
    ['c', detailState('loaded', { personalPerformance: 1 })],
  ])
  assert.deepEqual(selectFailedArchiveTasks(tasks, details).map((item) => item.id), ['a'])
  assert.deepEqual(selectFailedArchiveTaskIds(tasks, details), ['a'])
})

test('未加载过的任务不算失败', () => {
  const details = new Map<string, ArchiveTaskDetailState>()
  assert.deepEqual(selectFailedArchiveTasks([task('a')], details), [])
})

test('加载态检测只看 loading', () => {
  assert.equal(hasLoadingArchiveTask(new Map([['a', detailState('loading')]])), true)
  assert.equal(hasLoadingArchiveTask(new Map([['a', detailState('loaded')]])), false)
  assert.equal(hasLoadingArchiveTask(new Map()), false)
})

test('有任务被退回时能被检出', () => {
  assert.equal(hasRejectedArchiveTask([task('a'), task('b', { status: 'REJECTED' })]), true)
  assert.equal(hasRejectedArchiveTask([task('a')]), false)
})

test('任务详情状态缺失时回落「空闲」', () => {
  const state = resolveArchiveTaskState('missing', new Map())
  assert.equal(state.state, 'idle')
  assert.equal(state.detail, null)
  assert.equal(state.error, '')
})

test('任务状态展示信息覆盖 4 种状态', () => {
  assert.equal(resolveArchiveTaskStatusMeta({ status: 'REJECTED' }).label, '已退回')
  assert.equal(resolveArchiveTaskStatusMeta({ status: 'SUBMITTED' }).label, '审批中')
  assert.equal(resolveArchiveTaskStatusMeta({ status: 'CONFIRMED' }).label, '已确认')
  assert.equal(resolveArchiveTaskStatusMeta({ status: 'NOT_SUBMITTED' }).label, '未提交')
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 绩效合计与打印数据
// ═══════════════════════════════════════════════════════════════════════════

test('绩效合计只算加载成功的', () => {
  const details = new Map<string, ArchiveTaskDetailState>([
    ['a', detailState('loaded', { personalPerformance: 10.5 })],
    ['b', detailState('loaded', { personalPerformance: 4.5 })],
    ['c', detailState('failed')],
  ])
  assert.equal(sumArchivePersonalPerformance(details), 15)
})

test('绩效合计空表为 0', () => {
  assert.equal(sumArchivePersonalPerformance(new Map()), 0)
})

test('⚠️ 加载中但带着旧详情时，绩效仍要计入', () => {
  const details = new Map<string, ArchiveTaskDetailState>([
    ['a', { state: 'loading', detail: { personalPerformance: 8 }, error: '' }],
  ])
  assert.equal(sumArchivePersonalPerformance(details), 8)
})

test('打印详情只含真正有内容的任务', () => {
  const details = new Map<string, ArchiveTaskDetailState>([
    ['a', detailState('loaded', { personalPerformance: 1 })],
    ['b', detailState('failed')],
    ['c', { state: 'loading', detail: { personalPerformance: 2 }, error: '' }],
  ])
  const printed = buildArchivePrintDetails(details)
  assert.deepEqual(Object.keys(printed).sort(), ['a', 'c'])
})

test('打印详情空表得到空对象', () => {
  assert.deepEqual(buildArchivePrintDetails(new Map()), {})
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 审批事件流
// ═══════════════════════════════════════════════════════════════════════════

test('没有详情就没有审批事件', () => {
  assert.deepEqual(buildArchiveApprovalEvents(null), [])
})

test('只有员工提交时只有一个事件', () => {
  const events = buildArchiveApprovalEvents({ status: 'submitted', submittedAt: '2026-11-24T08:00:00' })
  assert.equal(events.length, 1)
  assert.equal(events[0].label, '员工提交')
  assert.equal(events[0].operatorName, '本人')
  assert.equal(events[0].state, 'SUBMITTED')
  assert.equal(events[0].current, true)
})

test('领导未处理过就没有「领导处理」事件', () => {
  const events = buildArchiveApprovalEvents({ status: 'submitted', submittedAt: '2026-11-24T08:00:00' })
  assert.ok(!events.some((event) => event.label === '领导处理'))
})

test('领导处理后产生第二个事件', () => {
  const events = buildArchiveApprovalEvents({
    status: 'confirmed',
    submittedAt: '2026-11-24T08:00:00',
    suggestedAt: '2026-11-24T10:00:00',
    leaderSuggestion: '干得不错',
  })
  assert.equal(events.length, 2)
  assert.equal(events[1].label, '领导处理')
  assert.equal(events[1].operatorName, '直属领导')
  assert.equal(events[1].comment, '干得不错')
  assert.equal(events[1].state, 'APPROVED')
  assert.equal(events[1].current, false)
})

test('⚠️ 成果被退回时领导事件状态是 REJECTED', () => {
  const events = buildArchiveApprovalEvents({
    status: 'rejected',
    submittedAt: '2026-11-24T08:00:00',
    suggestedAt: '2026-11-24T10:00:00',
  })
  assert.equal(events[1].state, 'REJECTED')
})

test('部门确认后产生第三个事件', () => {
  const events = buildArchiveApprovalEvents({
    status: 'confirmed',
    submittedAt: '2026-11-24T08:00:00',
    suggestedAt: '2026-11-24T10:00:00',
    confirmedAt: '2026-11-25T09:00:00',
    confirmComment: '确认通过',
  })
  assert.equal(events.length, 3)
  assert.equal(events[2].label, '部门确认')
  assert.equal(events[2].operatorName, '部门负责人')
  assert.equal(events[2].state, 'CONFIRMED')
  assert.equal(events[2].comment, '确认通过')
})

test('已流转后「员工提交」不再是 current', () => {
  const events = buildArchiveApprovalEvents({
    status: 'confirmed',
    submittedAt: '2026-11-24T08:00:00',
    confirmedAt: '2026-11-25T09:00:00',
  })
  assert.equal(events[0].current, false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 历史版本列表
// ═══════════════════════════════════════════════════════════════════════════

const version = (id: string, overrides: Record<string, unknown> = {}) => ({
  id,
  versionNo: id,
  monthPlanId: 'plan-1',
  monthPlanItemId: 'task-1',
  submittedAt: '2026-11-24T08:00:00',
  status: 'submitted',
  leaderSuggestion: '',
  ...overrides,
})

test('没有任务 ID 时不返回版本', () => {
  assert.deepEqual(buildArchiveVersionRows([version('v1')], { planId: 'plan-1', taskId: '' }), [])
})

test('只返回属于当前计划 + 当前任务的版本', () => {
  const rows = buildArchiveVersionRows([
    version('v1'),
    version('v2', { monthPlanId: 'other-plan' }),
    version('v3', { monthPlanItemId: 'other-task' }),
  ], { planId: 'plan-1', taskId: 'task-1' })
  assert.deepEqual(rows.map((row) => row.resultId), ['v1'])
})

test('⚠️ 排除草稿与未提交版本', () => {
  const rows = buildArchiveVersionRows([
    version('v1'),
    version('v2', { status: 'draft' }),
    version('v3', { status: 'not_submitted' }),
  ], { planId: 'plan-1', taskId: 'task-1' })
  assert.deepEqual(rows.map((row) => row.resultId), ['v1'])
})

test('版本按提交时间倒序', () => {
  const rows = buildArchiveVersionRows([
    version('v1', { submittedAt: '2026-11-20T08:00:00' }),
    version('v2', { submittedAt: '2026-11-24T08:00:00' }),
    version('v3', { submittedAt: '2026-11-22T08:00:00' }),
  ], { planId: 'plan-1', taskId: 'task-1' })
  assert.deepEqual(rows.map((row) => row.resultId), ['v2', 'v3', 'v1'])
})

test('同时间按 ID 倒序（排序稳定）', () => {
  const rows = buildArchiveVersionRows([
    version('v1'),
    version('v2'),
    version('v3'),
  ], { planId: 'plan-1', taskId: 'task-1' })
  assert.deepEqual(rows.map((row) => row.resultId), ['v3', 'v2', 'v1'])
})

test('当前版本被标记 current', () => {
  const rows = buildArchiveVersionRows([version('v1'), version('v2')], {
    planId: 'plan-1',
    taskId: 'task-1',
    latestVersionId: 'v2',
  })
  assert.equal(rows.find((row) => row.resultId === 'v2')?.current, true)
  assert.equal(rows.find((row) => row.resultId === 'v1')?.current, false)
})

test('版本变更摘要按状态区分', () => {
  const rows = buildArchiveVersionRows([
    version('v1', { status: 'rejected', leaderSuggestion: '内容不足' }),
    version('v2', { status: 'confirmed' }),
    version('v3', { status: 'submitted' }),
  ], { planId: 'plan-1', taskId: 'task-1' })
  const byId = Object.fromEntries(rows.map((row) => [row.resultId, row]))
  assert.equal(byId.v1.changeSummary, '成果退回')
  assert.equal(byId.v1.rejectionComment, '内容不足')
  assert.equal(byId.v2.changeSummary, '成果确认')
  assert.equal(byId.v2.rejectionComment, '')
  assert.equal(byId.v3.changeSummary, '提交成果')
})

test('版本行的时间已格式化', () => {
  const rows = buildArchiveVersionRows([version('v1', { submittedAt: '2026-11-24T08:30:00' })], {
    planId: 'plan-1',
    taskId: 'task-1',
  })
  assert.equal(rows[0].submittedAt, '2026-11-24 08:30')
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 数值与文件大小格式化
// ═══════════════════════════════════════════════════════════════════════════

test('时间戳格式化，空值回落 --', () => {
  assert.equal(formatArchiveDateTime('2026-11-24T08:30:15'), '2026-11-24 08:30')
  assert.equal(formatArchiveDateTime(''), '--')
  assert.equal(formatArchiveDateTime(undefined), '--')
})

test('普通数字最多两位小数', () => {
  assert.equal(formatArchiveNumber(1234.567), '1,234.57')
  assert.equal(formatArchiveNumber(0), '0')
  assert.equal(formatArchiveNumber(undefined), '0')
})

test('金额固定两位小数', () => {
  assert.equal(formatArchiveMoney(1234.5), '1,234.50')
  assert.equal(formatArchiveMoney(0), '0.00')
  assert.equal(formatArchiveMoney(undefined), '0.00')
})

test('⚠️ 可选金额：null 显示「未填写」而不是 0.00', () => {
  assert.equal(formatArchiveOptionalMoney(null), '未填写')
  assert.equal(formatArchiveOptionalMoney(undefined), '未填写')
  assert.equal(formatArchiveOptionalMoney(0), '0.00')
  assert.equal(formatArchiveOptionalMoney(12.3), '12.30')
})

test('文件大小：小于 1MB 用 KB 整数', () => {
  assert.equal(formatArchiveFileSize(1024), '1 KB')
  assert.equal(formatArchiveFileSize(1024 * 500), '500 KB')
})

test('文件大小：大于等于 1MB 用 MB 两位小数', () => {
  assert.equal(formatArchiveFileSize(1024 * 1024), '1.00 MB')
  assert.equal(formatArchiveFileSize(1024 * 1024 * 2.5), '2.50 MB')
})

test('文件大小：0 / 未知显示「未知大小」', () => {
  assert.equal(formatArchiveFileSize(0), '未知大小')
  assert.equal(formatArchiveFileSize(undefined), '未知大小')
})
