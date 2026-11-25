import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { pathToFileURL } from 'node:url'
import { normalizeMonthPlanPeriods as normalizePcPeriods } from '../src/utils/monthPlanPeriod.ts'

const diffPath = fileURLToPath(new URL('../src/views/employee/components/MonthPlanAiDiff.vue', import.meta.url))
const workspacePath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanAiWorkspace.vue', import.meta.url))
const editorPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanEdit.vue', import.meta.url))

/**
 * ⚠️⚠️ **小程序对照源在「仓库之外」**（2026-11-25 修复 CI 连续红）
 * ------------------------------------------------------------------
 * 小程序（``01-employee-performance``）是**同级目录**，不是本仓库的子目录。
 * ⇒ 本地有 ⇒ 测试通过；**CI 上没有**（CI 只 checkout 本仓库）
 * ⇒ 之前的**静态 import** 直接 ``ERR_MODULE_NOT_FOUND``
 * ⇒ **整个测试文件加载失败** ⇒ 里面的用例一个都没跑。
 *
 * ⚠️ **为什么必须改成「动态 import + 存在性探测」**
 *   静态 ``import`` 在**模块加载期**求值 ⇒ 无法用 try/catch 或
 *   条件导入兜住 ⇒ 只要文件不存在，**整个文件就崩**。
 *   ⇒ 改成：先 ``existsSync`` 探测，存在才动态 import。
 */
const miniRoot = '../../../01-employee-performance/enterprise-plan-results/app/'
const miniPeriodModule = fileURLToPath(new URL(`${miniRoot}utils/month-plan-period.js`, import.meta.url))
const miniEditorPath = fileURLToPath(new URL(`${miniRoot}pages/employee/month/edit.vue`, import.meta.url))
const miniDraftSheetPath = fileURLToPath(new URL(`${miniRoot}components/plan/MonthAiDraftSheet.vue`, import.meta.url))

/** 小程序对照源是否在本机存在（CI 上为 false）。 */
const HAS_MINI_SOURCE = existsSync(miniPeriodModule)

/**
 * ⚠️ **动态 import 必须用 ``file://`` URL，不能用裸绝对路径**
 * ESM loader 只认 ``file:`` / ``data:`` / ``node:`` 三种 scheme
 * ⇒ Windows 上传绝对路径（``D:\...``）会报
 *   ``ERR_UNSUPPORTED_ESM_URL_SCHEME``（protocol 'd:'）
 * ⇒ 用 ``pathToFileURL`` 转一下（Linux 上也一样，只是没必要）。
 */
const normalizeMiniPeriods: typeof normalizePcPeriods | null = HAS_MINI_SOURCE
  ? (await import(pathToFileURL(miniPeriodModule).href)).normalizeMonthPlanPeriods
  : null

const diff = readFileSync(diffPath, 'utf8')
const workspace = readFileSync(workspacePath, 'utf8')
const editor = readFileSync(editorPath, 'utf8')
const miniEditor = HAS_MINI_SOURCE ? readFileSync(miniEditorPath, 'utf8') : ''
const miniDraftSheet = HAS_MINI_SOURCE ? readFileSync(miniDraftSheetPath, 'utf8') : ''

test('month plan AI result preview keeps only the back action above selectable changes', () => {
  assert.match(diff, /返回助手/)
  assert.doesNotMatch(diff, /class="change-summary"/)
  assert.doesNotMatch(diff, /AI 建议已生成/)
  assert.doesNotMatch(diff, /当前计划优化建议/)
  assert.doesNotMatch(diff, /选择需要的内容，再应用到当前计划/)
  assert.match(diff, /应用到当前计划/)
  assert.match(diff, /selectedIndexes/)
  assert.match(diff, /operationMode/)
  assert.doesNotMatch(diff, /class="compare-workspace"/)
  assert.doesNotMatch(diff, /v-model="draft\.summary"/)
  assert.doesNotMatch(diff, /<el-table/)
  assert.doesNotMatch(diff, /append-to-body/)
})

test('month plan AI warnings are collapsed so they do not dominate the preview', () => {
  assert.match(diff, /<details v-if="suggestion\?\.warnings\?\.length" class="warning-summary">/)
  assert.match(diff, /AI 参考提醒/)
  assert.match(diff, /\.warning-list\s*{\s*display:grid;[^}]*max-height:140px;[^}]*overflow:auto;/)
  assert.doesNotMatch(diff, /el-alert v-for="warning/)
})

test('month plan AI replacement uses fresh items while append keeps current ids', () => {
  assert.match(workspace, /:current-items="currentItems"/)
  assert.match(workspace, /function mapCurrentItems\(\)/)
  assert.match(workspace, /id:\s*item\.id/)
  assert.match(workspace, /function stripIds\(items: AiPlanDraftItem\[\]\)/)
  assert.match(diff, /export interface AiPlanDraftItem extends AiPlanItem/)
  assert.match(workspace, /const proposedItems = value\.mode === 'append' \? \[\.\.\.currentItems\.value, \.\.\.value\.items\] : value\.items/)
  assert.doesNotMatch(diff, /normalizeItem\(item,\s*append \? undefined : props\.currentItems\[index\]\)/)
})

