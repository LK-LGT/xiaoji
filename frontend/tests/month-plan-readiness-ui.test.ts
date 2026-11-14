import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const editorPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanEdit.vue', import.meta.url))
const taskCardPath = fileURLToPath(new URL('../src/views/employee/components/MonthPlanTaskEditorCard.vue', import.meta.url))
const editor = readFileSync(editorPath, 'utf8')
const taskCard = readFileSync(taskCardPath, 'utf8')

test('month plan readiness is based on completion instead of issue count', () => {
  assert.match(editor, /if \(!form\.items\.length\) return 0/)
  assert.match(editor, /const itemFieldCount = form\.items\.length \* 7/)
  assert.match(editor, /String\(item\.quality \|\| ''\)\.trim\(\)/)
  assert.match(editor, /Number\(item\.estimatedValue\) >= 0/)
  assert.match(editor, /const planScore = planMonthReady \? 10 : 0/)
  assert.match(editor, /completedItemFields \/ itemFieldCount\) \* 65/)
  assert.doesNotMatch(editor, /summaryReady|form\.summary/)
  assert.match(editor, /const weightDistance = Math\.min\(Math\.abs\(regularWeightTotal\.value - 100\), 100\)/)
  assert.doesNotMatch(editor, /validationIssues\.value\.length \* 9/)
})

test('new month plan tasks start empty and numeric values are normalized when saved', () => {
  assert.match(editor, /performanceWeight:\s*null,/)
  assert.match(editor, /estimatedValue:\s*null,/)
  assert.match(editor, /performanceWeight:\s*numberForSave\(item\.performanceWeight\)/)
  assert.match(editor, /estimatedValue:\s*numberForSave\(item\.estimatedValue\)/)
  assert.match(taskCard, /:min="0"/)
  assert.match(taskCard, /label="预估价值（元）"/)
  assert.doesNotMatch(taskCard, /:min="0\.01"/)
})
