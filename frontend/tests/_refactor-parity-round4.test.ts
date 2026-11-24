import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildArchiveApprovalEvents,
  buildArchivePrintDetails,
  buildArchiveVersionRows,
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
import {
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
import {
  buildCalendarRuleNotice,
  buildDayPlanDateLabel,
  buildDayPlanSummaryItems,
  collectDayPlanIssues,
  computeDayPlanReadiness,
  formatDayPlanTime,
  hasDayPlanDraftContent,
  isDayPlanDirty,
  isDayPlanEditable,
  normalizeDayPlanForm,
  normalizeDayPlanViewMode,
  parseDayPlanVersionSnapshot,
  readDayPlanViewMode,
  resolveDayPlanStatusMeta,
  shouldShowDayPlanContentError,
} from '../src/utils/dayPlanEditor.ts'

// ═══════════════════════════════════════════════════════════════════════════
// ⚠️ **本文件的目的**：证明「抽纯函数这一步**没有改变任何行为**」。
//
// 做法：把**重构前的实现逐字抄一遍**放在测试里（``legacy*`` 函数），
//       然后在**输入矩阵**上断言 `deepEqual(实际, legacy)`。
//
// ⇒ 任何行为漂移都会被这个文件抓住。
// ═══════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 日计划 —— 重构前逐字实现
// ═══════════════════════════════════════════════════════════════════════════

const legacyPlanStatusMap = {
  draft: { label: '草稿', type: 'info' },
  submitted: { label: '已提交', type: 'warning' },
  approved: { label: '已通过', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' },
  confirmed: { label: '已确认', type: 'success' },
  paused: { label: '已暂停', type: 'warning' },
  canceled: { label: '已撤销', type: 'danger' },
  archived: { label: '已归档', type: 'info' },
} as const

function legacyStatusMeta(status?: string) {
  return legacyPlanStatusMap[status as keyof typeof legacyPlanStatusMap] ?? legacyPlanStatusMap.draft
}

function legacyEditable(status: string) { return status === 'draft' || status === 'rejected' }

function legacyShortDate(value: string) { return value ? value.slice(5).replace('-', '/') : '--' }

function legacyWeekdayLabel(value: string) {
  if (!value) return ''
  return ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][new Date(`${value}T00:00:00`).getDay()]
}

function legacyFormatTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 16) : '' }

function legacyCalendarRuleNotice(rule: {
  ruleType: string
  explicit?: boolean
  forceReport?: boolean
  description?: string
} | null) {
  if (!rule || (!rule.explicit && rule.ruleType === 'WORKDAY')) return ''
  const labels: Record<string, string> = {
    WORKDAY: '工作日', WEEKEND: '周末', HOLIDAY: '节假日',
    LEAVE: '请假', BUSINESS_TRIP: '出差', SPECIAL_SHIFT: '特殊排班',
  }
  return `${labels[rule.ruleType] || rule.ruleType}：${rule.forceReport ? '当日需要填报日计划' : '当日不强制填报日计划'}${rule.description ? `；${rule.description}` : ''}`
}

function legacyNormalizeForm(form: { planDate: string; relatedWeekPlanItemIds: string[]; content: string }) {
  return JSON.stringify({
    planDate: form.planDate,
    relatedWeekPlanItemIds: [...form.relatedWeekPlanItemIds].sort(),
    content: form.content.trim(),
  })
}

function legacyDirty(form: { planDate: string; relatedWeekPlanItemIds: string[]; content: string }, savedSnapshot: string) {
  return !!savedSnapshot && legacyNormalizeForm(form) !== savedSnapshot
}

function legacyHasDraftContent(form: { content: string; relatedWeekPlanItemIds: string[]; remark: string }) {
  return Boolean(form.content.trim() || form.relatedWeekPlanItemIds.length || form.remark.trim())
}

