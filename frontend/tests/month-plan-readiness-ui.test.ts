import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const editorPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanEdit.vue', import.meta.url))
const taskCardPath = fileURLToPath(new URL('../src/views/employee/components/MonthPlanTaskEditorCard.vue', import.meta.url))
const editorUtilsPath = fileURLToPath(new URL('../src/utils/monthPlanEditor.ts', import.meta.url))
const editor = readFileSync(editorPath, 'utf8')
const taskCard = readFileSync(taskCardPath, 'utf8')
const editorUtils = readFileSync(editorUtilsPath, 'utf8')

/**
 * ⚠️ **CI-5 阶段 2 第三轮**：月计划编辑器的纯逻辑已抽到 ``src/utils/monthPlanEditor.ts``。
 *
 * ⇒ 这里原本断言「准备度逻辑写在组件里」的静态契约，
 *   现在改为断言「准备度逻辑写在工具模块里、且组件确实调用了它」。
 *
 * ⚠️ **为什么要保留静态契约测试**
 *   行为测试（``month-plan-editor-utils.test.ts``）证明**算得对**；
 *   这里的静态契约证明**算的地方没跑偏**（防止有人把公式改回组件里另写一份）。
 */

test('month plan readiness is based on completion instead of issue count', () => {
  // ⚠️ 准备度公式现在住在工具模块里。
  assert.match(editorUtils, /if \(!items\.length\) return 0/)
  assert.match(editorUtils, /const itemFieldCount = items\.length \* READINESS_FIELDS_PER_ITEM/)
  assert.match(editorUtils, /READINESS_FIELDS_PER_ITEM = 7/)
  assert.match(editorUtils, /String\(item\.quality \|\| ''\)\.trim\(\)/)
  assert.match(editorUtils, /Number\(item\.estimatedValue\) >= 0/)
  assert.match(editorUtils, /const planScore = planMonthReady \? PLAN_MONTH_SCORE : 0/)
  assert.match(editorUtils, /PLAN_MONTH_SCORE = 10/)
  assert.match(editorUtils, /completedItemFields \/ itemFieldCount\) \* ITEM_FIELD_SCORE/)
  assert.match(editorUtils, /ITEM_FIELD_SCORE = 65/)
  assert.doesNotMatch(editorUtils, /summaryReady|form\.summary/)
  assert.match(editorUtils, /const weightDistance = Math\.min\(Math\.abs\(weightTotal - REQUIRED_WEIGHT_TOTAL\), 100\)/)
  assert.doesNotMatch(editorUtils, /validationIssues\.value\.length \* 9/)

  // ⚠️ 组件必须**调用**工具模块，而不是自己另写一份。
  assert.match(editor, /computeMonthPlanReadiness\(form\.planMonth, form\.items, currentMonth\(\)\)/)
  assert.doesNotMatch(editor, /const itemFieldCount = form\.items\.length \* 7/)
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

test('month plan validation rules live in the extracted util module', () => {
  // ⚠️ 校验规则同理：组件只做转发。
  assert.match(editor, /collectMonthPlanIssues\(form\.planMonth, form\.items\)/)
  assert.doesNotMatch(editor, /const requiredFields: Array<\[keyof EditableMonthPlanItem, string\]>/)

  assert.match(editorUtils, /REQUIRED_WEIGHT_TOTAL = 100/)
  assert.match(editorUtils, /MAX_ESTIMATED_VALUE = 9999999999\.99/)
  assert.match(editorUtils, /const weight = Number\(item\.performanceWeight\)/)
  assert.match(editorUtils, /if \(!weight \|\| weight <= 0 \|\| weight > 100\)/)
  assert.match(editorUtils, /item\.estimatedStartDate > item\.estimatedEndDate/)
})

test('AI optimize preview comparison is delegated to the extracted util module', () => {
  assert.match(editor, /buildOptimizePreview\(sourceSnapshot, normalizedItem\)/)
  assert.match(editor, /sameOptimizableValues\(row, sourceSnapshot\)/)
  assert.match(editor, /defaultSelectedOptimizeFields\(previewFields\)/)
  assert.doesNotMatch(editor, /const optimizableFields: Array<\{ key: OptimizableField; label: string \}>/)
})
