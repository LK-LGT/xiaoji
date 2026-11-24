import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

// ═══════════════════════════════════════════════════════════════════════════
// ⚠️ **静态契约测试**（CI-5 阶段 2 第四轮）
//
// 目的：锁定「纯逻辑住在 `src/utils/*.ts`，组件只做转发」这个结构。
//       —— 这样后续任何人把逻辑搬回组件里，测试会立刻失败。
// ═══════════════════════════════════════════════════════════════════════════

const dayPlanEditor = await readFile(new URL('../src/utils/dayPlanEditor.ts', import.meta.url), 'utf8')
const monthResultArchive = await readFile(new URL('../src/utils/monthResultArchive.ts', import.meta.url), 'utf8')
const aiConversationPanel = await readFile(new URL('../src/utils/aiConversationPanel.ts', import.meta.url), 'utf8')

const dayPlanEditorView = await readFile(
  new URL('../src/views/employee/components/EmployeeDayPlanEditor.vue', import.meta.url),
  'utf8',
)
const archiveDetailView = await readFile(
  new URL('../src/views/employee/components/EmployeeMonthResultArchiveDetail.vue', import.meta.url),
  'utf8',
)
const aiConversationView = await readFile(
  new URL('../src/views/employee/components/PlanAiConversationPanel.vue', import.meta.url),
  'utf8',
)

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 日计划编辑器
// ═══════════════════════════════════════════════════════════════════════════

test('日计划纯逻辑住在 utils 里，组件只做转发', async () => {
  assert.match(dayPlanEditorView, /from '@\/utils\/dayPlanEditor'/)
  assert.match(dayPlanEditorView, /collectDayPlanIssues/)
  assert.match(dayPlanEditorView, /computeDayPlanReadiness/)
  assert.match(dayPlanEditorView, /isDayPlanDirty/)
  assert.match(dayPlanEditorView, /resolveDayPlanStatusMeta/)
  assert.match(dayPlanEditorView, /buildCalendarRuleNotice/)
  assert.match(dayPlanEditorView, /normalizeDayPlanForm/)
  assert.match(dayPlanEditorView, /parseDayPlanVersionSnapshot/)
  assert.match(dayPlanEditorView, /readDayPlanViewMode/)
  assert.match(dayPlanEditorView, /persistDayPlanViewMode/)
})

test('日计划组件里不再内联排班规则映射表', async () => {
  // ⚠️ 映射表只在 utils 里 ⇒ 组件里不应再出现这张表。
  assert.doesNotMatch(dayPlanEditorView, /SPECIAL_SHIFT: '特殊排班'/)
  assert.doesNotMatch(dayPlanEditorView, /BUSINESS_TRIP: '出差'/)
  assert.match(dayPlanEditor, /SPECIAL_SHIFT: '特殊排班'/)
})

