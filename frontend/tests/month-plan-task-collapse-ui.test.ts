import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (relativePath: string) => readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')
const editor = read('../src/views/employee/EmployeeMonthPlanEdit.vue')
const taskCard = read('../src/views/employee/components/MonthPlanTaskEditorCard.vue')

test('month plan task cards expose controlled accessible collapse behavior', () => {
  assert.match(taskCard, /expanded:\s*boolean/)
  assert.match(taskCard, /taskKey:\s*string/)
  assert.match(taskCard, /'toggle-expanded':\s*\[\]/)
  assert.match(taskCard, /:aria-expanded="expanded"/)
  assert.match(taskCard, /:aria-controls="bodyId"/)
  assert.match(taskCard, /v-show="expanded"/)
})

test('month plan editor owns per-task expansion without bulk controls', () => {
  assert.match(editor, /const expandedTaskKeys = reactive\(new Set<string>\(\)\)/)
  assert.match(editor, /:key="item\.uiKey"/)
  assert.match(editor, /:expanded="expandedTaskKeys\.has\(item\.uiKey\)"/)
  assert.match(editor, /@toggle-expanded="toggleTaskExpanded\(item\.uiKey\)"/)
  assert.doesNotMatch(editor, /全部展开|全部收起/)
  assert.doesNotMatch(editor, /expandAllTasks|collapseAllTasks/)
  assert.doesNotMatch(editor, /ensureCardExpansion/)
  assert.match(editor, /expandedTaskKeys\.clear\(\)[\s\S]*autoSaveStatus\.value/)
})

test('new, duplicated, and validation-target tasks expand before focus', () => {
  assert.match(editor, /void expandAndLocateTask\(form\.items\.length - 1, 'taskContent'\)/)
  assert.match(editor, /void expandAndLocateTask\(index \+ 1, 'taskContent'\)/)
  assert.match(editor, /expandedTaskKeys\.add\(targetItem\.uiKey\)/)
  assert.match(editor, /await nextTick\(\)/)
})

test('UI-only task keys are omitted from version and AI payloads', () => {
  assert.match(editor, /\{ id: _id, uiKey: _uiKey, \.\.\.rawItem \}/)
  assert.match(editor, /const item = toAiPlanItem\(\{ \.\.\.rawItem, taskName: taskNameForSave\(sourceSnapshot\) \}\)/)
  assert.match(editor, /\{ id: _itemId, uiKey: _uiKey, \.\.\.currentItem \}/)
  assert.doesNotMatch(editor, /uiKey:\s*item\.uiKey/)
})
