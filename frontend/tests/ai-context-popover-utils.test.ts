import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildAiContextOptionRows,
  isAiContextChecked,
  isAiContextIndeterminate,
  isAiContextSelectionIndeterminate,
  isAllAiContextSelected,
  isGroupedAiContextType,
  resolveAiBindingLabel,
  resolveAiChildToggle,
  resolveAiContextGroupState,
  resolveAiGroupToggle,
  resolveAiToggleAll,
  selectAiContextTypes,
  selectAiKnowledgeReferences,
  selectAvailableHistoryMonths,
  selectAvailableKnowledgeTypes,
  updateAiContextTypeSelection,
  updateAiSelection,
} from '../src/utils/aiContextPopover.ts'

const LABELS: Record<string, string> = {
  HISTORY_PLANS: '历史计划',
  ENTERPRISE_KNOWLEDGE: '企业知识',
  JOB_DESCRIPTION: '岗位描述',
  PERFORMANCE_HISTORY: '绩效历史',
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 可用项筛选
// ═══════════════════════════════════════════════════════════════════════════

test('可用历史月份只取 available 为真的', () => {
  const months = selectAvailableHistoryMonths([
    { month: '2026-01', available: true },
    { month: '2026-02', available: false },
    { month: '2026-03', available: true },
  ])
  assert.deepEqual(months, ['2026-01', '2026-03'])
})

test('可用历史月份：undefined / 空数组 ⇒ 空数组', () => {
  assert.deepEqual(selectAvailableHistoryMonths(undefined), [])
  assert.deepEqual(selectAvailableHistoryMonths([]), [])
})

test('可用知识类型只取 available 为真的', () => {
  const types = selectAvailableKnowledgeTypes([
    { type: 'NORMATIVE', available: true },
    { type: 'REFERENCE', available: false },
  ])
  assert.deepEqual(types, ['NORMATIVE'])
})

test('可用知识类型：undefined ⇒ 空数组', () => {
  assert.deepEqual(selectAvailableKnowledgeTypes(undefined), [])
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 分组判定
// ═══════════════════════════════════════════════════════════════════════════

test('只有历史计划和企业知识是分组型', () => {
  assert.equal(isGroupedAiContextType('HISTORY_PLANS'), true)
  assert.equal(isGroupedAiContextType('ENTERPRISE_KNOWLEDGE'), true)
  assert.equal(isGroupedAiContextType('JOB_DESCRIPTION'), false)
  assert.equal(isGroupedAiContextType('PERFORMANCE_HISTORY'), false)
})

test('分组状态：历史计划返回可用月份 / 已选 / 总数', () => {
  const state = resolveAiContextGroupState('HISTORY_PLANS', {
    availableHistoryMonths: ['2026-01'],
    selectedHistoryMonths: ['2026-01', '2026-02'],
    historyMonthTotal: 3,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.deepEqual(state, {
    available: ['2026-01'],
    selected: ['2026-01', '2026-02'],
    total: 3,
  })
})

test('分组状态：企业知识返回可用类型 / 已选 / 总数', () => {
  const state = resolveAiContextGroupState('ENTERPRISE_KNOWLEDGE', {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: ['NORMATIVE'],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 2,
  })
  assert.deepEqual(state, { available: ['NORMATIVE'], selected: [], total: 2 })
})

test('分组状态：非分组型 ⇒ undefined', () => {
  assert.equal(resolveAiContextGroupState('JOB_DESCRIPTION', {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  }), undefined)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 选中 / 半选判定
// ═══════════════════════════════════════════════════════════════════════════

test('分组全选：可用项全部被选中 ⇒ true', () => {
  const group = { available: ['a', 'b'], selected: ['a', 'b'], total: 2 }
  assert.equal(isAiContextChecked('HISTORY_PLANS', group, []), true)
})

test('分组全选：只选了一部分 ⇒ false', () => {
  const group = { available: ['a', 'b'], selected: ['a'], total: 2 }
  assert.equal(isAiContextChecked('HISTORY_PLANS', group, []), false)
})

test('分组全选：可用项为空 ⇒ false（不能显示一个没法取消的勾）', () => {
  const group = { available: [], selected: [], total: 0 }
  assert.equal(isAiContextChecked('HISTORY_PLANS', group, []), false)
})

test('单选全选：看父级选择里有没有它', () => {
  assert.equal(isAiContextChecked('JOB_DESCRIPTION', undefined, ['JOB_DESCRIPTION']), true)
  assert.equal(isAiContextChecked('JOB_DESCRIPTION', undefined, []), false)
})

test('半选：部分选中且未全选 ⇒ true', () => {
  assert.equal(isAiContextIndeterminate({ available: ['a', 'b'], selected: ['a'], total: 2 }), true)
})

test('半选：全选 / 全不选 ⇒ false', () => {
  assert.equal(isAiContextIndeterminate({ available: ['a', 'b'], selected: ['a', 'b'], total: 2 }), false)
  assert.equal(isAiContextIndeterminate({ available: ['a', 'b'], selected: [], total: 2 }), false)
})

test('半选：非分组（undefined）⇒ false', () => {
  assert.equal(isAiContextIndeterminate(undefined), false)
})

test('可选上下文：没有可用月份 ⇒ 历史计划不可选', () => {
  const types = selectAiContextTypes(
    ['HISTORY_PLANS', 'ENTERPRISE_KNOWLEDGE', 'JOB_DESCRIPTION'],
    [],
    ['NORMATIVE'],
  )
  assert.deepEqual(types, ['ENTERPRISE_KNOWLEDGE', 'JOB_DESCRIPTION'])
})

test('可选上下文：没有可用知识类型 ⇒ 企业知识不可选', () => {
  const types = selectAiContextTypes(
    ['HISTORY_PLANS', 'ENTERPRISE_KNOWLEDGE'],
    ['2026-01'],
    [],
  )
  assert.deepEqual(types, ['HISTORY_PLANS'])
})

test('可选上下文：其余类型一律可选', () => {
  const types = selectAiContextTypes(['PERFORMANCE_HISTORY'], [], [])
  assert.deepEqual(types, ['PERFORMANCE_HISTORY'])
})

test('父级全选：全部可选类型都被选中 ⇒ true', () => {
  assert.equal(isAllAiContextSelected(['A', 'B'], ['A', 'B']), true)
})

test('父级全选：空可选列表 ⇒ true（Vue checkbox 标准语义）', () => {
  assert.equal(isAllAiContextSelected([], []), true)
})

test('父级全选：只选一部分 ⇒ false', () => {
  assert.equal(isAllAiContextSelected(['A', 'B'], ['A']), false)
})

test('父级半选：选了但没全选 ⇒ true', () => {
  assert.equal(isAiContextSelectionIndeterminate(['A', 'B'], ['A']), true)
})

test('父级半选：全选 / 全不选 ⇒ false', () => {
  assert.equal(isAiContextSelectionIndeterminate(['A', 'B'], ['A', 'B']), false)
  assert.equal(isAiContextSelectionIndeterminate(['A', 'B'], []), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 选择集合的增删
// ═══════════════════════════════════════════════════════════════════════════

test('增删选择：选中 ⇒ 加入', () => {
  assert.deepEqual(updateAiSelection(['a'], 'b', true), ['a', 'b'])
})

test('增删选择：取消 ⇒ 移除', () => {
  assert.deepEqual(updateAiSelection(['a', 'b'], 'a', false), ['b'])
})

test('增删选择：重复选中不会产生重复项（Set 去重）', () => {
  assert.deepEqual(updateAiSelection(['a'], 'a', true), ['a'])
})

test('增删选择：不改原数组', () => {
  const origin = ['a']
  updateAiSelection(origin, 'b', true)
  assert.deepEqual(origin, ['a'])
})

test('增删父级上下文类型：选中 / 取消', () => {
  assert.deepEqual(updateAiContextTypeSelection(['A'], 'B', true), ['A', 'B'])
  assert.deepEqual(updateAiContextTypeSelection(['A', 'B'], 'A', false), ['B'])
})

test('全选：勾上时选「可选类型」而不是「全部类型」', () => {
  const result = resolveAiToggleAll(true, ['ENTERPRISE_KNOWLEDGE'], ['2026-01'], ['NORMATIVE'])
  assert.deepEqual(result, {
    contextTypes: ['ENTERPRISE_KNOWLEDGE'],
    historyMonths: ['2026-01'],
    knowledgeTypes: ['NORMATIVE'],
  })
})

test('全选：取消 ⇒ 三组全空', () => {
  const result = resolveAiToggleAll(false, ['A'], ['2026-01'], ['NORMATIVE'])
  assert.deepEqual(result, { contextTypes: [], historyMonths: [], knowledgeTypes: [] })
})

test('全选：字符串 "false" 也是 truthy ⇒ 走「全选」分支（Boolean 语义）', () => {
  const result = resolveAiToggleAll('false', ['A'], ['2026-01'], ['NORMATIVE'])
  assert.deepEqual(result.contextTypes, ['A'])
})

test('全选：数字 0 ⇒ 走「全不选」分支', () => {
  const result = resolveAiToggleAll(0, ['A'], ['2026-01'], ['NORMATIVE'])
  assert.deepEqual(result.contextTypes, [])
})

test('切换历史分组：全选 ⇒ 值全选且父级选中', () => {
  const result = resolveAiGroupToggle('HISTORY_PLANS', true, ['2026-01', '2026-02'], [])
  assert.deepEqual(result, { values: ['2026-01', '2026-02'], parentSelected: true })
})

test('切换历史分组：全清 ⇒ 值空且父级取消', () => {
  const result = resolveAiGroupToggle('HISTORY_PLANS', false, ['2026-01'], [])
  assert.deepEqual(result, { values: [], parentSelected: false })
})

test('切换知识分组：全选 ⇒ 值全选且父级选中', () => {
  const result = resolveAiGroupToggle('ENTERPRISE_KNOWLEDGE', true, [], ['NORMATIVE'])
  assert.deepEqual(result, { values: ['NORMATIVE'], parentSelected: true })
})

test('切换子项：取消最后一个 ⇒ 父级自动取消', () => {
  const result = resolveAiChildToggle('HISTORY_PLANS', ['2026-01'], '2026-01', false)
  assert.deepEqual(result, { values: [], parentSelected: false })
})

test('切换子项：还剩其它子项 ⇒ 父级保持选中', () => {
  const result = resolveAiChildToggle('HISTORY_PLANS', ['2026-01', '2026-02'], '2026-01', false)
  assert.deepEqual(result, { values: ['2026-02'], parentSelected: true })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 展示行
// ═══════════════════════════════════════════════════════════════════════════

test('展示行：远端标签优先', () => {
  const rows = buildAiContextOptionRows(
    ['JOB_DESCRIPTION'],
    { contextOptions: [{ type: 'JOB_DESCRIPTION', label: '远端岗位描述' }] },
    '写代码',
    LABELS,
    {
      availableHistoryMonths: [],
      selectedHistoryMonths: [],
      historyMonthTotal: 0,
      availableKnowledgeTypes: [],
      selectedKnowledgeTypes: [],
      knowledgeTypeTotal: 0,
    },
  )
  assert.equal(rows[0].label, '远端岗位描述')
})

test('展示行：没有远端标签 ⇒ 用本地常量表', () => {
  const rows = buildAiContextOptionRows(['PERFORMANCE_HISTORY'], undefined, '', LABELS, {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.equal(rows[0].label, '绩效历史')
})

test('展示行：岗位描述为空 ⇒ 不可用', () => {
  const rows = buildAiContextOptionRows(['JOB_DESCRIPTION'], undefined, '   ', LABELS, {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.equal(rows[0].available, false)
})

test('展示行：岗位描述非空 ⇒ 可用，摘要为去空白后的文本', () => {
  const rows = buildAiContextOptionRows(['JOB_DESCRIPTION'], undefined, '  后端开发  ', LABELS, {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.equal(rows[0].available, true)
  assert.equal(rows[0].summary, '后端开发')
})

test('展示行：分组型 ⇒ 有可选项才算可用', () => {
  const rows = buildAiContextOptionRows(['HISTORY_PLANS'], undefined, '', LABELS, {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.equal(rows[0].available, false)
})

test('展示行：分组型摘要 ⇒ 已选 x/y 个月', () => {
  const rows = buildAiContextOptionRows(['HISTORY_PLANS'], undefined, '', LABELS, {
    availableHistoryMonths: ['2026-01', '2026-02'],
    selectedHistoryMonths: ['2026-01'],
    historyMonthTotal: 5,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.equal(rows[0].summary, '已选 1/5 个月')
})

test('展示行：企业知识摘要 ⇒ 已选 x/y 类', () => {
  const rows = buildAiContextOptionRows(['ENTERPRISE_KNOWLEDGE'], undefined, '', LABELS, {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: ['NORMATIVE'],
    selectedKnowledgeTypes: ['NORMATIVE'],
    knowledgeTypeTotal: 2,
  })
  assert.equal(rows[0].summary, '已选 1/2 类')
})

test('展示行：远端 available 优先于 availableContext 推断', () => {
  const rows = buildAiContextOptionRows(
    ['PERFORMANCE_HISTORY'],
    {
      contextOptions: [{ type: 'PERFORMANCE_HISTORY', available: false }],
      availableContext: ['绩效历史'],
    },
    '',
    LABELS,
    {
      availableHistoryMonths: [],
      selectedHistoryMonths: [],
      historyMonthTotal: 0,
      availableKnowledgeTypes: [],
      selectedKnowledgeTypes: [],
      knowledgeTypeTotal: 0,
    },
  )
  assert.equal(rows[0].available, false)
})

test('展示行：没有远端 available ⇒ 看 availableContext 是否含中文标签', () => {
  const rows = buildAiContextOptionRows(
    ['PERFORMANCE_HISTORY'],
    { availableContext: ['绩效历史'] },
    '',
    LABELS,
    {
      availableHistoryMonths: [],
      selectedHistoryMonths: [],
      historyMonthTotal: 0,
      availableKnowledgeTypes: [],
      selectedKnowledgeTypes: [],
      knowledgeTypeTotal: 0,
    },
  )
  assert.equal(rows[0].available, true)
})

test('展示行：availableContext 缺失 ⇒ 不可用（不抛错）', () => {
  const rows = buildAiContextOptionRows(['PERFORMANCE_HISTORY'], undefined, '', LABELS, {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.equal(rows[0].available, false)
})

test('展示行：远端摘要去空白', () => {
  const rows = buildAiContextOptionRows(
    ['PERFORMANCE_HISTORY'],
    { contextOptions: [{ type: 'PERFORMANCE_HISTORY', summary: '  近三月  ' }] },
    '',
    LABELS,
    {
      availableHistoryMonths: [],
      selectedHistoryMonths: [],
      historyMonthTotal: 0,
      availableKnowledgeTypes: [],
      selectedKnowledgeTypes: [],
      knowledgeTypeTotal: 0,
    },
  )
  assert.equal(rows[0].summary, '近三月')
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 知识引用与标签
// ═══════════════════════════════════════════════════════════════════════════

test('知识引用：只保留被勾选的类型', () => {
  const refs = [
    { knowledgeType: 'NORMATIVE', text: 'a' },
    { knowledgeType: 'REFERENCE', text: 'b' },
  ]
  assert.deepEqual(selectAiKnowledgeReferences(refs, ['NORMATIVE']), [{ knowledgeType: 'NORMATIVE', text: 'a' }])
})

test('知识引用：undefined ⇒ 空数组', () => {
  assert.deepEqual(selectAiKnowledgeReferences(undefined, ['NORMATIVE']), [])
})

test('知识引用：没勾任何类型 ⇒ 空数组', () => {
  assert.deepEqual(selectAiKnowledgeReferences([{ knowledgeType: 'NORMATIVE' }], []), [])
})

test('知识标签：三种已知类型', () => {
  assert.equal(resolveAiBindingLabel('NORMATIVE'), '正式规范')
  assert.equal(resolveAiBindingLabel('REFERENCE'), '参考资料')
  assert.equal(resolveAiBindingLabel('EXAMPLE'), '示例案例')
})

test('知识标签：未知类型原样返回', () => {
  assert.equal(resolveAiBindingLabel('WHATEVER'), 'WHATEVER')
})

test('知识标签：空值 ⇒ 知识资料', () => {
  assert.equal(resolveAiBindingLabel(''), '知识资料')
})