function legacyIssues(content: string) {
  const result: { key: string; label: string; targetId?: string; blocking?: boolean }[] = []
  if (!content.trim()) {
    result.push({ key: 'content', label: '请填写当天工作内容', targetId: 'day-plan-content', blocking: true })
  }
  return result
}

function legacyReadiness(content: string) { return content.trim() ? 100 : 0 }

function legacyShowContentError(content: string, feedbackVisible: boolean) {
  return feedbackVisible && !content.trim()
}

function legacySummaryItems(content: string, workPointCount: number) {
  return [
    { label: '工作内容', value: content.trim() ? '已填写' : '未填写' },
    { label: '工作要点', value: `${workPointCount} 项` },
  ]
}

function legacyReadViewMode(storage: { getItem: (key: string) => string | null }) {
  try {
    const saved = storage.getItem('day-plan-task-view')
    return saved === 'cards' ? 'cards' : 'table'
  } catch {
    return 'table'
  }
}

function legacyChangeViewMode(value: string | number | boolean | undefined) {
  return value === 'cards' ? 'cards' : 'table'
}

function legacyRestoreSnapshot(snapshot: Record<string, unknown>) {
  return {
    relatedWeekPlanItemIds: Array.isArray(snapshot.relatedWeekPlanItemIds)
      ? snapshot.relatedWeekPlanItemIds.map((item) => String(item).trim()).filter(Boolean)
      : [],
    content: String(snapshot.content ?? ''),
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 归档详情 —— 重构前逐字实现
// ═══════════════════════════════════════════════════════════════════════════

function legacyResultTasks(tasks: ArchiveTaskLike[]) { return tasks.filter((task) => task.latestVersion) }

function legacySelectedTask(tasks: ArchiveTaskLike[], selectedTaskId: string | null) {
  const visible = legacyResultTasks(tasks)
  return visible.find((task) => task.id === selectedTaskId) || visible[0] || null
}

function legacyFailedTasks(tasks: ArchiveTaskLike[], taskDetails: Map<string, ArchiveTaskDetailState>) {
  return tasks.filter((task) => taskDetails.get(task.id)?.state === 'failed')
}

function legacyHasLoading(taskDetails: Map<string, ArchiveTaskDetailState>) {
  return [...taskDetails.values()].some((item) => item.state === 'loading')
}

function legacyHasRejected(tasks: ArchiveTaskLike[]) {
  return tasks.some((task) => task.status === 'REJECTED')
}

function legacyTaskState(taskId: string, taskDetails: Map<string, ArchiveTaskDetailState>) {
  return taskDetails.get(taskId) || { state: 'idle', detail: null, error: '' }
}

function legacyTaskStatusMeta(task: { status: any }) {
  return ({
    REJECTED: { label: '已退回', type: 'danger' as const },
    SUBMITTED: { label: '审批中', type: 'primary' as const },
    CONFIRMED: { label: '已确认', type: 'success' as const },
    NOT_SUBMITTED: { label: '未提交', type: 'info' as const },
  })[task.status]
}

function legacyPerformanceTotal(taskDetails: Map<string, ArchiveTaskDetailState>) {
  return [...taskDetails.values()].reduce((sum, item) => sum + Number(item.detail?.personalPerformance || 0), 0)
}

function legacyPrintDetails(taskDetails: Map<string, ArchiveTaskDetailState>) {
  return Object.fromEntries(
    [...taskDetails.entries()].flatMap(([taskId, value]) => value.detail ? [[taskId, value.detail]] : []),
  )
}

function legacyApprovalEvents(detail: {
  status?: string
  submittedAt?: string
  suggestedAt?: string
  confirmedAt?: string
  leaderSuggestion?: string
  confirmComment?: string
} | null) {
  if (!detail) return []
  return [
    { label: '员工提交', operatorName: '本人', occurredAt: detail.submittedAt, state: 'SUBMITTED', current: detail.status === 'submitted' },
    ...(detail.suggestedAt ? [{ label: '领导处理', operatorName: '直属领导', occurredAt: detail.suggestedAt, comment: detail.leaderSuggestion, state: detail.status === 'rejected' ? 'REJECTED' : 'APPROVED', current: false }] : []),
    ...(detail.confirmedAt ? [{ label: '部门确认', operatorName: '部门负责人', occurredAt: detail.confirmedAt, comment: detail.confirmComment, state: 'CONFIRMED', current: false }] : []),
  ]
}

function legacyVersionRows(
  versions: { id: string; versionNo: number | string; monthPlanId?: string; monthPlanItemId?: string; submittedAt?: string; status?: string; leaderSuggestion?: string }[],
  planId: string | undefined,
  taskId: string,
  latestVersionId: string | undefined,
) {
  return versions
    .filter((version) => version.monthPlanId === planId && version.monthPlanItemId === taskId && !['draft', 'not_submitted'].includes(version.status as string))
    .sort((left, right) => String(right.submittedAt).localeCompare(String(left.submittedAt)) || String(right.id).localeCompare(String(left.id)))
    .map((version) => ({
      resultId: version.id,
      versionNo: String(version.versionNo),
      submittedAt: legacyDateTime(version.submittedAt),
      changeSummary: version.status === 'rejected' ? '成果退回' : version.status === 'confirmed' ? '成果确认' : '提交成果',
      rejectionComment: version.status === 'rejected' ? version.leaderSuggestion : '',
      current: version.id === latestVersionId,
    }))
}

function legacyDateTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 16) : '--' }
function legacyNumberText(value?: number) { return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }
function legacyMoneyText(value?: number) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }
function legacyOptionalMoneyText(value?: number) { return value == null ? '未填写' : legacyMoneyText(value) }
function legacyFileSize(value?: number) {
  if (!value) return '未知大小'
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(2)} MB`
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ AI 对话 —— 重构前逐字实现
// ═══════════════════════════════════════════════════════════════════════════

function legacyRoleLabel(role: string) {
  return role === 'USER' ? '我' : role === 'ASSISTANT' ? 'AI 助手' : '操作记录'
}

function legacyOperationLabel(code?: string) {
  const labels: Record<string, string> = {
    MONTH_PLAN_DRAFT: '生成月计划', MONTH_PLAN_ITEM_OPTIMIZE: '优化计划事项', MONTH_PLAN_CHECK: '检查月计划',
    WEEK_PLAN_DRAFT: '生成周计划', WEEK_PLAN_ADJUST: '调整周计划', DAY_PLAN_DRAFT: '生成日计划', DAY_PLAN_ADJUST: '调整日计划',
    PREVIEW: '已查看建议', APPLY_ALL: '已应用到草稿', APPLY_ITEM: '已应用到草稿', APPLY_FIELDS: '已应用到草稿', ADOPT_WITH_EDIT: '修改后采纳', IGNORE: '已忽略',
  }
  return code ? labels[code] || code : ''
}

function legacyMessageBody(message: AiConversationMessageLike) {
  const raw = (message.textContent || '').trim()
  const mapped = legacyOperationLabel(message.operationCode || raw)
  if (message.role === 'EVENT') return mapped || '操作已记录'
  if (!raw) return mapped || '内容已记录'
  return mapped && raw === (message.operationCode || raw) ? mapped : raw
}

function legacyCompactTime(value?: string) {
  if (!value) return ''
  const normalized = value.replace('T', ' ')
  return normalized.length >= 16 ? normalized.slice(11, 16) : normalized
}

function legacyResultSummary(message: AiConversationMessageLike) {
  const payload = message.payload || {}
  if (Array.isArray(payload.items)) {
    return payload.items.map((raw, index) => {
      const item = raw as Record<string, unknown>
      return [
        `${index + 1}. ${String(item.content || 'AI 未返回工作内容')}`,
        item.deliverable ? `交付物：${String(item.deliverable)}` : '',
        item.quality ? `质量：${String(item.quality)}` : '',
      ].filter(Boolean).join('\n')
    }).join('\n\n') || 'AI 未返回计划建议'
  }
  if (Array.isArray(payload.issues)) return `共 ${payload.issues.length} 条检查提示`
  if (payload.item) return '1 条计划事项优化建议'
  if (typeof payload.content === 'string') return payload.content
  return message.textContent || '结构化建议已保存'
}

function legacyCanPreview(message: AiConversationMessageLike) {
  if (message.role !== 'ASSISTANT' || !message.payload) return false
  const payload = message.payload
  return Array.isArray(payload.items) || typeof payload.content === 'string'
}

function legacyVisibleMessages(messages: AiConversationMessageLike[]) { return messages.slice(-5) }
function legacyHiddenCount(messages: AiConversationMessageLike[]) {
  return Math.max(0, messages.length - legacyVisibleMessages(messages).length)
}

function legacyPeriodLabel(planType: string, periodKey: string) {
  const type = planType === 'MONTH' ? '月计划' : planType === 'WEEK' ? '周计划' : '日计划'
  return `${periodKey} ${type}`
}

function legacyReadonly(viewingConversation: { status?: string } | undefined) {
  return Boolean(viewingConversation && viewingConversation.status === 'ENDED')
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 输入矩阵
// ═══════════════════════════════════════════════════════════════════════════

const STATUSES = ['draft', 'submitted', 'approved', 'rejected', 'confirmed', 'paused', 'canceled', 'archived', 'unknown', '']
const DATES = ['2026-11-24', '2026-01-01', '2026-12-31', '2027-02-28', '', '2026-11-24T10:00:00']
const CONTENTS = ['', '   ', '干活', '  干活  ', '1. A\n2. B', 'x'.repeat(2000)]
const ID_SETS = [[], ['a'], ['b', 'a'], ['a', 'b'], ['a', 'a'], ['z', 'y', 'x']]
const RULE_TYPES = ['WORKDAY', 'WEEKEND', 'HOLIDAY', 'LEAVE', 'BUSINESS_TRIP', 'SPECIAL_SHIFT', 'CUSTOM']
const OPERATION_CODES = ['MONTH_PLAN_DRAFT', 'PREVIEW', 'APPLY_ALL', 'IGNORE', 'UNKNOWN_CODE', '', undefined]
const ROLES = ['USER', 'ASSISTANT', 'EVENT'] as const

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 日计划等价性
// ═══════════════════════════════════════════════════════════════════════════

test('等价性：状态映射在全部状态上与重构前一致', () => {
  for (const status of STATUSES) {
    assert.deepEqual(resolveDayPlanStatusMeta(status), legacyStatusMeta(status), `status=${status}`)
  }
})

test('等价性：可编辑判定在全部状态上与重构前一致', () => {
  for (const status of STATUSES) {
    assert.equal(isDayPlanEditable(status), legacyEditable(status), `status=${status}`)
  }
})

test('等价性：日期标签在全部日期上与重构前一致', () => {
  for (const date of DATES) {
    assert.equal(buildDayPlanDateLabel(date), `${legacyShortDate(date)} ${legacyWeekdayLabel(date)}`, `date=${date}`)
  }
})

test('等价性：时间格式化在全部输入上与重构前一致', () => {
  for (const value of [...DATES, undefined]) {
    assert.equal(formatDayPlanTime(value), legacyFormatTime(value), `value=${value}`)
  }
})

test('等价性：排班提示在「类型 × 显式 × 强制 × 描述」矩阵上与重构前一致', () => {
  for (const ruleType of RULE_TYPES) {
    for (const explicit of [true, false, undefined]) {
      for (const forceReport of [true, false, undefined]) {
        for (const description of ['', '年假', '调休']) {
          const rule = { ruleType, explicit, forceReport, description }
          assert.equal(buildCalendarRuleNotice(rule), legacyCalendarRuleNotice(rule), JSON.stringify(rule))
        }
      }
    }
  }
  assert.equal(buildCalendarRuleNotice(null), legacyCalendarRuleNotice(null))
})

test('等价性：快照归一化在「日期 × ID 组合 × 内容」矩阵上与重构前一致', () => {
  for (const planDate of DATES) {
    for (const relatedWeekPlanItemIds of ID_SETS) {
      for (const content of CONTENTS) {
        const form = { planDate, relatedWeekPlanItemIds, content }
        assert.equal(normalizeDayPlanForm(form), legacyNormalizeForm(form), JSON.stringify(form))
      }
    }
  }
})

test('等价性：脏检查在「表单 × 快照」矩阵上与重构前一致', () => {
  for (const planDate of DATES) {
    for (const relatedWeekPlanItemIds of ID_SETS) {
      for (const content of CONTENTS) {
        const form = { planDate, relatedWeekPlanItemIds, content }
        for (const snapshot of ['', 'x', legacyNormalizeForm(form)]) {
          assert.equal(isDayPlanDirty(form, snapshot), legacyDirty(form, snapshot), JSON.stringify({ form, snapshot }))
        }
      }
    }
  }
})

test('等价性：草稿内容判定在全部组合上与重构前一致', () => {
  for (const content of CONTENTS) {
    for (const relatedWeekPlanItemIds of ID_SETS) {
      for (const remark of ['', ' ', '备注']) {
        const form = { content, relatedWeekPlanItemIds, remark }
        assert.equal(hasDayPlanDraftContent(form), legacyHasDraftContent(form), JSON.stringify(form))
      }
    }
  }
})

test('等价性：校验问题在全部内容上与重构前一致', () => {
  for (const content of CONTENTS) {
    assert.deepEqual(collectDayPlanIssues(content), legacyIssues(content), `content=${content}`)
  }
})

test('等价性：准备度与错误提示在全部组合上与重构前一致', () => {
  for (const content of CONTENTS) {
    assert.equal(computeDayPlanReadiness(content), legacyReadiness(content), `content=${content}`)
    for (const feedbackVisible of [true, false]) {
      assert.equal(
        shouldShowDayPlanContentError(content, feedbackVisible),
        legacyShowContentError(content, feedbackVisible),
      )
    }
  }
})

test('等价性：摘要项在全部内容 × 要点数上与重构前一致', () => {
  for (const content of CONTENTS) {
    for (const count of [0, 1, 5, 99]) {
      assert.deepEqual(buildDayPlanSummaryItems(content, count), legacySummaryItems(content, count))
    }
  }
})

test('等价性：视图模式读取与切换在全部输入上与重构前一致', () => {
  const storages: { getItem: (key: string) => string | null }[] = [
    { getItem: () => 'cards' },
    { getItem: () => 'table' },
    { getItem: () => 'weird' },
    { getItem: () => null },
    { getItem: () => { throw new Error('denied') } },
  ]
  for (const storage of storages) {
    assert.equal(readDayPlanViewMode(storage as unknown as Storage), legacyReadViewMode(storage))
  }
  for (const value of ['cards', 'table', '', undefined, true, false, 1, 0] as const) {
    assert.equal(normalizeDayPlanViewMode(value), legacyChangeViewMode(value), `value=${value}`)
  }
})

test('等价性：历史版本快照解析在全部输入上与重构前一致', () => {
  const snapshots: Record<string, unknown>[] = [
    {},
    { content: 'x' },
    { content: null },
    { content: 123 },
    { relatedWeekPlanItemIds: ['a', 'b'] },
    { relatedWeekPlanItemIds: [1, '  ', 2] },
    { relatedWeekPlanItemIds: null },
    { relatedWeekPlanItemIds: 'not-array' },
    { relatedWeekPlanItemIds: [], content: 'y' },
  ]
  for (const snapshot of snapshots) {
    assert.deepEqual(parseDayPlanVersionSnapshot(snapshot), legacyRestoreSnapshot(snapshot), JSON.stringify(snapshot))
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 归档详情等价性
// ═══════════════════════════════════════════════════════════════════════════

const ARCHIVE_TASKS: ArchiveTaskLike[] = [
  { id: 'a', status: 'SUBMITTED', latestVersion: { id: 'a1' } },
  { id: 'b', status: 'REJECTED', latestVersion: { id: 'b1' } },
  { id: 'c', status: 'CONFIRMED', latestVersion: { id: 'c1' } },
  { id: 'd', status: 'NOT_SUBMITTED', latestVersion: null },
  { id: 'e', status: 'SUBMITTED', latestVersion: undefined },
]

test('等价性：任务筛选与选中在全部任务集上与重构前一致', () => {
  for (const selectedTaskId of [null, 'a', 'b', 'd', 'missing']) {
    assert.deepEqual(
      selectArchiveResultTasks(ARCHIVE_TASKS),
      legacyResultTasks(ARCHIVE_TASKS),
    )
    assert.deepEqual(
      resolveSelectedArchiveTask(ARCHIVE_TASKS, selectedTaskId),
      legacySelectedTask(ARCHIVE_TASKS, selectedTaskId),
      `selectedTaskId=${selectedTaskId}`,
    )
  }
})

test('等价性：失败任务与加载态在全部状态组合上与重构前一致', () => {
  const states: ArchiveTaskDetailState['state'][] = ['idle', 'loading', 'loaded', 'failed']
  for (const stateA of states) {
    for (const stateB of states) {
      const details = new Map<string, ArchiveTaskDetailState>([
        ['a', { state: stateA, detail: null, error: '' }],
        ['b', { state: stateB, detail: null, error: 'boom' }],
      ])
      assert.deepEqual(selectFailedArchiveTasks(ARCHIVE_TASKS, details), legacyFailedTasks(ARCHIVE_TASKS, details))
      assert.deepEqual(
        selectFailedArchiveTaskIds(ARCHIVE_TASKS, details),
        legacyFailedTasks(ARCHIVE_TASKS, details).map((task) => task.id),
      )
      assert.equal(hasLoadingArchiveTask(details), legacyHasLoading(details))
    }
  }
})

test('等价性：退回检出与任务状态文案在全部任务上与重构前一致', () => {
  assert.equal(hasRejectedArchiveTask(ARCHIVE_TASKS), legacyHasRejected(ARCHIVE_TASKS))
  for (const task of ARCHIVE_TASKS) {
    assert.deepEqual(resolveArchiveTaskStatusMeta(task), legacyTaskStatusMeta(task), `task=${task.id}`)
  }
})

test('等价性：任务详情状态缺失时与重构前一致', () => {
  const details = new Map<string, ArchiveTaskDetailState>([
    ['a', { state: 'loaded', detail: { personalPerformance: 1 }, error: '' }],
  ])
  assert.deepEqual(resolveArchiveTaskState('a', details), legacyTaskState('a', details))
  assert.deepEqual(resolveArchiveTaskState('missing', details), legacyTaskState('missing', details))
})

test('等价性：绩效合计与打印数据在全部绩效组合上与重构前一致', () => {
  const values = [0, 1, 10.5, -3, 999.99, undefined]
  for (const valueA of values) {
    for (const valueB of values) {
      const details = new Map<string, ArchiveTaskDetailState>([
        ['a', { state: 'loaded', detail: { personalPerformance: valueA as number }, error: '' }],
        ['b', { state: 'failed', detail: null, error: 'boom' }],
        ['c', { state: 'loading', detail: { personalPerformance: valueB as number }, error: '' }],
      ])
      assert.equal(sumArchivePersonalPerformance(details), legacyPerformanceTotal(details))
      assert.deepEqual(buildArchivePrintDetails(details), legacyPrintDetails(details))
    }
  }
})

test('等价性：审批事件在全部时间点组合上与重构前一致', () => {
  const times = [undefined, '2026-11-24T08:00:00']
  for (const submittedAt of times) {
    for (const suggestedAt of times) {
      for (const confirmedAt of times) {
        for (const status of ['submitted', 'confirmed', 'rejected', undefined]) {
          const detail = { status, submittedAt, suggestedAt, confirmedAt, leaderSuggestion: '意见', confirmComment: '确认' }
          assert.deepEqual(buildArchiveApprovalEvents(detail), legacyApprovalEvents(detail), JSON.stringify(detail))
        }
      }
    }
  }
  assert.deepEqual(buildArchiveApprovalEvents(null), legacyApprovalEvents(null))
})

test('等价性：历史版本行在全部版本组合上与重构前一致', () => {
  const versions = [
    { id: 'v1', versionNo: '1', monthPlanId: 'p', monthPlanItemId: 't', submittedAt: '2026-11-20T08:00:00', status: 'submitted', leaderSuggestion: '' },
    { id: 'v2', versionNo: '2', monthPlanId: 'p', monthPlanItemId: 't', submittedAt: '2026-11-24T09:00:00', status: 'rejected', leaderSuggestion: '不行' },
    { id: 'v3', versionNo: '3', monthPlanId: 'p', monthPlanItemId: 't', submittedAt: '2026-11-24T09:00:00', status: 'confirmed', leaderSuggestion: '' },
    { id: 'v4', versionNo: '4', monthPlanId: 'p', monthPlanItemId: 't', submittedAt: '2026-11-22T08:00:00', status: 'draft', leaderSuggestion: '' },
    { id: 'v5', versionNo: '5', monthPlanId: 'p', monthPlanItemId: 'other', submittedAt: '2026-11-24T10:00:00', status: 'submitted', leaderSuggestion: '' },
    { id: 'v6', versionNo: '6', monthPlanId: 'other', monthPlanItemId: 't', submittedAt: '2026-11-24T10:00:00', status: 'submitted', leaderSuggestion: '' },
    { id: 'v7', versionNo: '7', monthPlanId: 'p', monthPlanItemId: 't', submittedAt: '2026-11-24T10:00:00', status: 'not_submitted', leaderSuggestion: '' },
  ]
  for (const latestVersionId of [undefined, 'v1', 'v3', 'missing']) {
    assert.deepEqual(
      buildArchiveVersionRows(versions, { planId: 'p', taskId: 't', latestVersionId }),
      legacyVersionRows(versions, 'p', 't', latestVersionId),
      `latestVersionId=${latestVersionId}`,
    )
  }
})

test('等价性：数值与文件大小格式化在全部输入上与重构前一致', () => {
  const values = [0, 1, 1234.5, 1234.567, -3, 99999999.99, undefined, null as unknown as number]
  for (const value of values) {
    assert.equal(formatArchiveNumber(value), legacyNumberText(value), `value=${value}`)
    assert.equal(formatArchiveMoney(value), legacyMoneyText(value), `value=${value}`)
    assert.equal(formatArchiveOptionalMoney(value), legacyOptionalMoneyText(value), `value=${value}`)
    assert.equal(formatArchiveFileSize(value), legacyFileSize(value), `value=${value}`)
  }
  for (const bytes of [1, 1023, 1024, 1025, 1048575, 1048576, 1048577, 3 * 1024 * 1024]) {
    assert.equal(formatArchiveFileSize(bytes), legacyFileSize(bytes), `bytes=${bytes}`)
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. ⭐ AI 对话等价性
// ═══════════════════════════════════════════════════════════════════════════

test('等价性：角色标签在全部角色上与重构前一致', () => {
  for (const role of ROLES) {
    assert.equal(resolveAiMessageRoleLabel(role), legacyRoleLabel(role), `role=${role}`)
  }
})

test('等价性：操作码标签在全部操作码上与重构前一致', () => {
  for (const code of OPERATION_CODES) {
    assert.equal(resolveAiOperationLabel(code), legacyOperationLabel(code), `code=${code}`)
  }
})

test('等价性：消息正文在「角色 × 操作码 × 正文」矩阵上与重构前一致', () => {
  const texts = ['', '   ', '正文', 'PREVIEW', 'MONTH_PLAN_DRAFT']
  for (const role of ROLES) {
    for (const operationCode of OPERATION_CODES) {
      for (const textContent of texts) {
        const message: AiConversationMessageLike = { role, operationCode, textContent }
        assert.equal(resolveAiMessageBody(message), legacyMessageBody(message), JSON.stringify(message))
      }
    }
  }
})

test('等价性：紧凑时间在全部输入上与重构前一致', () => {
  const values = ['', '08:30', '2026-11-24', '2026-11-24T08:30:15', '2026-11-24 08:30', undefined]
  for (const value of values) {
    assert.equal(formatAiCompactTime(value), legacyCompactTime(value), `value=${value}`)
  }
})

test('等价性：结果摘要在全部载荷形状上与重构前一致', () => {
  const payloads: (AiConversationMessageLike['payload'] | null)[] = [
    null,
    undefined,
    {},
    { items: [] },
    { items: [{ content: 'A', deliverable: 'D', quality: 'Q' }] },
    { items: [{ content: '' }] },
    { items: [{}] },
    { issues: [] },
    { issues: [1, 2, 3] },
    { item: { id: 'x' } },
    { content: '文本' },
    { items: [{ content: 'A' }], issues: [1] },
  ]
  for (const payload of payloads) {
    for (const textContent of ['', '兜底正文']) {
      const message: AiConversationMessageLike = { role: 'ASSISTANT', payload, textContent }
      assert.equal(buildAiResultSummary(message), legacyResultSummary(message), JSON.stringify({ payload, textContent }))
    }
  }
})

test('等价性：可预览判定在「角色 × 载荷」矩阵上与重构前一致', () => {
  const payloads: (AiConversationMessageLike['payload'] | null)[] = [
    null,
    undefined,
    {},
    { items: [] },
    { items: [{}] },
    { content: 'x' },
    { content: 123 as unknown as string },
    { issues: [1] },
  ]
  for (const role of ROLES) {
    for (const payload of payloads) {
      const message: AiConversationMessageLike = { role, payload }
      assert.equal(canPreviewAiResult(message), legacyCanPreview(message), JSON.stringify({ role, payload }))
    }
  }
})

test('等价性：消息可见性与折叠计数在全部长度上与重构前一致', () => {
  for (let length = 0; length <= 12; length += 1) {
    const messages: AiConversationMessageLike[] = Array.from({ length }, (_, index) => ({
      role: 'ASSISTANT',
      textContent: String(index),
    }))
    assert.deepEqual(selectVisibleAiMessages(messages), legacyVisibleMessages(messages), `length=${length}`)
    assert.equal(countHiddenAiMessages(messages), legacyHiddenCount(messages), `length=${length}`)
  }
})

test('等价性：周期标签在全部计划类型上与重构前一致', () => {
  for (const planType of ['MONTH', 'WEEK', 'DAY', 'OTHER', '']) {
    for (const periodKey of ['2026-11', '2026-11-24', '']) {
      assert.equal(buildAiPeriodLabel(planType, periodKey), legacyPeriodLabel(planType, periodKey))
    }
  }
})

test('等价性：只读态判定在全部会话状态上与重构前一致', () => {
  const conversations = [undefined, {}, { status: 'ENDED' }, { status: 'ACTIVE' }, { status: '' }]
  for (const conversation of conversations) {
    assert.equal(isAiConversationReadonly(conversation), legacyReadonly(conversation), JSON.stringify(conversation))
  }
})