test('日计划组件里不再内联状态映射表', async () => {
  assert.doesNotMatch(dayPlanEditorView, /canceled: \{ label: '已撤销'/)
  assert.doesNotMatch(dayPlanEditorView, /archived: \{ label: '已归档'/)
  assert.match(dayPlanEditor, /archived: \{ label: '已归档'/)
})

test('日计划组件不再手写快照序列化', async () => {
  assert.doesNotMatch(dayPlanEditorView, /JSON\.stringify\(\{\s*planDate:/)
  assert.match(dayPlanEditor, /JSON\.stringify\(\{/)
})

test('日计划组件不再手写星期数组', async () => {
  assert.doesNotMatch(dayPlanEditorView, /'星期日', '星期一'/)
  assert.match(dayPlanEditor, /'星期日', '星期一'/)
})

test('日计划视图模式存储键只在 utils 定义一次', async () => {
  assert.match(dayPlanEditor, /DAY_PLAN_VIEW_MODE_KEY = 'day-plan-task-view'/)
  assert.doesNotMatch(dayPlanEditorView, /'day-plan-task-view'/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 归档详情
// ═══════════════════════════════════════════════════════════════════════════

test('归档详情纯逻辑住在 utils 里，组件只做转发', async () => {
  assert.match(archiveDetailView, /from '@\/utils\/monthResultArchive'/)
  assert.match(archiveDetailView, /selectArchiveResultTasks/)
  assert.match(archiveDetailView, /resolveSelectedArchiveTask/)
  assert.match(archiveDetailView, /selectFailedArchiveTasks/)
  assert.match(archiveDetailView, /buildArchiveApprovalEvents/)
  assert.match(archiveDetailView, /buildArchiveVersionRows/)
  assert.match(archiveDetailView, /sumArchivePersonalPerformance/)
  assert.match(archiveDetailView, /formatArchiveFileSize/)
})

test('归档详情组件里不再内联任务状态映射表', async () => {
  assert.doesNotMatch(archiveDetailView, /NOT_SUBMITTED: \{ label: '未提交'/)
  assert.match(monthResultArchive, /NOT_SUBMITTED: \{ label: '未提交'/)
})

test('归档详情组件里不再内联文件大小换算', async () => {
  assert.doesNotMatch(archiveDetailView, /1024 \* 1024/)
  assert.match(monthResultArchive, /1024 \* 1024/)
})

test('归档详情组件不再内联审批事件构造', async () => {
  assert.doesNotMatch(archiveDetailView, /label: '直属领导', operatorName/)
  assert.match(monthResultArchive, /label: '领导处理'[\s\S]*?operatorName: '直属领导'/)
})

test('归档详情组件不再内联历史版本过滤条件', async () => {
  assert.doesNotMatch(archiveDetailView, /'draft', 'not_submitted'/)
  assert.match(monthResultArchive, /'draft', 'not_submitted'/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ AI 对话面板
// ═══════════════════════════════════════════════════════════════════════════

test('AI 对话纯逻辑住在 utils 里，组件只做转发', async () => {
  assert.match(aiConversationView, /from '@\/utils\/aiConversationPanel'/)
  assert.match(aiConversationView, /resolveAiMessageBody/)
  assert.match(aiConversationView, /resolveAiOperationLabel/)
  assert.match(aiConversationView, /resolveAiMessageRoleLabel/)
  assert.match(aiConversationView, /buildAiResultSummary/)
  assert.match(aiConversationView, /canPreviewAiResult/)
  assert.match(aiConversationView, /formatAiCompactTime/)
  assert.match(aiConversationView, /selectVisibleAiMessages/)
})

test('AI 对话组件里不再内联操作码映射表', async () => {
  assert.doesNotMatch(aiConversationView, /MONTH_PLAN_ITEM_OPTIMIZE: '优化计划事项'/)
  assert.doesNotMatch(aiConversationView, /ADOPT_WITH_EDIT: '修改后采纳'/)
  assert.match(aiConversationPanel, /MONTH_PLAN_ITEM_OPTIMIZE: '优化计划事项'/)
  assert.match(aiConversationPanel, /ADOPT_WITH_EDIT: '修改后采纳'/)
})

test('AI 对话组件里不再内联角色三元判断', async () => {
  assert.doesNotMatch(aiConversationView, /role === 'USER' \? '我' :/)
  assert.match(aiConversationPanel, /role === 'USER'\) return '我'/)
})

test('AI 对话组件里不再内联结果摘要分派', async () => {
  assert.doesNotMatch(aiConversationView, /共 \$\{payload\.issues\.length\} 条检查提示/)
  assert.match(aiConversationPanel, /条检查提示/)
})

test('AI 对话组件里不再内联可见消息切片', async () => {
  assert.doesNotMatch(aiConversationView, /messages\.value\.slice\(-5\)/)
  assert.match(aiConversationPanel, /AI_VISIBLE_MESSAGE_LIMIT = 5/)
})