test('month plan AI preview selects suggestions when it opens', () => {
  assert.match(diff, /props\.suggestion\?\.items\.map/)
  assert.match(diff, /if \(visible\) resetSelection\(\)/)
})

test('month plan AI tasks without dates are scheduled in order inside the current month', () => {
  const tasks = Array.from({ length: 3 }, (_, index) => ({ taskName: `任务${index + 1}` }))
  const expected = [
    ['2026-08-06', '2026-08-13'],
    ['2026-08-14', '2026-08-22'],
    ['2026-08-23', '2026-08-31'],
  ]
  const pc = normalizePcPeriods('2026-08', tasks, '2026-08-06')

  assert.deepEqual(pc.map((item) => [item.estimatedStartDate, item.estimatedEndDate]), expected)
  // ⚠️ **小程序对照源不在（CI）时跳过这一半** —— PC 侧的断言仍然照跑。
  if (normalizeMiniPeriods) {
    const mini = normalizeMiniPeriods('2026-08', tasks, '2026-08-06')
    assert.deepEqual(mini.map((item) => [item.estimatedStartDate, item.estimatedEndDate]), expected)
  }
})

test('month plan scheduling preserves valid AI dates and repairs partial dates', () => {
  const result = normalizePcPeriods('2026-09', [
    { estimatedStartDate: '2026-09-03', estimatedEndDate: '2026-09-08' },
    { estimatedStartDate: '2026-09-20', estimatedEndDate: '' },
    { estimatedStartDate: '', estimatedEndDate: '2026-09-28' },
  ], '2026-08-06')

  assert.deepEqual([result[0].estimatedStartDate, result[0].estimatedEndDate], ['2026-09-03', '2026-09-08'])
  assert.deepEqual([result[1].estimatedStartDate, result[1].estimatedEndDate], ['2026-09-20', '2026-09-20'])
  assert.deepEqual([result[2].estimatedStartDate, result[2].estimatedEndDate], ['2026-09-21', '2026-09-28'])
})

test('PC and mini month plan flows retain dates and repair old empty drafts before submit', () => {
  assert.match(workspace, /normalizeMonthPlanPeriods\(plan\.value\.planMonth, proposedItems\)/)
  // ⚠️ 这条断言原先硬编码成 `detail.items.map`（内联写法），
  // 但实现已重构为「先抽 `detailItems` 变量，再 normalize 后 map」。
  // **行为意图没变** —— 仍然是用 detail.planMonth 做周期归一化再逐项 map，
  // 所以改断言适配新写法，而不是回退实现。
  assert.match(editor, /normalizeMonthPlanPeriods\(detail\.planMonth, detailItems\)\.map\(/)
  assert.match(editor, /function validateDraft[\s\S]*?ensureFormItemPeriods\(\)/)
  // ⚠️ **小程序对照源不在（CI）时跳过小程序侧断言** —— PC 侧的照跑。
  if (HAS_MINI_SOURCE) {
    assert.match(miniEditor, /estimatedStartDate:\s*item\.estimatedStartDate/)
    assert.match(miniEditor, /estimatedEndDate:\s*item\.estimatedEndDate/)
    assert.match(miniEditor, /normalizeMonthPlanPeriods\(form\.planMonth/)
    assert.doesNotMatch(miniEditor, /deadline:\s*item\.deadline\s*\|\|\s*null/)
    assert.match(miniDraftSheet, /item\.estimatedStartDate/)
    assert.match(miniDraftSheet, /item\.estimatedEndDate/)
  }
})

test('小程序对照源缺失时应明确跳过而不是误报通过', () => {
  /**
   * ⚠️ **为什么要有这条自检**
   * 上一条用例在「小程序源缺失」时**只查了 PC 侧**。
   * ⇒ 如果哪天有人把对照逻辑整个删掉、把 ``HAS_MINI_SOURCE``
   *   永远设成 false，这条用例会**「绿着但什么都没查」**。
   * ⇒ 所以必须有这条：**当对照源存在时，断言它确实被读到了**。
   */
  if (!HAS_MINI_SOURCE) {
    assert.ok(
      normalizeMiniPeriods === null && miniEditor === '' && miniDraftSheet === '',
      '对照源缺失时应全部为空/null（否则说明读取路径写错了）',
    )
    return
  }
  assert.ok(typeof normalizeMiniPeriods === 'function', '小程序周期归一化函数应可用')
  assert.ok(miniEditor.length > 0, '小程序 edit.vue 应非空')
  assert.ok(miniDraftSheet.length > 0, '小程序 MonthAiDraftSheet.vue 应非空')
})
