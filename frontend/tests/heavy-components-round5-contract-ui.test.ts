import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

// ═══════════════════════════════════════════════════════════════════════════
// ⚠️ **静态契约测试**（CI-5 阶段 2 第五轮）
//
// 目的：锁定「纯逻辑住在 `src/utils/*.ts`，组件只做转发」这个结构。
//       —— 这样后续任何人把逻辑搬回组件里，测试会立刻失败。
// ═══════════════════════════════════════════════════════════════════════════

const aiContextPopover = await readFile(new URL('../src/utils/aiContextPopover.ts', import.meta.url), 'utf8')
const resultWorkbench = await readFile(new URL('../src/utils/resultWorkbench.ts', import.meta.url), 'utf8')
const weekResultWorkspace = await readFile(new URL('../src/utils/weekResultWorkspace.ts', import.meta.url), 'utf8')
const monthPlanVersionDiff = await readFile(new URL('../src/utils/monthPlanVersionDiff.ts', import.meta.url), 'utf8')
const planAiAssistant = await readFile(new URL('../src/utils/planAiAssistant.ts', import.meta.url), 'utf8')
const planDraftVersionDrawer = await readFile(new URL('../src/utils/planDraftVersionDrawer.ts', import.meta.url), 'utf8')
const monthPlanSupportActions = await readFile(new URL('../src/utils/monthPlanSupportActions.ts', import.meta.url), 'utf8')

const aiContextPopoverView = await readFile(
  new URL('../src/views/employee/components/MonthPlanAiContextPopover.vue', import.meta.url),
  'utf8',
)
const resultWorkbenchView = await readFile(
  new URL('../src/views/employee/components/EmployeeResultWorkbench.vue', import.meta.url),
  'utf8',
)
const weekResultWorkspaceView = await readFile(
  new URL('../src/views/employee/components/EmployeeWeekResultWorkspace.vue', import.meta.url),
  'utf8',
)
const monthPlanVersionDiffView = await readFile(
  new URL('../src/views/employee/components/MonthPlanVersionDiff.vue', import.meta.url),
  'utf8',
)
const planAiAssistantView = await readFile(
  new URL('../src/views/employee/components/PlanAiAssistant.vue', import.meta.url),
  'utf8',
)
const planDraftVersionDrawerView = await readFile(
  new URL('../src/views/employee/components/PlanDraftVersionDrawer.vue', import.meta.url),
  'utf8',
)
const monthPlanSupportActionsView = await readFile(
  new URL('../src/views/employee/components/EmployeeMonthPlanSupportActions.vue', import.meta.url),
  'utf8',
)

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ AI 上下文选择器
// ═══════════════════════════════════════════════════════════════════════════

test('AI 上下文纯逻辑住在 utils 里，组件只做转发', () => {
  assert.match(aiContextPopoverView, /from '@\/utils\/aiContextPopover'/)
  assert.match(aiContextPopoverView, /selectAvailableHistoryMonths/)
  assert.match(aiContextPopoverView, /selectAvailableKnowledgeTypes/)
  assert.match(aiContextPopoverView, /selectAiContextTypes/)
  assert.match(aiContextPopoverView, /isAllAiContextSelected/)
  assert.match(aiContextPopoverView, /isAiContextSelectionIndeterminate/)
  assert.match(aiContextPopoverView, /isAiContextChecked/)
  assert.match(aiContextPopoverView, /isAiContextIndeterminate/)
  assert.match(aiContextPopoverView, /resolveAiContextGroupState/)
  assert.match(aiContextPopoverView, /buildAiContextOptionRows/)
  assert.match(aiContextPopoverView, /resolveAiToggleAll/)
  assert.match(aiContextPopoverView, /resolveAiGroupToggle/)
  assert.match(aiContextPopoverView, /resolveAiChildToggle/)
  assert.match(aiContextPopoverView, /resolveAiBindingLabel/)
})

test('AI 上下文组件里不再内联分组类型判定', () => {
  assert.doesNotMatch(
    aiContextPopoverView,
    /return type === 'HISTORY_PLANS' \|\| type === 'ENTERPRISE_KNOWLEDGE'/,
  )
  assert.match(aiContextPopover, /type === 'HISTORY_PLANS' \|\| type === 'ENTERPRISE_KNOWLEDGE'/)
})

test('AI 上下文组件里不再内联知识标签映射表', () => {
  assert.doesNotMatch(aiContextPopoverView, /NORMATIVE: '正式规范'/)
  assert.match(aiContextPopover, /NORMATIVE: '正式规范'/)
})

