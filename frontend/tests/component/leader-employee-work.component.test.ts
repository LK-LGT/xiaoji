/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 领导查看下属工作记录。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/leader-employee-work-ui.test.ts``）全是「源码里有这个字符串」：
 *
 * ```typescript
 * assert.match(source, /collapseMonthPlanRows/)
 * assert.match(source, /v-if="row\.workType !== 'MONTH_PLAN'"/)
 * assert.match(source, /@click="openMonthPlan\(row\)"/)
 * assert.match(source, /displayTotal/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把折叠逻辑里的 ``if (item.workType !== 'MONTH_PLAN') return [item]``
 *   改成 ``if (item.workType === 'MONTH_PLAN') return [item]`` ——
 *   函数名还在、``v-if`` 还在 ⇒ **旧断言全部照样绿**，
 *   而界面上月计划行会被原样铺开、总数也算错。
 *
 * ⚠️ **本文件真正测的东西**
 *   1. **折叠**：N 条月计划任务 → 1 行父行，标题「X 月计划」、内容「共 N 项月计划任务」；
 *   2. **去重**：同一 ``businessId`` 只保留一行；
 *   3. **总数**：``displayTotal`` 必须减去折叠掉的行数（否则分页错乱）；
 *   4. **操作按钮**：非月计划行**不该**出现「月计划详情」。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'

// ⚠️ **路由 mock** —— 组件用 ``route.path`` 区分领导/部门工作台。
//
// ⚠️ **``params.ownerId`` 是必需的** —— 组件里：
//
//     const ownerId = typeof route.params.ownerId === 'string' ? route.params.ownerId : ''
//     async function loadWork() { if (!ownerId) return; ... }
//
// ⇒ 拿不到 ownerId 就**直接 return**，列表永远是空的。
// ⇒ mock 必须提供它。
const { routePath, routeParams } = vi.hoisted(() => ({
  routePath: { value: '/leader/employee-work' },
  routeParams: { value: { ownerId: 'ou_abc123' } as Record<string, string> },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({
    get path() { return routePath.value },
    query: {},
    get params() { return routeParams.value },
  }),
  useRouter: () => ({ replace: vi.fn(async () => {}) }),
}))

// ⚠️ **工作列表 API** —— 组件挂载后立刻拉数据。
const {
  getLeaderEmployeeWorkApi,
  getDepartmentEmployeeWorkApi,
  getLeaderEmployeeMonthPlanApi,
  getDepartmentEmployeeMonthPlanApi,
} = vi.hoisted(() => ({
  getLeaderEmployeeWorkApi: vi.fn(),
  getDepartmentEmployeeWorkApi: vi.fn(),
  getLeaderEmployeeMonthPlanApi: vi.fn(),
  getDepartmentEmployeeMonthPlanApi: vi.fn(),
}))

vi.mock('@/api/leader', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/leader')>()
  return {
    ...actual,
    getLeaderEmployeeWorkApi: (...a: unknown[]) => getLeaderEmployeeWorkApi(...a),
    getDepartmentEmployeeWorkApi: (...a: unknown[]) => getDepartmentEmployeeWorkApi(...a),
    getLeaderEmployeeMonthPlanApi: (...a: unknown[]) => getLeaderEmployeeMonthPlanApi(...a),
  }
})

vi.mock('@/api/department', () => ({
  getDepartmentEmployeeWorkApi: (...a: unknown[]) => getDepartmentEmployeeWorkApi(...a),
  getDepartmentEmployeeMonthPlanApi: (...a: unknown[]) => getDepartmentEmployeeMonthPlanApi(...a),
}))

import LeaderEmployeeWork from '../../src/views/leader/LeaderEmployeeWork.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

function item(overrides: Record<string, unknown> = {}) {
  return {
    rowId: 'ROW_1',
    workType: 'WEEK_PLAN',
    businessId: 900,
    itemId: 100,
    title: '周计划条目',
    content: '周计划正文',
    deliverable: '',
    remark: '',
    quality: '',
    periodLabel: '11月',
    status: 'PENDING',
    relatedPlanTitle: '',
    leaderComment: '',
    ...overrides,
  }
}

/** 一条月计划**任务**行（itemId 有值 ⇒ 会被折叠成父行）。 */
function monthPlanTask(businessId: number, itemId: number, overrides: Record<string, unknown> = {}) {
  return item({
    rowId: `ROW_${itemId}`,
    workType: 'MONTH_PLAN',
    businessId,
    itemId,
    title: '月计划任务',
    content: '任务正文',
    ...overrides,
  })
}

async function mountWith(items: unknown[], total?: number) {
  getLeaderEmployeeWorkApi.mockResolvedValue({
    employee: {
      employeeId: 'E_1',
      employeeName: '李婵雨',
      weekPlanCount: 2,
      dayPlanCount: 3,
      resultCount: 4,
    },
    items,
    // ⚠️ 默认 total = items.length —— 但月计划折叠场景下要用更大的 total。
    total: total ?? items.length,
    pageNo: 1,
    pageSize: 20,
  })

  const wrapper = mount(LeaderEmployeeWork)
  await nextTick()
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
  return wrapper
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 月计划折叠
// ═══════════════════════════════════════════════════════════════════════════

describe('月计划任务折叠成一行父行', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/leader/employee-work'
  })

  test('3 条月计划任务 → 只渲染 1 行', async () => {
    const wrapper = await mountWith([
      monthPlanTask(500, 1),
      monthPlanTask(500, 2),
      monthPlanTask(500, 3),
    ])

    // ⚠️ el-table 的行数 —— 用组件自身的表格行。
    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(1)
  })

  test('父行标题是「X 月计划」', async () => {
    const wrapper = await mountWith([monthPlanTask(500, 1, { periodLabel: '11月' })])

    expect(wrapper.text()).toContain('11月 月计划')
  })

  test('折叠后只渲染 1 行，且显示月计划类型标签', async () => {
    // ⚠️ **可见行为** —— 折叠逻辑把 ``content`` 改成了「共 N 项月计划任务」，
    //    但模板里 content 被 ``v-if="row.workType !== 'MONTH_PLAN'"`` 挡掉了
    //    ⇒ 用户看到的是**类型标签「月计划」+ 标题「11月 月计划」**，
    //    看不到「共 N 项」那句。
    //    ⇒ 所以断言要测**看得见的东西**，不是数据里的字符串。
    const wrapper = await mountWith([
      monthPlanTask(500, 1),
      monthPlanTask(500, 2),
      monthPlanTask(500, 3),
    ])

    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(1)
    // ⚠️ 类型列的标签。
    expect(rows[0].text()).toContain('月计划')
  })

  test('不同 businessId 的月计划**各占一行**', async () => {
    const wrapper = await mountWith([
      monthPlanTask(500, 1),
      monthPlanTask(500, 2),
      monthPlanTask(600, 3),
    ])

    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(2)
  })

  test('非月计划行原样保留', async () => {
    const wrapper = await mountWith([
      item({ rowId: 'ROW_W', workType: 'WEEK_PLAN', title: '我的周计划条目' }),
      monthPlanTask(500, 1),
    ])

    expect(wrapper.text()).toContain('我的周计划条目')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 总数（displayTotal）
// ═══════════════════════════════════════════════════════════════════════════

describe('总条数要减去折叠掉的行', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/leader/employee-work'
  })

  test('本页 3 条月计划折叠成 1 行，total=8 → 显示 6 条', async () => {
    // ⚠️ **核心语义**：折叠掉的 2 行不该继续占分页名额。
    //    若不减，总数会虚高 → 用户翻到最后一页是空的。
    const wrapper = await mountWith(
      [monthPlanTask(500, 1), monthPlanTask(500, 2), monthPlanTask(500, 3)],
      8,
    )

    expect(wrapper.text()).toContain('共 6 条记录')
  })

  test('没有折叠时总数就是 total', async () => {
    const wrapper = await mountWith(
      [item({ rowId: 'R1' }), item({ rowId: 'R2' })],
      8,
    )

    expect(wrapper.text()).toContain('共 8 条记录')
  })

  test('折叠后本行数不少于折叠后的实际行数', async () => {
    // ⚠️ **下限护栏**：Math.max 的意义 —— 折叠后行数不该被 total 减过头。
    const wrapper = await mountWith([monthPlanTask(500, 1), monthPlanTask(500, 2)], 2)

    expect(wrapper.text()).toContain('共 1 条记录')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 操作按钮的条件渲染
// ═══════════════════════════════════════════════════════════════════════════

describe('操作列与内容按工作类型区分', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/leader/employee-work'
  })

  test('月计划行有「查看」按钮', async () => {
    const wrapper = await mountWith([monthPlanTask(500, 1)])

    // ⚠️ 「查看」是**操作列**的按钮 —— 不是抽屉标题「月计划详情」
    //    （那个是 el-drawer 的 header，永远存在）。
    expect(wrapper.find('.empty-action').exists()).toBe(false)
    expect(wrapper.text()).toContain('查看')
  })

  test('非月计划行操作列显示「-」而不是「查看」', async () => {
    // ⚠️ **否定式断言** —— 条件写反时（``=== 'MONTH_PLAN'`` 写成 ``!== 'WEEK_PLAN'``）
    //    这里会红，而源码断言不会。
    const wrapper = await mountWith([item({ rowId: 'ROW_W', workType: 'WEEK_PLAN' })])

    expect(wrapper.find('.empty-action').exists()).toBe(true)
    expect(wrapper.find('.empty-action').text()).toBe('-')
  })

  test('非月计划行显示原始内容', async () => {
    const wrapper = await mountWith([
      item({ rowId: 'ROW_W', workType: 'WEEK_PLAN', content: '这周要把剪辑做完' }),
    ])

    expect(wrapper.text()).toContain('这周要把剪辑做完')
  })

  test('月计划父行**不**显示原始任务内容', async () => {
    // ⚠️ **核心条件渲染**：``<p v-if="row.workType !== 'MONTH_PLAN'">{{ row.content }}</p>``
    //    月计划折叠后内容已被换成「共 N 项月计划任务」，
    //    再显示原始正文就是重复且误导。
    const wrapper = await mountWith([
      monthPlanTask(500, 1, { content: '这是月计划任务的原始正文' }),
    ])

    expect(wrapper.text()).not.toContain('这是月计划任务的原始正文')
    // ⚠️ 月计划行的内容区是空的 —— 只留类型标签 + 标题。
    expect(wrapper.findAll('.el-table__row')[0].text()).toContain('月计划')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 路由分支
// ═══════════════════════════════════════════════════════════════════════════

describe('按当前工作台调不同接口', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('领导工作台 → getLeaderEmployeeWorkApi', async () => {
    routePath.value = '/leader/employee-work'
    await mountWith([])

    expect(getLeaderEmployeeWorkApi).toHaveBeenCalled()
    expect(getDepartmentEmployeeWorkApi).not.toHaveBeenCalled()
  })

  test('部门工作台 → getDepartmentEmployeeWorkApi', async () => {
    routePath.value = '/department/employee-work'
    await mountWith([])

    expect(getDepartmentEmployeeWorkApi).toHaveBeenCalled()
    expect(getLeaderEmployeeWorkApi).not.toHaveBeenCalled()
  })
})
