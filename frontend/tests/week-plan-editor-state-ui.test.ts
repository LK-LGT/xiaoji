import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { countWeekWorkPoints, mergeWeekPlanFields, mergeWeekWorkContent } from '../src/utils/weekPlanDisplay.ts'

const editorUrl = new URL('../src/views/employee/EmployeeWeekPlanEdit.vue', import.meta.url)

test('weekly fields merge into one row without losing detailed work points', () => {
  const content = mergeWeekWorkContent(['1. 完成接口开发\n2、完成接口联调', '- 整理验收材料'])
  const fields = mergeWeekPlanFields([
    { content: '任务一', completionStatus: '已完成', deliverable: '联调记录', remark: '无风险' },
    { content: '任务二', completionStatus: '已完成', deliverable: '验收材料', remark: '' },
  ])

  assert.equal(content, '1. 完成接口开发\n2. 完成接口联调\n3. 整理验收材料')
  assert.equal(countWeekWorkPoints(content), 3)
  assert.equal(fields.content, '1. 任务一\n2. 任务二')
  assert.equal(fields.completionStatus, '已完成')
  assert.equal(fields.deliverable, '联调记录\n验收材料')
  assert.equal(fields.remark, '无风险')
})

test('new week plan uses the target week and opens an existing plan', async () => {
  const editor = await readFile(editorUrl, 'utf8')
  assert.match(editor, /const initialWeekStart = routeWeekStart\(\) \|\| currentMonday\(\)/)
  assert.match(editor, /form = reactive<WeekPlanSavePayload>\(\{ weekStart: initialWeekStart/)
  assert.match(editor, /listEmployeeWeekPlansApi\(\{\s*weekStart:\s*form\.weekStart\s*\}\)/)
  assert.match(editor, /该周已有周计划/)
  assert.match(editor, /existing\.status === 'DRAFT' \|\| existing\.status === 'REJECTED'/)
  assert.match(editor, /`\$\{weekPlanBasePath\}\/\$\{existing\.id\}\/edit`/)
  assert.match(editor, /`\$\{weekPlanBasePath\}\/\$\{existing\.id\}`/)
  assert.doesNotMatch(editor, /v-model="form\.weekStart" type="date"/)
})

test('successful draft save establishes identity and baseline before route replacement', async () => {
  const editor = await readFile(editorUrl, 'utf8')
  assert.match(editor, /const currentPlanId = ref/)
  assert.match(editor, /currentPlanId\.value = saved\.summary\.id[\s\S]*savedSnapshot\.value = normalizeForm\(\)[\s\S]*router\.replace/)
  assert.match(editor, /if \(allowRouteLeave\.value\) return true/)
  assert.match(editor, /async function backToList\(\) \{ await goBack\(weekPlanBasePath\) \}/)
  assert.doesNotMatch(editor, /async function backToList\(\) \{ if \(await confirmLeave\(\)\)/)
})

test('week plan AI assistant uses a dedicated floating drawer entry', async () => {
  const editor = await readFile(editorUrl, 'utf8')
  assert.match(editor, /class="[^"]*week-editor-header[^"]*"/)
  assert.match(editor, /class="[^"]*week-plan-identity[^"]*"/)
  assert.match(editor, /v-model="aiAssistantVisible"[^>]*class="week-ai-drawer"/)
  assert.match(editor, /class="week-ai-floating-entry"/)
  assert.match(editor, /@click="aiAssistantVisible = true"/)
  assert.match(editor, /<PlanAiAssistant[\s\S]*mode="week"[\s\S]*@apply="applyAiDraft"/)
  assert.doesNotMatch(editor, /<main class="plan-editor-main">[\s\S]*<PlanAiAssistant[\s\S]*<section class="week-task-stage">/)
})

test('week plan editor keeps one weekly row in both table and card views', async () => {
  const editor = await readFile(editorUrl, 'utf8')
  const card = await readFile(new URL('../src/views/employee/components/WeekPlanTaskEditorCard.vue', import.meta.url), 'utf8')
  assert.match(editor, /v-model="taskViewMode"/)
  assert.match(editor, /<el-radio-button value="table">/)
  assert.match(editor, /<el-radio-button value="cards">/)
  assert.match(editor, /class="compact-week-task-table" role="table"/)
  assert.match(editor, /v-else class="week-task-editor-list"/)
  assert.match(editor, /localStorage\.getItem\('week-plan-task-view'\)/)
  assert.match(editor, /localStorage\.setItem\('week-plan-task-view', mode\)/)
  assert.match(editor, /matchMedia\('\(max-width: 980px\)'\)\.matches \? 'cards' : 'table'/)
  assert.match(editor, /const weekCardExpanded = ref\(false\)/)
  assert.doesNotMatch(editor, /function expandAllTasks\(\)|function collapseAllTasks\(\)|全部展开|全部收起/)
  assert.doesNotMatch(editor, /v-model="weekItem\.completionStatus"/)
  assert.doesNotMatch(editor, /v-model="weekItem\.deliverable"/)
  assert.doesNotMatch(editor, /v-model="weekItem\.remark"/)
  assert.match(editor, /mergeSaveItems\(detail\.items/)
  assert.match(editor, /items: \[\{ monthPlanItemId: item\.monthPlanItemId/)
  assert.doesNotMatch(editor, /function addItem|function duplicateItem|function removeItem/)
  assert.match(editor, /统计周期/)
  assert.doesNotMatch(editor, /v-model="item\.plannedFinishDate"/)
  assert.match(card, /expanded: boolean/)
  assert.match(card, /toggle: \[\]/)
  assert.doesNotMatch(card, /v-model="model\.completionStatus"/)
  assert.doesNotMatch(card, /v-model="model\.deliverable"/)
  assert.doesNotMatch(card, /v-model="model\.remark"/)
  assert.doesNotMatch(card, /const collapsed = ref\(/)
})

test('week plan submission only carries plan content and defers result fields', async () => {
  const editor = await readFile(editorUrl, 'utf8')
  assert.doesNotMatch(editor, /尚未填写本周完成情况|尚未填写本周交付物/)
  assert.doesNotMatch(editor, /completionStatus: previous\.completionStatus|deliverable: previous\.deliverable|remark: previous\.remark/)
  assert.match(editor, /content: mergeWeekWorkContent\(value\.map/)
  assert.match(editor, /plannedFinishDate: weekEnd\.value/)
})

test('week plan editor delays validation and uses a fixed submission bar', async () => {
  const editor = await readFile(editorUrl, 'utf8')
  assert.match(editor, /const validationFeedbackVisible = ref\(false\)/)
  assert.match(editor, /const visibleIssues = computed\(\(\) => validationFeedbackVisible\.value \? issues\.value : \[\]\)/)
  assert.match(editor, /class="week-validation-bar" aria-label="周计划提交操作"/)
  assert.match(editor, /class="week-submit-readiness"/)
  assert.match(editor, /class="week-submit-metrics"/)
  assert.match(editor, /v-model="assistDrawerVisible"[^>]*title="辅助工具"/)
  assert.match(editor, /class="week-issue-list"/)
  assert.doesNotMatch(editor, /<PlanValidationAside/)
  assert.match(editor, /\.week-validation-bar \{ position:fixed;/)
  assert.match(editor, /\.week-plan-workbench \{[^}]*padding-bottom:112px;/)
})

test('week plan list selects a week and renders one read-only preview table', async () => {
  const list = await readFile(new URL('../src/views/employee/EmployeeWeekPlanList.vue', import.meta.url), 'utf8')
  assert.match(list, /Array\.from\(\{ length: 7 \}/)
  assert.match(list, /shiftWeek\(activeWeekStart\.value, index - 3\)/)
  assert.match(list, /@click="selectWeek\(week\.weekStart\)"/)
  assert.match(list, /getEmployeeWeekPlanApi\(plan\.id\)/)
  assert.match(list, /class="week-preview-table"/)
  assert.match(list, /mergeWeekPlanFields\(selectedDetail\.value\?\.items \|\| \[\]\)/)
  assert.match(list, /selectedWeekFields\.content/)
  assert.doesNotMatch(list, /selectedWeekFields\.completionStatus|selectedWeekFields\.deliverable|selectedWeekFields\.remark/)
  assert.match(list, /:loading="loading" @click="loadRows">刷新<\/el-button>/)
  assert.doesNotMatch(list, /class="week-now-card/)
  assert.doesNotMatch(list, /<el-button v-if="selectedPlan" @click="openSelectedWeek"/)
  assert.match(list, /router\.push\(\{ path: `\$\{weekPlanBasePath\}\/new\/edit`, query: \{ weekStart: activeWeekStart\.value \} \}\)/)
  assert.doesNotMatch(list, /v-model="query\.weekStart"/)
})
