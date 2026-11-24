import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (relativePath: string) => readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')

const employeeApi = read('../src/api/employee.ts')
const employeeAiApi = read('../src/api/employeeAi.ts')
const editor = read('../src/views/employee/EmployeeMonthPlanEdit.vue')
const editorCard = read('../src/views/employee/components/MonthPlanTaskEditorCard.vue')
// ⚠️ CI-5 阶段 2 第三轮：必填字段清单已抽到工具模块。
const editorUtils = read('../src/utils/monthPlanEditor.ts')
const aiDiff = read('../src/views/employee/components/MonthPlanAiDiff.vue')
const approval = read('../src/views/department/MonthPlanApproval.vue')

test('month plan quality is editable, validated, and included in payloads', () => {
  assert.match(employeeApi, /quality:\s*string/)
  assert.match(employeeAiApi, /quality:\s*string/)
  assert.match(editorCard, /label="质量"/)
  assert.match(editorCard, /v-model="item\.quality"/)
  assert.match(editor, /quality:\s*item\.quality/)
  // ⚠️ 必填字段清单（质量 / 交付物 / 内容 / 起止日期）现在住在工具模块里。
  assert.match(editorUtils, /\['quality',\s*'质量'\]/)
})

test('month plan quality flows through AI preview and approval views', () => {
  assert.match(aiDiff, /item\.quality \|\| '-'/)
  assert.match(aiDiff, /quality:\s*String\(item\.quality/)
  assert.doesNotMatch(aiDiff, /v-model="item\.quality"/)
  assert.match(approval, /<dt>考核标准（交付物、质量）<\/dt>/)
  assert.match(approval, /formatMonthPlanStandard\(item\.deliverable, item\.quality/)
})
