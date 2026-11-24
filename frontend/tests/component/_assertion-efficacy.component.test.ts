/**
 * 🔴 **断言有效性自检（CI-5 阶段 2）**
 *
 * ⚠️ **这个文件是干什么的**
 * ------------------------------------------------------------------
 * 挂载测试最大的风险是「**测了，但测不出东西**」——
 * 断言写得太松（``expect(wrapper.text()).toBeTruthy()``），
 * 或者断言的恰好是「无论逻辑对错都成立」的东西。
 *
 * ⚠️ **这里的做法**
 *   在**测试内**复刻一份「**故意写错的折叠逻辑**」，
 *   断言它与正确实现的**输出必须不同**。
 *
 *   ⇒ 证明挂载测试里那几条断言用的判据，
 *     **确实能区分「逻辑对」和「逻辑错」**，不是在凑数。
 *
 * ⚠️ **为什么不直接改产品源码做实验**
 *   改 ``src/`` 里的组件来验证 ⇒ 会污染工作区、可能被误提交。
 *   这里复刻逻辑、只动测试文件 ⇒ **零产品代码风险**。
 *
 * ⚠️ **注意**：这些用例**永远通过** ——
 *   它们验证的是「两种实现有差异」这个事实，
 *   而不是「某个实现对不对」。
 *   对不对由 ``leader-employee-work.component.test.ts`` 负责。
 */
import { describe, expect, test } from 'vitest'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

interface Row {
  workType: string
  businessId: number
  itemId?: number
  periodLabel: string
  content: string
  title: string
}

/** ✅ **正确实现** —— 复制自 ``LeaderEmployeeWork.vue`` 的 ``collapseMonthPlanRows``。 */
function collapseCorrect(items: Row[]): Row[] {
  const monthPlanCounts = new Map<number, number>()
  const seenMonthPlanIds = new Set<number>()
  for (const item of items) {
    if (item.workType === 'MONTH_PLAN') {
      monthPlanCounts.set(item.businessId, (monthPlanCounts.get(item.businessId) ?? 0) + 1)
    }
  }
  return items.flatMap((item) => {
    if (item.workType !== 'MONTH_PLAN') return [item]
    if (seenMonthPlanIds.has(item.businessId)) return []
    seenMonthPlanIds.add(item.businessId)
    if (item.itemId == null) return [item]
    const taskCount = monthPlanCounts.get(item.businessId) ?? 1
    return [{
      ...item,
      title: `${item.periodLabel || '本月'} 月计划`,
      content: `共 ${taskCount} 项月计划任务`,
    }]
  })
}

/** ❌ **错误实现 A**：把 ``!==`` 写成 ``===``（折叠条件反了）。 */
function collapseConditionReversed(items: Row[]): Row[] {
  const monthPlanCounts = new Map<number, number>()
  const seenMonthPlanIds = new Set<number>()
  for (const item of items) {
    if (item.workType === 'MONTH_PLAN') {
      monthPlanCounts.set(item.businessId, (monthPlanCounts.get(item.businessId) ?? 0) + 1)
    }
  }
  return items.flatMap((item) => {
    // ⚠️ **这就是注入的 bug**
    if (item.workType === 'MONTH_PLAN') return [item]
    if (seenMonthPlanIds.has(item.businessId)) return []
    seenMonthPlanIds.add(item.businessId)
    if (item.itemId == null) return [item]
    const taskCount = monthPlanCounts.get(item.businessId) ?? 1
    return [{
      ...item,
      title: `${item.periodLabel || '本月'} 月计划`,
      content: `共 ${taskCount} 项月计划任务`,
    }]
  })
}

/** ❌ **错误实现 B**：漏掉去重（同一 businessId 保留多行）。 */
function collapseWithoutDedupe(items: Row[]): Row[] {
  const monthPlanCounts = new Map<number, number>()
  for (const item of items) {
    if (item.workType === 'MONTH_PLAN') {
      monthPlanCounts.set(item.businessId, (monthPlanCounts.get(item.businessId) ?? 0) + 1)
    }
  }
  return items.flatMap((item) => {
    if (item.workType !== 'MONTH_PLAN') return [item]
    if (item.itemId == null) return [item]
    const taskCount = monthPlanCounts.get(item.businessId) ?? 1
    return [{
      ...item,
      title: `${item.periodLabel || '本月'} 月计划`,
      content: `共 ${taskCount} 项月计划任务`,
    }]
  })
}

