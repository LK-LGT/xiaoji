import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const editorPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanEdit.vue', import.meta.url))
const editor = readFileSync(editorPath, 'utf8')

test('month plan AI assistant opens a dedicated workspace after saving the draft', () => {
  assert.match(editor, /class="ai-floating-entry"/)
  assert.match(editor, /:icon="Clock" @click="versionDrawerVisible = true">历史版本/)
  assert.match(editor, /function openAiWorkspace/)
  assert.match(editor, /await saveDraft\(false\)/)
  assert.match(editor, /router\.push\(aiPath\(form\.id\)\)/)
  assert.doesNotMatch(editor, />打开历史版本</)
  assert.doesNotMatch(editor, />打开 AI 助手</)
  assert.match(editor, /\.month-plan-editor-page \.ai-floating-entry\s*{[^}]*display:inline-flex;[^}]*bottom:var\(--month-submit-bar-clearance\);/)
  assert.doesNotMatch(editor, /MonthPlanAiAssistant/)
  assert.doesNotMatch(editor, /ai-assistant-dialog/)
})

test('applied AI changes return to and highlight the task editor', () => {
  assert.match(editor, /route\.query\.aiApplied !== '1'/)
  assert.match(editor, /is-ai-applied/)
  assert.match(editor, /scrollIntoView/)
})

test('month plan editor keeps the fixed submission bar within narrow viewports', () => {
  assert.match(editor, /@media \(max-width:\s*980px\)/)
  assert.match(editor, /\.editor-workbench\s*{\s*grid-template-columns:\s*1fr;/)
  assert.match(editor, /\.validation-aside\s*{\s*position:\s*fixed;/)
  assert.match(editor, /@media \(max-width: 720px\)[\s\S]*\.validation-aside { right:16px; left:16px;/)
})
