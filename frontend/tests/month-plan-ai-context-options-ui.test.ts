import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const editorPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanEdit.vue', import.meta.url))
const workspacePath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanAiWorkspace.vue', import.meta.url))
const contextPopoverPath = fileURLToPath(new URL('../src/views/employee/components/MonthPlanAiContextPopover.vue', import.meta.url))
const checkPanelPath = fileURLToPath(new URL('../src/views/employee/components/MonthPlanAiCheckPanel.vue', import.meta.url))
const apiPath = fileURLToPath(new URL('../src/api/employeeAi.ts', import.meta.url))
// ⚠️ **CI-5 阶段 2 第五轮**：展示行的纯逻辑已抽到 `@/utils/aiContextPopover`
const aiContextPopoverPath = fileURLToPath(new URL('../src/utils/aiContextPopover.ts', import.meta.url))
const editor = readFileSync(editorPath, 'utf8')
const workspace = readFileSync(workspacePath, 'utf8')
const contextPopover = readFileSync(contextPopoverPath, 'utf8')
const checkPanel = readFileSync(checkPanelPath, 'utf8')
const api = readFileSync(apiPath, 'utf8')
const aiContextPopover = readFileSync(aiContextPopoverPath, 'utf8')

test('month plan editor keeps default context for row optimization', () => {
  assert.match(editor, /selectedAiContextTypes\s*=\s*ref<MonthPlanContextType\[\]>\(\[\.\.\.MONTH_PLAN_CONTEXT_TYPES\]\)/)
  assert.match(editor, /selectedContextTypes:\s*\[\.\.\.selectedAiContextTypes\.value\]/)
  assert.match(editor, /conversationId:\s*aiConversationId\.value/)
})

test('workspace popover exposes compact reference options and sends a selection snapshot', () => {
  assert.match(contextPopover, /参考资料/)
  assert.match(contextPopover, /selectedCount.*MONTH_PLAN_CONTEXT_TYPES\.length/)
  assert.match(contextPopover, /historyMonthOptions/)
  assert.match(contextPopover, /knowledgeTypeOptions/)
  assert.match(contextPopover, /contextIndeterminate/)
  assert.match(contextPopover, /selectedContextTypes\.includes\('JOB_DESCRIPTION'\)/)
  assert.match(workspace, /selectedContextTypes:\s*\[\.\.\.selectedContextTypes\.value\]/)
  assert.match(workspace, /selectedHistoryMonths:\s*\[\.\.\.selectedHistoryMonths\.value\]/)
  assert.match(workspace, /selectedKnowledgeTypes:\s*\[\.\.\.selectedKnowledgeTypes\.value\]/)
})

test('reference options keep availability status and show available content summaries', () => {
  assert.match(api, /summary\?: string/)
  // ⚠️ **CI-5 阶段 2 第五轮**：`option.summary` / `option.available` 的渲染留在组件里，
  //    但「怎么算出来」的纯逻辑搬到了 `@/utils/aiContextPopover`。
  assert.match(contextPopover, /option\.summary/)
  assert.match(aiContextPopover, /type === 'JOB_DESCRIPTION'[\s\S]*?jobDescription\.trim\(\)/)
  assert.match(contextPopover, /option\.available \? '可用' : '暂无'/)
  assert.match(contextPopover, /暂无已审批计划/)
  assert.match(contextPopover, /暂无可引用文档/)
  assert.match(contextPopover, /class="context-summary"/)
  assert.match(contextPopover, /text-overflow:ellipsis/)
  assert.doesNotMatch(contextPopover, /未配置/)
})

test('workspace reference options stay readable without a cramped two-column layout', () => {
  assert.match(contextPopover, /width="min\(520px, calc\(100vw - 24px\)\)"/)
  assert.match(contextPopover, /grid-template-columns:minmax\(0,1fr\)/)
  assert.match(contextPopover, /white-space:normal/)
  assert.match(contextPopover, /overflow-wrap:anywhere/)
  assert.doesNotMatch(contextPopover, /grid-template-columns:1fr 1fr/)
})

test('workspace keeps a live reference ledger inside one composer surface', () => {
  assert.match(workspace, /class="composer-surface"/)
  assert.match(workspace, /class="composer-reference-ledger"/)
  assert.match(workspace, /参考 \$\{selectedContextTypes\.value\.length\} 项/)
  assert.match(workspace, /历史 \$\{selectedHistoryMonths\.value\.length\} 月/)
  assert.match(workspace, /知识 \$\{selectedKnowledgeTypes\.value\.length\} 类/)
  assert.match(contextPopover, /historyExpanded = ref\(true\)/)
  assert.match(contextPopover, /knowledgeExpanded = ref\(true\)/)
})

test('all three AI request contracts accept selected context types', () => {
  assert.match(api, /export type MonthPlanContextType/)
  assert.match(api, /MONTH_PLAN_CONTEXT_TYPES/)
  assert.equal((api.match(/selectedContextTypes\?: MonthPlanContextType\[\]/g) || []).length, 3)
  assert.equal((api.match(/selectedHistoryMonths\?: string\[\]/g) || []).length, 3)
  assert.equal((api.match(/selectedKnowledgeTypes\?: string\[\]/g) || []).length, 4)
  assert.match(checkPanel, /selectedContextTypes:\s*MonthPlanContextType\[\]/)
  assert.match(checkPanel, /selectedContextTypes:\s*\[\.\.\.props\.selectedContextTypes\]/)
  assert.match(checkPanel, /selectedHistoryMonths:\s*props\.selectedHistoryMonths/)
  assert.match(checkPanel, /selectedKnowledgeTypes:\s*props\.selectedKnowledgeTypes/)
  assert.match(checkPanel, /conversationId:\s*props\.conversationId/)
})
