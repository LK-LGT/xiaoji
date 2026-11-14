import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

function source(path: string) {
  return readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
}

const conversation = source('../src/composables/usePlanAiConversation.ts')
const thread = source('../src/views/employee/components/PlanAiChatThread.vue')
const resultCard = source('../src/views/employee/components/MonthPlanAiResultCard.vue')
const contextPopover = source('../src/views/employee/components/MonthPlanAiContextPopover.vue')
const workspace = source('../src/views/employee/EmployeeMonthPlanAiWorkspace.vue')
const diff = source('../src/views/employee/components/MonthPlanAiDiff.vue')
const aiApi = source('../src/api/employeeAi.ts')
const router = source('../src/router/index.ts')

test('conversation state owns active history pagination and readonly lifecycle', () => {
  assert.match(conversation, /resolveAiConversation/)
  assert.match(conversation, /listAiConversations/)
  assert.match(conversation, /listAiConversationMessages/)
  assert.match(conversation, /startNewAiConversation/)
  assert.match(conversation, /scopeRequest/)
  assert.match(conversation, /messages\.value\s*=\s*\[\.\.\.page\.records,\s*\.\.\.messages\.value\]/)
  assert.match(conversation, /readonlyView/)
})

test('chat thread renders conversation roles without flow timeline visuals', () => {
  assert.match(thread, /message--user/)
  assert.match(thread, /message--assistant/)
  assert.match(thread, /message--event/)
  assert.match(thread, /starter-prompts/)
  assert.match(thread, /MonthPlanAiResultCard/)
  assert.doesNotMatch(thread, /message-timeline/)
  assert.doesNotMatch(thread, /message-node/)
  assert.doesNotMatch(thread, /structured JSON|结构化结果/)
})

test('result and context tools keep structured data outside the main chat flow', () => {
  assert.match(resultCard, /查看并应用/)
  assert.match(resultCard, /继续调整/)
  assert.match(resultCard, /draftVersionNo/)
  assert.match(contextPopover, /参考资料/)
  assert.match(contextPopover, /MONTH_PLAN_CONTEXT_TYPES/)
  assert.match(contextPopover, /今日剩余/)
})

test('context popover uses the button itself as its click reference', () => {
  assert.match(contextPopover, /<template #reference>\s*<el-button/)
  assert.doesNotMatch(contextPopover, /<template #reference>\s*<el-tooltip/)
})

test('month AI workspace is a dedicated route with history rail and sticky composer', () => {
  assert.match(router, /employee\/month-plans\/:id\/ai/)
  assert.match(router, /EmployeeMonthPlanAiWorkspace/)
  assert.match(workspace, /class="conversation-history-rail"/)
  assert.match(workspace, /class="history-actions"/)
  assert.match(workspace, /class="workspace-composer"/)
  assert.match(workspace, /class="composer-surface"/)
  assert.match(workspace, /class="composer-reference-ledger"/)
  assert.match(workspace, /position:\s*sticky/)
  assert.match(workspace, /MonthPlanAiDiff/)
  assert.match(workspace, /saveEmployeeMonthPlanDraftApi/)
  assert.doesNotMatch(workspace, /PlanAiConversationPanel/)
  assert.doesNotMatch(workspace, /<el-dialog/)
})

test('workspace starter commands use a compact stable grid', () => {
  assert.match(thread, /grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/)
  assert.match(thread, /border-radius:6px/)
  assert.match(thread, /@media \(max-width:680px\)[\s\S]*grid-template-columns:1fr/)
})

test('month AI workspace uses direct generation without the Agent workflow', () => {
  assert.match(workspace, /generateMonthPlanApi/)
  assert.match(workspace, /sendQuickGenerate/)
  assert.match(workspace, /MonthPlanAiDiff/)
  assert.match(workspace, /saveEmployeeMonthPlanDraftApi/)
  assert.doesNotMatch(workspace, /PlanAgent|monthPlanAgent|candidate-entry|待确认任务/)
  assert.doesNotMatch(workspace, /current-summary|applySummary|\.summary\s*\|\|/)
  assert.doesNotMatch(diff, /应用计划摘要|applySummary|currentSummary|suggestion\?\.summary/)
  assert.doesNotMatch(aiApi, /interface AiPlanForm \{\s*summary:|interface AiGenerateResponse \{[\s\S]*?\n\s*summary:/)
  assert.doesNotMatch(thread, /summary:\s*String\(payload\.summary/)
})
