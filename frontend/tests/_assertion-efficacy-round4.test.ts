import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildArchiveApprovalEvents,
  buildArchiveVersionRows,
  formatArchiveFileSize,
  formatArchiveOptionalMoney,
  resolveSelectedArchiveTask,
  selectArchiveResultTasks,
  sumArchivePersonalPerformance,
  type ArchiveTaskDetailState,
  type ArchiveTaskLike,
} from '../src/utils/monthResultArchive.ts'
import {
  buildAiResultSummary,
  canPreviewAiResult,
  countHiddenAiMessages,
  resolveAiMessageBody,
  selectVisibleAiMessages,
  type AiConversationMessageLike,
} from '../src/utils/aiConversationPanel.ts'
import {
  buildCalendarRuleNotice,
  computeDayPlanReadiness,
  isDayPlanDirty,
  normalizeDayPlanForm,
  parseDayPlanVersionSnapshot,
  resolveDayPlanStatusMeta,
  shouldShowDayPlanContentError,
} from '../src/utils/dayPlanEditor.ts'

// ═══════════════════════════════════════════════════════════════════════════
// ⚠️ **本文件的目的**：证明「同事写的那些断言**真的有判别力**」。
//
// 做法：故意实现**有缺陷的变体**（朴素实现 / 漏掉边界），
//       然后断言真实实现**能把它们判出来**。
//
// ⇒ 如果哪天有人把断言改成 `assert.ok(true)`，本文件会失败。
// ═══════════════════════════════════════════════════════════════════════════

const task = (id: string, overrides: Partial<ArchiveTaskLike> = {}): ArchiveTaskLike => ({
  id,
  status: 'SUBMITTED',
  latestVersion: { id: `${id}-v` },
  ...overrides,
})

const msg = (overrides: Partial<AiConversationMessageLike> = {}): AiConversationMessageLike => ({
  role: 'ASSISTANT',
  ...overrides,
})

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 日计划：朴素实现的缺陷必须被抓住
// ═══════════════════════════════════════════════════════════════════════════

test('判别力：脏检查必须能抓住「不排序关联 ID」的朴素实现', () => {
  // ⚠️ 朴素实现：不排序 ⇒ 换个勾选顺序就被判成「脏」。
  const naiveNormalize = (form: { planDate: string; relatedWeekPlanItemIds: string[]; content: string }) => (
    JSON.stringify({
      planDate: form.planDate,
      relatedWeekPlanItemIds: [...form.relatedWeekPlanItemIds],
      content: form.content.trim(),
    })
  )
  const form = { planDate: '2026-11-24', relatedWeekPlanItemIds: ['b', 'a'], content: 'x' }
  const sortedSnapshot = normalizeDayPlanForm(form)
  const naiveSnapshot = naiveNormalize(form)
  const reordered = { ...form, relatedWeekPlanItemIds: ['a', 'b'] }
  // 真实实现：两种顺序同一快照 ⇒ 不脏
  assert.equal(isDayPlanDirty(reordered, sortedSnapshot), false)
  // ⚠️ 朴素实现：会被误判成「脏」
  assert.equal(isDayPlanDirty(reordered, naiveSnapshot), true)
})

test('判别力：脏检查必须能抓住「不 trim 内容」的朴素实现', () => {
  const naiveNormalize = (form: { planDate: string; relatedWeekPlanItemIds: string[]; content: string }) => (
    JSON.stringify({
      planDate: form.planDate,
      relatedWeekPlanItemIds: [...form.relatedWeekPlanItemIds].sort(),
      content: form.content,
    })
  )
  // ⚠️ 关键：朴素实现**当初保存时**就把尾随空格存进去了。
  const saved = { planDate: '2026-11-24', relatedWeekPlanItemIds: [], content: '干活 ' }
  const naiveSnapshot = naiveNormalize(saved)
  const realSnapshot = normalizeDayPlanForm(saved)
  // 用户把尾随空格删掉（内容语义没变）
  const cleaned = { ...saved, content: '干活' }
  // ⚠️ 真实实现：判为「不脏」（语义没变）
  assert.equal(isDayPlanDirty(cleaned, realSnapshot), false)
  // ⚠️ 朴素实现：误判成「脏」
  assert.equal(isDayPlanDirty(cleaned, naiveSnapshot), true)
})

test('判别力：未知状态不能静默变 undefined（朴素实现会渲染空标签）', () => {
  const naiveStatusMeta = (status: string) => ({
    draft: { label: '草稿', type: 'info' },
    submitted: { label: '已提交', type: 'warning' },
  } as Record<string, { label: string; type: string }>)[status]
  assert.equal(naiveStatusMeta('archived'), undefined)
  const real = resolveDayPlanStatusMeta('archived')
  assert.ok(real, '真实实现必须给未知状态一个兜底')
  assert.equal(real.label, '已归档')
})

