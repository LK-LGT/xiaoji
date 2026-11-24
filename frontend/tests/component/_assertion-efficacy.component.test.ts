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
