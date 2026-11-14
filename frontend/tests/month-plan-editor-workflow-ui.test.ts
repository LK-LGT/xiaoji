import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
const editor = read('../src/views/employee/EmployeeMonthPlanEdit.vue')
const card = read('../src/views/employee/components/MonthPlanTaskEditorCard.vue')

test('month editor has one task-focused workflow without a plan summary', () => {
  assert.doesNotMatch(editor, /计划基础信息|month-plan-identity__head|month-plan-identity__meta/)
  assert.doesNotMatch(editor, /label="计划摘要"|form\.summary|summaryIssue|请填写计划摘要/)
  assert.match(editor, /class="task-editor-toolbar"/)
  assert.match(editor, /class="validation-aside__actions"/)
  assert.equal((editor.match(/@click="submitApproval"/g) || []).length, 1)
  assert.equal((editor.match(/@click="saveAndReturn"/g) || []).length, 1)
})

test('editor task card exposes the monthly task rail and icon action labels', () => {
  assert.match(card, /month-task-rail/)
  assert.match(card, /aria-label="AI 优化当前任务"/)
  assert.match(card, /aria-label="复制当前任务"/)
  assert.match(card, /aria-label="删除当前任务"/)
  assert.match(card, /prefers-reduced-motion/)
})

test('month editor keeps a responsive fixed submission bar', () => {
  assert.match(editor, /@media \(max-width:\s*980px\)/)
  assert.match(editor, /\.editor-workbench\s*\{\s*grid-template-columns:\s*1fr;/)
  assert.match(editor, /\.validation-aside \{ position:fixed;/)
  assert.match(editor, /\.month-plan-editor-page \{[^}]*padding-bottom:112px;/)
  assert.match(editor, /@media \(max-width: 720px\)[\s\S]*\.validation-aside \{ right:16px; left:16px; grid-template-columns:auto minmax\(0,1fr\) auto;/)
})

test('month editor submission bar is compact and has no nested metric card', () => {
  assert.match(editor, /<aside class="validation-aside" aria-label="月计划提交操作">/)
  assert.match(editor, /class="submit-save-state"/)
  assert.match(editor, /class="submit-readiness"/)
  assert.match(editor, /class="submit-metrics"/)
  assert.match(editor, /class="submit-issue-link"[\s\S]*assistDrawerVisible = true/)
  assert.doesNotMatch(editor, /class="aside-metrics"|class="aside-heading"/)
  assert.match(editor, /\.validation-aside \{[^}]*border-top:1px solid #cbd7d1;[^}]*backdrop-filter:blur\(14px\);/)
  assert.doesNotMatch(editor, /\.validation-aside \{[^}]*border-radius:/)
})

test('month plan tasks provide table and grouped card views over the same items', () => {
  assert.match(editor, /v-model="taskViewMode"/)
  assert.match(editor, /<el-radio-button value="table">/)
  assert.match(editor, /<el-radio-button value="cards">/)
  assert.match(editor, /form\.items\.length && taskViewMode === 'table'/)
  assert.match(editor, /v-else-if="form\.items\.length" class="task-editor-list"/)
  assert.match(editor, /<MonthPlanTaskEditorCard/)
  assert.match(editor, /v-for="\(item, index\) in form\.items"/)
})

test('table view uses compact grouped rows without nested table scrolling', () => {
  assert.match(editor, /class="compact-task-table" role="table"/)
  assert.match(editor, /class="compact-task-table__head" role="row"/)
  assert.match(editor, /class="compact-task-row" role="row"/)
  assert.match(editor, /序号<\/span><span>工作计划内容<\/span><span>权重（%）<\/span><span>考核标准（交付物、质量）<\/span><span>完成时间段<\/span><span>预估价值<\/span><span>操作/)
  assert.match(editor, /compact-task-cell--weight/)
  assert.match(editor, /compact-task-cell--standard/)
  assert.match(editor, /compact-task-cell--period/)
  assert.match(editor, /compact-task-cell--value/)
  assert.match(editor, /\.month-plan-editor-page \{[^}]*overflow-x:hidden/)
  assert.match(editor, /--task-columns:/)
  assert.match(editor, /@media \(max-width: 1180px\)[\s\S]*\.compact-task-row \{ grid-template-columns:/)
  assert.doesNotMatch(editor, /<el-table|scrollbar-always-on|fixed="right"/)
  assert.match(editor, /\.compact-task-table :deep\(\.el-input__wrapper\),\.compact-task-table :deep\(\.el-textarea__inner\)[^}]*border-radius:0; background:transparent; box-shadow:none;/)
  assert.match(editor, /\.compact-task-cell:focus-within \{ background:rgb\(67 136 120 \/ 5%\);/)
  assert.match(editor, /\.compact-task-cell:has\(\.sheet-field\.has-error\) \{ background:rgb\(198 83 70 \/ 4%\); box-shadow:inset 0 0 0 1px #c65346;/)
  assert.doesNotMatch(editor, /\.sheet-field:focus-within\s*\{/)
  assert.doesNotMatch(editor, /\.sheet-field\.has-error\s*\{/)
})

test('task view preference persists and defaults to cards on narrow screens', () => {
  assert.match(editor, /localStorage\.getItem\('month-plan-task-view'\)/)
  assert.match(editor, /localStorage\.setItem\('month-plan-task-view', mode\)/)
  assert.match(editor, /matchMedia\('\(max-width: 980px\)'\)\.matches \? 'cards' : 'table'/)
  assert.doesNotMatch(editor, /ensureCardExpansion|expandAllTasks|collapseAllTasks/)
})

test('task name is hidden from editing and filled only for backend compatibility', () => {
  assert.doesNotMatch(editor, /任务名称|v-model="row\.taskName"/)
  assert.doesNotMatch(card, /label="任务名称"|v-model="item\.taskName"/)
  assert.match(editor, /function taskNameForSave\(item:/)
  assert.match(editor, /if \(existing\) return existing/)
  assert.match(editor, /replace\(\/\\s\+\/g, ' '\)\.trim\(\)\.slice\(0, 30\)/)
  assert.match(editor, /taskName: taskNameForSave\(item\)/)
})

test('table rows are directly editable without a separate edit state', () => {
  assert.doesNotMatch(editor, /activeTableEditKey|toggleTableRowEdit|EditPen|完成编辑当前任务/)
  assert.match(editor, /class="compact-task-cell compact-task-cell--content"[\s\S]*v-model="row\.taskContent"/)
  assert.match(editor, /class="compact-task-cell compact-task-cell--weight"[\s\S]*v-model="row\.performanceWeight"/)
  assert.match(editor, /class="compact-task-cell compact-task-cell--standard"[\s\S]*v-model="row\.deliverable"[\s\S]*v-model="row\.quality"/)
  assert.match(editor, /class="compact-task-cell compact-task-cell--period"[\s\S]*class="period-separator">-<\/span>/)
  assert.match(editor, /\.compact-task-row \{ min-height:48px;/)
  assert.match(editor, /v-model="row\.taskContent" type="textarea" :autosize="\{ minRows: 1 \}"/)
  assert.match(editor, /v-model="row\.deliverable" type="textarea" :autosize="\{ minRows: 1 \}"/)
  assert.match(editor, /v-model="row\.quality" type="textarea" :autosize="\{ minRows: 1 \}"/)
  assert.doesNotMatch(editor, /v-model="row\.taskContent"[^>]*maxRows/)
  assert.doesNotMatch(editor, /v-model="row\.(?:taskContent|deliverable|quality)"[^>]*maxRows/)
  assert.match(editor, /\.compact-task-row \{ min-height:48px; align-items:stretch;/)
  assert.match(editor, /\.compact-task-cell--index \{ display:grid; place-items:center;/)
})

test('validation feedback appears only after a submit attempt', () => {
  assert.match(editor, /const validationFeedbackVisible = ref\(false\)/)
  assert.match(editor, /const visibleValidationIssues = computed\(\(\) => validationFeedbackVisible\.value \? validationIssues\.value : \[\]\)/)
  assert.match(editor, /function validateDraft\(showFeedback = false\)/)
  assert.match(editor, /if \(showFeedback\) validationFeedbackVisible\.value = true/)
  assert.match(editor, /submitApproval\(\)[\s\S]*validateDraft\(true\)/)
  assert.match(editor, /checkMonthPlanNow\(\)[\s\S]*validateDraft\(false\)/)
  assert.match(editor, /function taskFieldError[\s\S]*visibleValidationIssues\.value\.find/)
  assert.match(editor, /function issuesForItem[\s\S]*visibleValidationIssues\.value\.filter/)
  assert.doesNotMatch(editor, /:disabled="editingDisabled \|\| saving \|\| validationIssues\.length > 0"/)
})