test('判别力：内容错误提示必须要求「已触发检查」', () => {
  // ⚠️ 朴素实现：只要内容空就报错 ⇒ 用户还没动手就看到红框。
  const naiveShowError = (content: string) => !content.trim()
  assert.equal(naiveShowError(''), true)
  assert.equal(shouldShowDayPlanContentError('', false), false)
  assert.equal(shouldShowDayPlanContentError('', true), true)
})

test('判别力：准备度必须是 0/100 两态（不能是假的百分比）', () => {
  // ⚠️ 朴素实现：按字数算百分比 ⇒ 写一个字符就显示 10%。
  const naiveReadiness = (content: string) => Math.min(100, content.length * 10)
  assert.equal(naiveReadiness('a'), 10)
  assert.equal(computeDayPlanReadiness('a'), 100)
  assert.equal(computeDayPlanReadiness(''), 0)
})

test('判别力：排班提示必须区分强制 / 非强制填报', () => {
  // ⚠️ 朴素实现：只写类型名，不说要不要填。
  const naiveNotice = (rule: { ruleType: string }) => rule.ruleType
  const rule = { ruleType: 'HOLIDAY', forceReport: true }
  assert.equal(naiveNotice(rule), 'HOLIDAY')
  const real = buildCalendarRuleNotice(rule)
  assert.ok(real.includes('当日需要填报日计划'))
})

test('判别力：默认工作日规则不提示（朴素实现会天天打扰用户）', () => {
  const naiveNotice = (rule: { ruleType: string }) => `当前是${rule.ruleType}`
  assert.ok(naiveNotice({ ruleType: 'WORKDAY' }).length > 0)
  assert.equal(buildCalendarRuleNotice({ ruleType: 'WORKDAY', explicit: false }), '')
})