test('AI 上下文组件里不再内联选择集合的增删', () => {
  assert.doesNotMatch(
    aiContextPopoverView,
    /const next = new Set\(values\)[\s\S]{0,120}?next\.add\(value\)[\s\S]{0,120}?next\.delete\(value\)/,
  )
  assert.match(aiContextPopover, /new Set\(values\)/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 成果工作台
// ═══════════════════════════════════════════════════════════════════════════

test('成果工作台纯逻辑住在 utils 里，组件只做转发', () => {
  assert.match(resultWorkbenchView, /from '@\/utils\/resultWorkbench'/)
  assert.match(resultWorkbenchView, /countResultStatuses/)
  assert.match(resultWorkbenchView, /countPendingResultTasks/)
  assert.match(resultWorkbenchView, /filterResultPackages/)
  assert.match(resultWorkbenchView, /sortResultPackagesByRejection/)
  assert.match(resultWorkbenchView, /resolveResultWorkbenchEmptyTitle/)
  assert.match(resultWorkbenchView, /resolveResultWorkbenchEmptyHint/)
  assert.match(resultWorkbenchView, /formatCompactResultDate/)
  assert.match(resultWorkbenchView, /resolveResultWeekTitle/)
  assert.match(resultWorkbenchView, /resolveResultPackageTitle/)
  assert.match(resultWorkbenchView, /resolveResultPackageStateText/)
  assert.match(resultWorkbenchView, /resolveResultPackageActionLabel/)
  assert.match(resultWorkbenchView, /shiftResultMonth/)
  assert.match(resultWorkbenchView, /resolveResultPackageTargetTask/)
})

test('成果工作台组件里不再内联状态归一化', () => {
  assert.doesNotMatch(resultWorkbenchView, /upper === 'DRAFT' \|\| upper === 'TODO'/)
  assert.match(resultWorkbench, /upper === 'DRAFT' \|\| upper === 'TODO'/)
})

test('成果工作台组件里不再内联空态标题表', () => {
  assert.doesNotMatch(resultWorkbenchView, /PENDING: '当前月份没有待处理成果'/)
  assert.match(resultWorkbench, /PENDING: '当前月份没有待处理成果'/)
})

test('成果工作台组件里不再内联月份偏移', () => {
  assert.doesNotMatch(resultWorkbenchView, /new Date\(year, month - 1 \+ offset, 1\)/)
  assert.match(resultWorkbench, /new Date\(year, month - 1 \+ offset, 1\)/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 周成果工作区
// ═══════════════════════════════════════════════════════════════════════════

test('周成果工作区纯逻辑住在 utils 里，组件只做转发', () => {
  assert.match(weekResultWorkspaceView, /from '@\/utils\/weekResultWorkspace'/)
  assert.match(weekResultWorkspaceView, /resolveWeekResultCompletion/)
  assert.match(weekResultWorkspaceView, /resolveWeekResultDeliverable/)
  assert.match(weekResultWorkspaceView, /isWeekResultRowLocked/)
  assert.match(weekResultWorkspaceView, /resolveWeekResultStatusMeta/)
  assert.match(weekResultWorkspaceView, /formatWeekResultPeriod/)
  assert.match(weekResultWorkspaceView, /buildEditableWeekResultRow/)
  assert.match(weekResultWorkspaceView, /resolveFocusWeekResultRow/)
  assert.match(weekResultWorkspaceView, /resolveMonthEditableWeekResultRow/)
  assert.match(weekResultWorkspaceView, /resolveWeekMonthFromQuery/)
  assert.match(weekResultWorkspaceView, /resolveWeekPlanIdFromQuery/)
  assert.match(weekResultWorkspaceView, /validateWeekResultRow/)
})

test('周成果工作区组件里不再内联状态映射表', () => {
  assert.doesNotMatch(weekResultWorkspaceView, /SUBMITTED: \{ label: '待审批'/)
  assert.match(weekResultWorkspace, /SUBMITTED: \{ label: '待审批'/)
})

test('周成果工作区组件里不再内联完成情况回填', () => {
  assert.doesNotMatch(
    weekResultWorkspaceView,
    /result\?\.completionStatus === 'COMPLETED' \? result\.content \|\| '已完成'/,
  )
  assert.match(weekResultWorkspace, /completionStatus === 'COMPLETED'/)
})

test('周成果工作区组件里不再手写计划 ID 的 query 解析', () => {
  assert.doesNotMatch(
    weekResultWorkspaceView,
    /Array\.isArray\(route\.query\.weekPlanId\) \? route\.query\.weekPlanId\[0\]/,
  )
  assert.match(weekResultWorkspace, /Array\.isArray/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 版本差异
// ═══════════════════════════════════════════════════════════════════════════

test('版本差异纯逻辑住在 utils 里，组件只做转发', () => {
  assert.match(monthPlanVersionDiffView, /from '@\/utils\/monthPlanVersionDiff'/)
  assert.match(monthPlanVersionDiffView, /buildMonthPlanDiffFilterOptions/)
  assert.match(monthPlanVersionDiffView, /selectVisibleMonthPlanDiffChanges/)
  assert.match(monthPlanVersionDiffView, /areAllVisibleMonthPlanDiffExpanded/)
  assert.match(monthPlanVersionDiffView, /toggleMonthPlanDiffChange/)
  assert.match(monthPlanVersionDiffView, /toggleAllVisibleMonthPlanDiffChanges/)
  assert.match(monthPlanVersionDiffView, /buildDefaultExpandedMonthPlanDiffKeys/)
  assert.match(monthPlanVersionDiffView, /resolveMonthPlanDiffChangeSummary/)
  assert.match(monthPlanVersionDiffView, /buildMonthPlanDiffTaskDetailEntries/)
})

test('版本差异组件里不再内联工作类型映射表', () => {
  assert.doesNotMatch(monthPlanVersionDiffView, /TASK: '任务', METRIC: '指标'/)
  assert.match(monthPlanVersionDiff, /TASK: '任务'/)
})

test('版本差异组件里不再内联筛选选项表', () => {
  assert.doesNotMatch(monthPlanVersionDiffView, /label: `全部 \$\{props\.diff\.items\.length\}`/)
  assert.match(monthPlanVersionDiff, /label: `全部 \$\{diff\.items\.length\}`/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ AI 助手
// ═══════════════════════════════════════════════════════════════════════════

test('AI 助手纯逻辑住在 utils 里，组件只做转发', () => {
  assert.match(planAiAssistantView, /from '@\/utils\/planAiAssistant'/)
  assert.match(planAiAssistantView, /resolveWeekEndDate/)
  assert.match(planAiAssistantView, /buildWeekPlanAiDraft/)
  assert.match(planAiAssistantView, /buildDayPlanAiDraft/)
  assert.match(planAiAssistantView, /resolveAiDraftMissingMessage/)
  assert.match(planAiAssistantView, /resolveAiAssistantEmptyDescription/)
  assert.match(planAiAssistantView, /resolveAiComposerPlaceholder/)
  assert.match(planAiAssistantView, /resolveAiComposerSendLabel/)
  // ⚠️ 字符串数组归一由草稿构造器内部完成 ⇒ 组件不该再自己写一份
  assert.doesNotMatch(planAiAssistantView, /Array\.isArray\(value\) \? value\.map\(\(item\) => String\(item\)\)/)
  assert.match(planAiAssistant, /Array\.isArray\(value\) \? value\.map\(\(item\) => String\(item\)\)/)
})

test('AI 助手组件里不再内联周结束日计算', () => {
  assert.doesNotMatch(planAiAssistantView, /dateValue\.setDate\(dateValue\.getDate\(\) \+ 6\)/)
  assert.match(planAiAssistant, /dateValue\.setDate\(dateValue\.getDate\(\) \+ 6\)/)
})

test('AI 助手组件里不再内联条目主键归一化（BUG-R6-14 守卫）', () => {
  // ⚠️ **BUG-R6-14**：条目主键是字符串 UUID，绝不能被 `Number(...)` 归一化。
  assert.doesNotMatch(
    planAiAssistantView,
    /Number\(payload\.items|Number\(item\.id\)|Number\(raw\.id\)/,
  )
  assert.match(planAiAssistant, /String\(item\.id\)/)
})

test('AI 助手组件里不再内联输入框文案', () => {
  assert.doesNotMatch(planAiAssistantView, /描述本周工作重点和交付要求，生成周计划建议/)
  assert.match(planAiAssistant, /描述本周工作重点和交付要求，生成周计划建议/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 草稿版本抽屉
// ═══════════════════════════════════════════════════════════════════════════

test('草稿版本抽屉纯逻辑住在 utils 里，组件只做转发', () => {
  assert.match(planDraftVersionDrawerView, /from '@\/utils\/planDraftVersionDrawer'/)
  assert.match(planDraftVersionDrawerView, /selectVisiblePlanDraftVersions/)
  assert.match(planDraftVersionDrawerView, /findPlanDraftVersionById/)
  assert.match(planDraftVersionDrawerView, /buildPlanDraftVersionGroupOptions/)
  assert.match(planDraftVersionDrawerView, /buildPlanDraftPreviewEntries/)
  assert.match(planDraftVersionDrawerView, /formatPlanDraftSnapshot/)
  assert.match(planDraftVersionDrawerView, /formatPlanDraftFullTime/)
  assert.match(planDraftVersionDrawerView, /isPlanDraftChanged/)
})

test('草稿版本抽屉组件里不再内联版本分组过滤', () => {
  assert.doesNotMatch(
    planDraftVersionDrawerView,
    /item\.sourceType === 'MANUAL_SAVE' \|\| item\.sourceType === 'RESTORED_SAVE'/,
  )
  assert.match(planDraftVersionDrawer, /'MANUAL_SAVE' \|\| .*'RESTORED_SAVE'/)
})

test('草稿版本抽屉组件里不再内联快照 JSON 序列化', () => {
  assert.doesNotMatch(planDraftVersionDrawerView, /JSON\.stringify\(selected\.value\.snapshot, null, 2\)/)
  assert.match(planDraftVersionDrawer, /JSON\.stringify\(snapshot, null, 2\)/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. ⭐ 月计划支持动作
// ═══════════════════════════════════════════════════════════════════════════

test('月计划支持动作纯逻辑住在 utils 里，组件只做转发', () => {
  assert.match(monthPlanSupportActionsView, /from '@\/utils\/monthPlanSupportActions'/)
  assert.match(monthPlanSupportActionsView, /escapeMonthPlanHtml/)
  assert.match(monthPlanSupportActionsView, /splitMonthPlanTextLines/)
  assert.match(monthPlanSupportActionsView, /formatMonthPlanCurrency/)
  assert.match(monthPlanSupportActionsView, /formatMonthPlanPlainNumber/)
  assert.match(monthPlanSupportActionsView, /buildMonthPlanPrintTitle/)
  assert.match(monthPlanSupportActionsView, /canAdjustMonthPlan/)
  assert.match(monthPlanSupportActionsView, /buildMonthPlanTemplateRows/)
  assert.match(monthPlanSupportActionsView, /renderMonthPlanTaskContent/)
  assert.match(monthPlanSupportActionsView, /renderMonthPlanStandard/)
  assert.match(monthPlanSupportActionsView, /buildMonthPlanTemplatePrintRows/)
  assert.match(monthPlanSupportActionsView, /renderMonthPlanEstimatedTotal/)
})

test('月计划支持动作组件里不再内联 HTML 转义', () => {
  assert.doesNotMatch(monthPlanSupportActionsView, /\.replace\(\/&\/g, '&amp;'\)/)
  assert.match(monthPlanSupportActions, /\.replace\(\/&\/g, '&amp;'\)/)
})

test('月计划支持动作组件里不再内联模板兜底行数', () => {
  assert.doesNotMatch(monthPlanSupportActionsView, /Array\.from\(\{ length: 8 \}/)
  assert.match(monthPlanSupportActions, /MONTH_PLAN_TEMPLATE_BLANK_ROWS = 8/)
})

test('月计划支持动作组件里不再内联打印标题生成', () => {
  assert.doesNotMatch(
    monthPlanSupportActionsView,
    /padStart\(2, '0'\)\}\s*月个人目标制定/,
  )
  assert.match(monthPlanSupportActions, /padStart\(2, '0'\)\}\s*月个人目标制定/)
})

// ═══════════════════════════════════════════════════════════════════════════
// 8. ⭐ 跨模块通用守卫
// ═══════════════════════════════════════════════════════════════════════════

test('第五轮新增的 6 个 utils 文件都存在且非空', () => {
  const files = [
    aiContextPopover,
    resultWorkbench,
    weekResultWorkspace,
    monthPlanVersionDiff,
    planAiAssistant,
    planDraftVersionDrawer,
    monthPlanSupportActions,
  ]
  for (const content of files) {
    assert.ok(content.length > 500, `utils 文件内容过短（${content.length} 字符）`)
  }
})

test('第五轮的 7 个组件都从 utils 导入纯函数（不许自己内联）', () => {
  const views = [
    aiContextPopoverView,
    resultWorkbenchView,
    weekResultWorkspaceView,
    monthPlanVersionDiffView,
    planAiAssistantView,
    planDraftVersionDrawerView,
    monthPlanSupportActionsView,
  ]
  for (const view of views) {
    assert.match(view, /from '@\/utils\//)
  }
})

test('第五轮的组件都标注了「纯逻辑已抽到 utils」的说明', () => {
  const views = [
    aiContextPopoverView,
    resultWorkbenchView,
    weekResultWorkspaceView,
    monthPlanVersionDiffView,
    planAiAssistantView,
    planDraftVersionDrawerView,
    monthPlanSupportActionsView,
  ]
  for (const view of views) {
    assert.match(view, /纯逻辑已抽到 `@\/utils\//)
  }
})
