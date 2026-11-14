import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const employeeRoot = new URL('../src/views/employee/', import.meta.url)

test('weekly plan is a single summary row without per-task parent controls', async () => {
  const edit = await readFile(new URL('EmployeeWeekPlanEdit.vue', employeeRoot), 'utf8')
  const card = await readFile(new URL('components/WeekPlanTaskEditorCard.vue', employeeRoot), 'utf8')
  assert.match(edit, /所有工作统一写在一份周计划中/)
  assert.match(edit, /const weekItem = computed/)
  assert.match(edit, /form\.items = \[mergeSaveItems\(detail\.items/)
  assert.doesNotMatch(edit, /listWeekPlanParentOptionsApi|visibleOptionsFor|usedSourceIds/)
  assert.doesNotMatch(card, /关联月计划事项|<el-select|复制当前任务|删除当前任务/)
  assert.match(card, /所有任务统一分点填写/)
})

test('daily plan links optional weekly items with a multi-select', async () => {
  const editor = await readFile(new URL('components/EmployeeDayPlanEditor.vue', employeeRoot), 'utf8')
  assert.match(editor, /关联本周计划（选填）/)
  assert.match(editor, /v-model="form\.relatedWeekPlanItemIds"/)
  assert.match(editor, /<el-select[^>]*multiple[^>]*clearable/)
  assert.doesNotMatch(editor, /建议关联一个月计划项/)
  assert.match(editor, /relatedWeekPlanItemIds:\s*\[\.\.\.form\.relatedWeekPlanItemIds\]/)
})
