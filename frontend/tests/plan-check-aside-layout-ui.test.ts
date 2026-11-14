import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const employeeRoot = new URL('../src/views/employee/', import.meta.url)
const weekEditor = readFileSync(fileURLToPath(new URL('EmployeeWeekPlanEdit.vue', employeeRoot)), 'utf8')
const dayEditor = readFileSync(fileURLToPath(new URL('components/EmployeeDayPlanEditor.vue', employeeRoot)), 'utf8')
const monthEditor = readFileSync(fileURLToPath(new URL('EmployeeMonthPlanEdit.vue', employeeRoot)), 'utf8')
const validationAside = readFileSync(fileURLToPath(new URL('components/PlanValidationAside.vue', employeeRoot)), 'utf8')
const aiReviewPanel = readFileSync(fileURLToPath(new URL('../../components/AiReviewPanel.vue', employeeRoot)), 'utf8')

test('shared plan validation aside can host AI plan checks', () => {
  assert.match(validationAside, /<slot name="ai-review"/)
  assert.match(validationAside, /plan-checklist__ai-review/)
})

test('week plan AI checks render inside the right validation aside', () => {
  assert.ok(weekEditor.indexOf('title="AI周计划检查"') > weekEditor.indexOf('<PlanValidationAside'))
  assert.doesNotMatch(weekEditor, /<AiReviewPanel\s+class="mt16"[\s\S]*title="AI周计划检查"/)
  assert.doesNotMatch(dayEditor, /AI日计划检查|AiReviewPanel|PlanAiReviewCell|PlanAiReviewDrawer/)
})

test('month plan AI check follows result submission aside placement', () => {
  assert.ok(monthEditor.indexOf('title="AI月计划检查"') > monthEditor.indexOf('<aside class="validation-aside"'))
  assert.match(monthEditor, /class="month-aside-ai-panel"/)
  assert.doesNotMatch(monthEditor, /<AiReviewPanel class="mt16"[\s\S]*title="AI月计划检查"/)
  assert.doesNotMatch(monthEditor, /type="success" plain :loading="aiChecking"/)
})

test('AI review labels use neutral copy and the dynamic model name', () => {
  assert.match(aiReviewPanel, /label: props\.review\.modelName \|\| 'AI模型'/)
  assert.match(aiReviewPanel, /label: capability\.value\.modelName \|\| 'AI模型'/)
  assert.match(aiReviewPanel, /'系统规则' : 'AI语义判断'/)
  assert.doesNotMatch(aiReviewPanel, /千问/)
})