/** ✅ **正确的总数计算** —— 复制自组件的 ``displayTotal``。 */
function displayTotalCorrect(items: Row[], total: number): number {
  const displayItems = collapseCorrect(items)
  const collapsedOnPage = items.length - displayItems.length
  return Math.max(displayItems.length, total - collapsedOnPage)
}

/** ❌ **错误实现 C**：总数忘了减去折叠掉的行。 */
function displayTotalWithoutCollapseFix(items: Row[], total: number): number {
  // ⚠️ **这就是注入的 bug** —— 忘了减 collapsedOnPage。
  return total
}

function task(businessId: number, itemId: number): Row {
  return {
    workType: 'MONTH_PLAN',
    businessId,
    itemId,
    periodLabel: '11月',
    content: '任务正文',
    title: '月计划任务',
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 正确实现的基线（挂载测试依赖这些行为）
// ═══════════════════════════════════════════════════════════════════════════

describe('正确实现的基线', () => {
  test('3 条月计划任务 → 折叠成 1 行', () => {
    const rows = collapseCorrect([task(500, 1), task(500, 2), task(500, 3)])

    expect(rows).toHaveLength(1)
  })

  test('父行标题是「11月 月计划」', () => {
    const rows = collapseCorrect([task(500, 1)])

    expect(rows[0].title).toBe('11月 月计划')
  })

  test('总数 = total - 折叠掉的行数', () => {
    const total = displayTotalCorrect([task(500, 1), task(500, 2), task(500, 3)], 8)

    expect(total).toBe(6)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 错误实现必须与正确实现**可区分**
//    ⇒ 证明挂载测试的判据不是「恒真」
// ═══════════════════════════════════════════════════════════════════════════

describe('错误实现与正确实现可区分', () => {
  test('折叠条件反了 → 行数不同', () => {
    const items = [task(500, 1), task(500, 2), task(500, 3)]

    expect(collapseConditionReversed(items)).toHaveLength(3)
    expect(collapseCorrect(items)).toHaveLength(1)
    // ⚠️ **这才是关键**：两者必须不同 ⇒ 「折叠成 1 行」这条判据有区分力。
    expect(collapseConditionReversed(items).length).not.toBe(collapseCorrect(items).length)
  })

  test('漏掉去重 → 行数不同', () => {
    const items = [task(500, 1), task(500, 2), task(500, 3)]

    expect(collapseWithoutDedupe(items)).toHaveLength(3)
    expect(collapseCorrect(items)).toHaveLength(1)
    expect(collapseWithoutDedupe(items).length).not.toBe(collapseCorrect(items).length)
  })

  test('总数没减折叠行 → 总数不同', () => {
    const items = [task(500, 1), task(500, 2), task(500, 3)]

    expect(displayTotalWithoutCollapseFix(items, 8)).toBe(8)
    expect(displayTotalCorrect(items, 8)).toBe(6)
    expect(displayTotalWithoutCollapseFix(items, 8)).not.toBe(displayTotalCorrect(items, 8))
  })

  test('折叠后父行的 content 是「共 N 项月计划任务」', () => {
    const rows = collapseCorrect([task(500, 1), task(500, 2), task(500, 3)])

    expect(rows[0].content).toBe('共 3 项月计划任务')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 月计划列表：防重复建计划 / 历史月份不可建
// ═══════════════════════════════════════════════════════════════════════════

/** ✅ **正确实现** —— 复制自 ``EmployeeMonthPlanListRefactored.vue``。 */
function canCreateCorrect(currentMonth: string, thisMonth: string): boolean {
  return currentMonth >= thisMonth
}

/** ❌ **错误实现**：忘了「不能早于本月」这道闸。 */
function canCreateWithoutGuard(currentMonth: string, thisMonth: string): boolean {
  void thisMonth
  return true
}

/** ✅ **正确实现** —— ``goCreateSelectedMonth``。 */
function goCreateCorrect(
  selectedPlanId: string | undefined,
  currentMonth: string,
): { kind: 'edit' | 'create'; target: string } {
  if (selectedPlanId) return { kind: 'edit', target: `/edit/${selectedPlanId}` }
  return { kind: 'create', target: `/edit?planMonth=${currentMonth}` }
}

/** ❌ **错误实现**：忘了「已有计划就打开它」⇒ 会重复建计划。 */
function goCreateWithoutGuard(
  selectedPlanId: string | undefined,
  currentMonth: string,
): { kind: 'edit' | 'create'; target: string } {
  return { kind: 'create', target: `/edit?planMonth=${currentMonth}` }
}

describe('月计划列表的判据有效性', () => {
  test('历史月份不可建：正确实现为 false，漏闸的实现为 true', () => {
    const past = '2026-09'
    const now = '2026-11'

    expect(canCreateCorrect(past, now)).toBe(false)
    expect(canCreateWithoutGuard(past, now)).toBe(true)
    // ⚠️ **关键**：两者必须不同 ⇒ 「历史月份不给新建按钮」这条判据有区分力。
    expect(canCreateCorrect(past, now)).not.toBe(canCreateWithoutGuard(past, now))
  })

  test('本月可建：正确实现为 true', () => {
    expect(canCreateCorrect('2026-11', '2026-11')).toBe(true)
  })

  test('未来月份可建：正确实现为 true', () => {
    expect(canCreateCorrect('2026-12', '2026-11')).toBe(true)
  })

  test('已有计划 → 打开它；漏闸的实现 → 会新建', () => {
    const correct = goCreateCorrect('PLAN_42', '2026-11')
    const buggy = goCreateWithoutGuard('PLAN_42', '2026-11')

    expect(correct.kind).toBe('edit')
    expect(correct.target).toContain('PLAN_42')
    // ⚠️ 漏闸的实现会新建出第二份计划 ⇒ 判据有区分力。
    expect(buggy.kind).toBe('create')
    expect(buggy.target).not.toContain('PLAN_42')
  })

  test('没有计划 → 新建；两种实现一致（此处判据无区分力，故不作为有效判据）', () => {
    const correct = goCreateCorrect(undefined, '2026-11')
    const buggy = goCreateWithoutGuard(undefined, '2026-11')

    expect(correct.kind).toBe('create')
    expect(correct).toEqual(buggy)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 周计划任务卡片：字段绑定 / 要点计数
// ═══════════════════════════════════════════════════════════════════════════

interface CardModel {
  content?: string
  deliverable?: string
  quality?: string
  /** ⚠️ 故意拼错的字段名 —— 模拟 ``v-model="model.contents"`` 这类 bug。 */
  contents?: string
}

/** ✅ **正确绑定** —— 三个字段各读各的。 */
function readBoundFieldsCorrect(model: CardModel) {
  return {
    content: model.content ?? '',
    deliverable: model.deliverable ?? '',
    quality: model.quality ?? '',
  }
}

/** ❌ **错误绑定**：内容字段名拼错（``content`` → ``contents``）。 */
function readBoundFieldsTypo(model: CardModel) {
  return {
    content: model.contents ?? '',
    deliverable: model.deliverable ?? '',
    quality: model.quality ?? '',
  }
}

/** ✅ **正确计数** —— 复制自 ``countWeekWorkPoints``。 */
function countPointsCorrect(content: string | undefined): number {
  return String(content || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .length
}

/** ❌ **错误计数**：把空白行也算进去。 */
function countPointsIncludingBlank(content: string | undefined): number {
  return String(content || '').split(/\r?\n/).length
}

describe('周计划任务卡片的判据有效性', () => {
  test('⚠️ 字段名拼错 → 内容框读到的值不同 ⇒ 判据有区分力', () => {
    const model: CardModel = { content: '我的工作内容', contents: undefined }

    expect(readBoundFieldsCorrect(model).content).toBe('我的工作内容')
    expect(readBoundFieldsTypo(model).content).toBe('')
    // ⚠️ **关键**：两者必须不同 ⇒ 「内容框的值 = model.content」这条判据能抓到拼写 bug。
    expect(readBoundFieldsCorrect(model).content).not.toBe(readBoundFieldsTypo(model).content)
  })

  test('⚠️ 空白行计入 → 计数不同 ⇒ 判据有区分力', () => {
    const content = '1. 一件事\n\n   \n2. 两件事'

    expect(countPointsCorrect(content)).toBe(2)
    expect(countPointsIncludingBlank(content)).toBe(4)
    expect(countPointsCorrect(content)).not.toBe(countPointsIncludingBlank(content))
  })

  test('空内容的要点数 = 0', () => {
    expect(countPointsCorrect('')).toBe(0)
    expect(countPointsCorrect(undefined)).toBe(0)
  })
})
