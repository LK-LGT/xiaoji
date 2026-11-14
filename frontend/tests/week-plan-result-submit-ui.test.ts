import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const employeeRoot = new URL('../src/views/employee/', import.meta.url)

test('legacy employee result submit routes redirect into the result center', async () => {
  const router = await readFile(new URL('../../router/index.ts', employeeRoot), 'utf8')
  assert.match(router, /path: '\/employee\/result-submit'/)
  assert.match(router, /`\/employee\/results\/workspace\/WEEK\/\$\{weekPlanId\}`/)
  assert.match(router, /`\/employee\/results\/workspace\/MONTH\/\$\{monthPlanId\}`/)
  assert.match(router, /return \{ path: '\/employee\/results', query: \{ view: 'workbench' \} \}/)
})

test('approved single-row week plan opens the unified week result submission', async () => {
  const detail = await readFile(new URL('EmployeeWeekPlanDetail.vue', employeeRoot), 'utf8')
  assert.match(detail, /resultSubmissionAvailable/)
  assert.match(detail, /resultSubmissionAvailable = computed\(\(\) => detail\.value\?\.summary\.status === 'APPROVED'\)/)
  assert.match(detail, /@click="openResultSubmit\(detail\.items\[0\]\)"/)
  assert.match(detail, /path: `\/employee\/results\/workspace\/WEEK\/\$\{id\}`/)
  assert.match(detail, /path: '\/leader\/results\/submit'/)
  assert.match(detail, /taskId: String\(item\.id\)/)
})

test('unified result page switches plan type and week result uses the monthly interaction structure', async () => {
  const workspace = await readFile(new URL('EmployeeResultPackageWorkspace.vue', employeeRoot), 'utf8')
  const editor = await readFile(new URL('components/WeekResultTaskEditor.vue', employeeRoot), 'utf8')
  assert.match(workspace, /周度执行成果/)
  assert.match(workspace, /WeekResultTaskEditor/)
  assert.match(workspace, /getWeekResultSubmitOptionsApi/)
  assert.match(workspace, /submitWeekResultApi/)
  assert.match(editor, /完成情况/)
  assert.match(editor, /实际交付物/)
  assert.match(workspace, /payload\.append\('completionRate', '100'\)/)
  assert.match(workspace, /payload\.append\('completionStatus'/)
  assert.match(workspace, /payload\.append\('deliverable'/)
  assert.match(workspace, /payload\.append\('remark'/)
  assert.match(workspace, /if \(draft\.file\) payload\.append\('file', draft\.file\)/)
  assert.match(workspace, /class="sheet-evidence"/)
  assert.match(workspace, /onSheetFileChange/)
  assert.match(workspace, /sheetFileAccept/)
  assert.match(workspace, /onSheetDrop/)
  assert.match(workspace, /@drop\.prevent="onSheetDrop\(\$event, task\)"/)
  assert.match(workspace, /is-compact.*hasSheetEvidence/)
  assert.match(workspace, /替换证明材料/)
  assert.match(workspace, /当前已有证明材料，是否替换为新文件/)
  assert.doesNotMatch(editor, /v-model=".*completionRate|完成比例/)
})

test('month result submission remains a separate performance flow', async () => {
  const workspace = await readFile(new URL('EmployeeResultPackageWorkspace.vue', employeeRoot), 'utf8')
  const editor = await readFile(new URL('components/MonthResultTaskEditor.vue', employeeRoot), 'utf8')
  assert.match(workspace, /submitEmployeeResultApi/)
  assert.match(workspace, /monthPlanId/)
  assert.match(workspace, /monthPlanItemId/)
  assert.match(workspace, /completionRate', '100'/)
  assert.doesNotMatch(editor, /v-model=".*completionRate"/)
  assert.doesNotMatch(editor, /label="完成比例"/)
})

test('employee result side entry previews monthly performance before opening the editor', async () => {
  const center = await readFile(new URL('EmployeeResultCenter.vue', employeeRoot), 'utf8')
  const workspace = await readFile(new URL('EmployeeResultPackageWorkspace.vue', employeeRoot), 'utf8')
  assert.match(center, /周期工作台/)
  assert.match(workspace, /预览汇总表/)
  assert.match(workspace, /AI检查/)
  assert.match(workspace, /if \(draft\.file\) payload\.append\('file', draft\.file\)/)
})