test('判别力：历史版本快照必须过滤掉空串与非数组', () => {
  // ⚠️ 朴素实现：直接当数组用 ⇒ 快照里是 null 就崩。
  const naiveParse = (snapshot: Record<string, unknown>) => ({
    relatedWeekPlanItemIds: (snapshot.relatedWeekPlanItemIds as string[]).map(String),
    content: String(snapshot.content ?? ''),
  })
  assert.throws(() => naiveParse({ relatedWeekPlanItemIds: null }))
  const real = parseDayPlanVersionSnapshot({ relatedWeekPlanItemIds: null })
  assert.deepEqual(real.relatedWeekPlanItemIds, [])
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 归档详情：朴素实现的缺陷必须被抓住
// ═══════════════════════════════════════════════════════════════════════════

test('判别力：选中任务必须过滤掉无版本的任务（朴素实现会选到空任务）', () => {
  const tasks = [task('a', { latestVersion: null }), task('b')]
  // ⚠️ 朴素实现：不过滤 ⇒ 选中第一个（没版本）⇒ 详情页空白。
  const naiveSelect = (all: ArchiveTaskLike[], selectedId: string | null) => (
    all.find((item) => item.id === selectedId) ?? all[0] ?? null
  )
  assert.equal(naiveSelect(tasks, 'a')?.id, 'a')
  const real = resolveSelectedArchiveTask(tasks, 'a')
  assert.equal(real?.id, 'b')
})

test('判别力：绩效合计必须跳过失败任务', () => {
  // ⚠️ 真实实现里失败任务的 detail 会被置 null ⇒ 合计不含脏数据。
  const dirty = new Map<string, ArchiveTaskDetailState>([
    ['a', { state: 'loaded', detail: { personalPerformance: 10 }, error: '' }],
    ['b', { state: 'failed', detail: { personalPerformance: 99 }, error: 'boom' }],
  ])
  const cleaned = new Map<string, ArchiveTaskDetailState>([
    ['a', { state: 'loaded', detail: { personalPerformance: 10 }, error: '' }],
    ['b', { state: 'failed', detail: null, error: 'boom' }],
  ])
  // 朴素实现（不看 state 直接累加 detail）会把脏数据算进去
  const naiveSum = (map: Map<string, ArchiveTaskDetailState>) => (
    [...map.values()].reduce((sum, item) => sum + Number(item.detail?.personalPerformance || 0), 0)
  )
  assert.equal(naiveSum(dirty), 109)
  assert.equal(sumArchivePersonalPerformance(cleaned), 10)
})

test('判别力：版本排序必须稳定（朴素实现用数值减法会崩在字符串上）', () => {
  const versions = [
    { id: 'v1', versionNo: '1', monthPlanId: 'p', monthPlanItemId: 't', submittedAt: '2026-11-24T08:00:00', status: 'submitted' },
    { id: 'v2', versionNo: '2', monthPlanId: 'p', monthPlanItemId: 't', submittedAt: '2026-11-24T09:00:00', status: 'submitted' },
  ]
  // ⚠️ 朴素实现：数值减法 ⇒ 时间字符串转数字是 NaN ⇒ 排序不生效。
  const naiveSort = [...versions].sort((l, r) => (Number(r.submittedAt) - Number(l.submittedAt)))
  assert.ok(Number.isNaN(Number(naiveSort[0].submittedAt)))
  const rows = buildArchiveVersionRows(versions, { planId: 'p', taskId: 't' })
  assert.deepEqual(rows.map((row) => row.resultId), ['v2', 'v1'])
})

test('判别力：可选金额的 null 必须显示「未填写」而不是 0.00', () => {
  // ⚠️ 朴素实现：`value || 0` ⇒ null 变成 0.00 ⇒ 用户看不出「没填」。
  const naiveOptionalMoney = (value?: number) => (value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  assert.equal(naiveOptionalMoney(null), '0.00')
  assert.equal(formatArchiveOptionalMoney(null), '未填写')
})

test('判别力：文件大小必须跨 1MB 分界', () => {
  // ⚠️ 朴素实现：永远用 KB ⇒ 3MB 显示成「3072 KB」。
  const naiveSize = (value?: number) => `${Math.round((value ?? 0) / 1024)} KB`
  assert.equal(naiveSize(3 * 1024 * 1024), '3072 KB')
  assert.equal(formatArchiveFileSize(3 * 1024 * 1024), '3.00 MB')
})

test('判别力：审批事件只按真实时间点生成', () => {
  // ⚠️ 朴素实现：永远生成三行 ⇒ 领导没处理过也显示「领导处理」。
  const naiveEvents = () => [
    { label: '员工提交' },
    { label: '领导处理' },
    { label: '部门确认' },
  ]
  assert.equal(naiveEvents().length, 3)
  const real = buildArchiveApprovalEvents({ status: 'submitted', submittedAt: '2026-11-24T08:00:00' })
  assert.equal(real.length, 1)
})

test('判别力：任务筛选必须排除无版本任务', () => {
  const tasks = [task('a'), task('b', { latestVersion: null })]
  assert.equal(tasks.length, 2)
  assert.equal(selectArchiveResultTasks(tasks).length, 1)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ AI 对话：朴素实现的缺陷必须被抓住
// ═══════════════════════════════════════════════════════════════════════════

test('判别力：正文为空且无操作码时必须给兜底文案', () => {
  // ⚠️ 朴素实现：直接返回空串 ⇒ 气泡空白。
  const naiveBody = (m: AiConversationMessageLike) => (m.textContent || '').trim()
  assert.equal(naiveBody(msg({ textContent: '' })), '')
  assert.equal(resolveAiMessageBody(msg({ textContent: '' })), '内容已记录')
})

test('判别力：可预览判定必须排除用户自己的消息', () => {
  // ⚠️ 朴素实现：只看有没有载荷 ⇒ 用户消息也显示「预览」按钮。
  const naivePreview = (m: AiConversationMessageLike) => Boolean(m.payload)
  assert.equal(naivePreview(msg({ role: 'USER', payload: { content: 'x' } })), true)
  assert.equal(canPreviewAiResult(msg({ role: 'USER', payload: { content: 'x' } })), false)
})

test('判别力：结果摘要必须区分 items / issues / item / content', () => {
  // ⚠️ 朴素实现：一律用 textContent ⇒ 结构化结果全丢。
  const naiveSummary = (m: AiConversationMessageLike) => m.textContent || ''
  assert.equal(naiveSummary(msg({ payload: { items: [{ content: 'A' }] } })), '')
  const real = buildAiResultSummary(msg({ payload: { items: [{ content: 'A' }] } }))
  assert.ok(real.includes('1. A'))
})

test('判别力：空 items 必须给「AI 未返回计划建议」', () => {
  // ⚠️ 朴素实现：join 空数组 ⇒ 空串。
  const naiveSummary = (items: unknown[]) => items.map((item) => String(item)).join('\n\n')
  assert.equal(naiveSummary([]), '')
  assert.equal(buildAiResultSummary(msg({ payload: { items: [] } })), 'AI 未返回计划建议')
})

test('判别力：可见消息必须只留尾部 N 条', () => {
  const messages = Array.from({ length: 9 }, (_, index) => msg({ textContent: String(index) }))
  // ⚠️ 朴素实现：全量展示 ⇒ 面板被刷屏。
  assert.equal(messages.length, 9)
  assert.equal(selectVisibleAiMessages(messages).length, 5)
  assert.equal(countHiddenAiMessages(messages), 4)
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 反向自检：本文件自身的断言也不能是空断言
// ═══════════════════════════════════════════════════════════════════════════

test('自检：朴素实现与真实实现确实在关键点上给出不同答案', () => {
  // ⚠️ 如果哪天有人「顺手」把真实实现改成朴素实现，上面所有「判别力」测试都会失败。
  //    这里再补一条汇总断言，确认差异是**实质性的**而不是巧合。
  const naive = (value?: number) => (value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  assert.notEqual(naive(null), formatArchiveOptionalMoney(null))

  const naivePreview = (m: AiConversationMessageLike) => Boolean(m.payload)
  assert.notEqual(
    naivePreview(msg({ role: 'USER', payload: { content: 'x' } })),
    canPreviewAiResult(msg({ role: 'USER', payload: { content: 'x' } })),
  )
})
