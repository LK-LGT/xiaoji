/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 员工月计划列表（月份导航 + 新建入口）。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/employee-month-navigation-ui.test.ts``）：
 *
 * ```typescript
 * assert.doesNotMatch(source, /v-if="monthCards\.length"/)
 * assert.match(source, /@click="goCreateSelectedMonth"/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   ① ``doesNotMatch(/v-if="monthCards\.length"/)`` 只证明「源码里没有**这一串字**」。
 *      改成 ``v-if="monthCards.length > 0"`` ⇒ **照样通过**，
 *      而「没有计划的月份不再显示卡片」这个 bug 已经上线。
 *   ② ``@click="goCreateSelectedMonth"`` 只证明「有个按钮绑了这个函数」，
 *      完全没验证「**点了会不会重复建计划**」——
 *      而这恰恰是数据一致性的关键。
 *
 * ⚠️ **本文件真正测的东西**（三条产品级规则）
 *   1. **7 个月份卡片都要渲染** —— 哪怕该月没有计划（无计划的月份是空卡片，不是消失）；
 *   2. **主按钮文案随状态变** —— 有计划→「打开月计划」，无计划→「新建月计划」；
 *   3. **已有计划时点新建 → 打开已有计划，不重复创建**（防重复建计划）；
 *   4. **历史月份不能新建** —— `canCreateSelectedMonth` = 当前月份 ≥ 本月。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'

// ⚠️ **路由 mock** —— 组件用 ``route.query.month`` 读初始月份、用 ``route.path`` 判上下文。
const { routePath, routeQuery, routerPush } = vi.hoisted(() => ({
  routePath: { value: '/employee/month-plans' },
  routeQuery: { value: {} as Record<string, string> },
  routerPush: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({
    get path() { return routePath.value },
    get query() { return routeQuery.value },
    params: {},
  }),
  useRouter: () => ({ push: routerPush, replace: vi.fn(async () => {}) }),
}))

// ⚠️ **计划列表 API** —— 组件挂载后立刻拉。
const { listMonthPlansApi, getEmployeeMonthPlanDetailApi, getEmployeeResultSubmitOptionsApi, withdrawEmployeeMonthPlanApi } =
  vi.hoisted(() => ({
    listMonthPlansApi: vi.fn(),
    getEmployeeMonthPlanDetailApi: vi.fn(),
    getEmployeeResultSubmitOptionsApi: vi.fn(),
    withdrawEmployeeMonthPlanApi: vi.fn(),
  }))

vi.mock('@/api/planning', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/planning')>()
  return {
    ...actual,
    listMonthPlansApi: (...a: unknown[]) => listMonthPlansApi(...a),
  }
})

vi.mock('@/api/employee', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/employee')>()
  return {
    ...actual,
    getEmployeeMonthPlanDetailApi: (...a: unknown[]) => getEmployeeMonthPlanDetailApi(...a),
    getEmployeeResultSubmitOptionsApi: (...a: unknown[]) => getEmployeeResultSubmitOptionsApi(...a),
    withdrawEmployeeMonthPlanApi: (...a: unknown[]) => withdrawEmployeeMonthPlanApi(...a),
  }
})

// ⚠️ **AI 评审集合** —— 组件挂载后会 ``await aiReviews.load(...)``。
vi.mock('@/composables/useAiReviewCollection', () => ({
  useAiReviewCollection: () => ({
    load: vi.fn(async () => {}),
    loadOne: vi.fn(async () => {}),
    reviewFor: () => null,
    loading: { value: false },
  }),
}))

import EmployeeMonthPlanList from '../../src/views/employee/EmployeeMonthPlanListRefactored.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

/** 本月（组件用 ``currentMonth()`` 取真实当前月，所以夹具要对齐真实月份）。 */
function thisMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function plan(overrides: Record<string, unknown> = {}) {
  return {
    id: 'PLAN_1',
    planMonth: thisMonth(),
    status: 'DRAFT',
    employeeName: '李婵雨',
    departmentName: '剪辑组',
    ...overrides,
  }
}

async function mountWith(plans: unknown[]) {
  listMonthPlansApi.mockResolvedValue(plans)
  // ⚠️ **详情字段要补全** —— 模板里直接解引用：
  //    ``getPlanStatus(selectedPlanDetail.status).type``、
  //    ``getResultStatus(selectedPlanDetail.resultStatus).type``、
  //    ``selectedPlanDetail.resultSummary.overallCompletionRate``
  //    ⇒ 缺一个就是渲染期 TypeError。
  getEmployeeMonthPlanDetailApi.mockResolvedValue({
    id: 'PLAN_1',
    planMonth: thisMonth(),
    title: '11月 月计划',
    // ⚠️ **必须是小写** —— ``EmployeeMonthPlanDetailResp.status`` 的类型是
    //    ``EmployeePlanStatus``（'draft' | 'submitted' | ...），
    //    而 ``getPlanStatus()`` 查的 ``planStatusMap`` 键也全是小写。
    //    传大写 'DRAFT' ⇒ ``planStatusMap['DRAFT']`` 是 undefined
    //    ⇒ 模板 ``getPlanStatus(...).type`` 直接抛 TypeError。
    status: 'draft',
    resultStatus: 'not_submitted',
    resultSummary: { overallCompletionRate: 0 },
    employeeName: '李婵雨',
    departmentName: '剪辑组',
    items: [],
  })
  getEmployeeResultSubmitOptionsApi.mockResolvedValue({ resultVersions: [] })

  const wrapper = mount(EmployeeMonthPlanList)
  // ⚠️ onMounted → loadAll 是 async，且内部有 await 链。
  await nextTick()
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
  await nextTick()
  return wrapper
}

/** 月份卡片。 */
function monthCards(wrapper: Awaited<ReturnType<typeof mountWith>>) {
  return wrapper.findAll('.employee-month-card')
}

/** 主操作按钮（顶部工具栏里那个 primary 按钮）。 */
function primaryButtons(wrapper: Awaited<ReturnType<typeof mountWith>>) {
  return wrapper.findAll('button').filter((b) => b.text().includes('月计划'))
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 月份导航 —— 7 张卡片，空月份也要显示
// ═══════════════════════════════════════════════════════════════════════════

describe('月份导航', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/employee/month-plans'
    routeQuery.value = {}
  })

  test('渲染 7 张月份卡片（前 3 + 本月 + 后 3）', async () => {
    const wrapper = await mountWith([])

    expect(monthCards(wrapper)).toHaveLength(7)
  })

  test('⚠️ 没有任何计划时，月份卡片**依然要显示**（空卡片，不是消失）', async () => {
    // ⚠️ **核心规则**：没有计划的月份也要能被看到、能被点到 ——
    //    否则员工看不到「我该建哪个月的计划」。
    //    ⇒ 组件里刻意**没有** ``v-if="monthCards.length"``。
    const wrapper = await mountWith([])

    const cards = monthCards(wrapper)
    expect(cards).toHaveLength(7)
    // ⚠️ 无计划的月份带 ``is-empty`` 类（灰显），而不是不渲染。
    expect(cards.filter((c) => c.classes().includes('is-empty')).length).toBeGreaterThan(0)
  })

  test('有计划时，对应月份卡片**不带** is-empty', async () => {
    const wrapper = await mountWith([plan()])

    // ⚠️ **用类名定位本月卡片**，不要按文本找「2026-11」——
    //    卡片上渲染的是月份数字（periodMonthNumber）和年份，不是 YYYY-MM。
    const current = monthCards(wrapper).find((c) => c.classes().includes('is-current'))
    expect(current).toBeDefined()
    expect(current!.classes()).not.toContain('is-empty')
  })

  test('CANCELED 的计划不算「有计划」', async () => {
    // ⚠️ 撤销的计划不该让卡片显示成「已有计划」。
    const wrapper = await mountWith([plan({ status: 'CANCELED' })])

    const current = monthCards(wrapper).find((c) => c.classes().includes('is-current'))
    expect(current).toBeDefined()
    expect(current!.classes()).toContain('is-empty')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 主按钮文案随状态变
// ═══════════════════════════════════════════════════════════════════════════

describe('主操作按钮的文案', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/employee/month-plans'
    routeQuery.value = {}
  })

  test('本月没有计划 → 「新建月计划」', async () => {
    const wrapper = await mountWith([])

    expect(wrapper.text()).toContain('新建月计划')
    expect(wrapper.text()).not.toContain('打开月计划')
  })

  test('本月已有计划 → 「打开月计划」', async () => {
    // ⚠️ **文案即语义** —— 「新建」会让人以为会多建一份。
    const wrapper = await mountWith([plan()])

    expect(wrapper.text()).toContain('打开月计划')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 防重复建计划
// ═══════════════════════════════════════════════════════════════════════════

describe('已有计划时点主操作 → 打开已有计划，不重复创建', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/employee/month-plans'
    routeQuery.value = {}
  })

  test('已有计划 → 跳转到该计划的编辑页（带 planId）', async () => {
    const wrapper = await mountWith([plan({ id: 'PLAN_42' })])

    const btn = primaryButtons(wrapper).find((b) => b.text().includes('打开月计划'))
    expect(btn).toBeDefined()
    await btn!.trigger('click')
    await nextTick()

    // ⚠️ **关键**：跳的是**已有计划**（带 id），不是「新建」。
    expect(routerPush).toHaveBeenCalled()
    const arg = routerPush.mock.calls[0][0]
    const path = typeof arg === 'string' ? arg : arg.path
    expect(path).toContain('PLAN_42')
  })

  test('没有计划 → 跳转到新建页（带 planMonth，不带 planId）', async () => {
    const wrapper = await mountWith([])

    const btn = primaryButtons(wrapper).find((b) => b.text().includes('新建月计划'))
    expect(btn).toBeDefined()
    await btn!.trigger('click')
    await nextTick()

    expect(routerPush).toHaveBeenCalled()
    const arg = routerPush.mock.calls[0][0]
    const path = typeof arg === 'string' ? arg : arg.path
    expect(path).toContain('/edit')
    expect(path).not.toContain('PLAN_')
    // ⚠️ 新建时带上目标月份 —— 否则会建到「当前月」而不是用户选的那个月。
    expect(arg.query?.planMonth).toBe(thisMonth())
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 历史月份不能新建
// ═══════════════════════════════════════════════════════════════════════════

describe('历史月份不允许新建', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/employee/month-plans'
  })

  test('切到历史月份 → 空态区不给「新建月计划」按钮', async () => {
    // ⚠️ 历史月份没有计划可建（也不能补建）——
    //    组件用 ``canCreateSelectedMonth = currentMonth >= getCurrentMonth()`` 判定。
    const past = (() => {
      const d = new Date()
      d.setMonth(d.getMonth() - 2)
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    })()
    routeQuery.value = { month: past }

    const wrapper = await mountWith([])

    // ⚠️ 空态区提示语要区分「可建」和「不可建」。
    expect(wrapper.text()).toContain('历史月份没有可查看的月计划记录')
    expect(wrapper.text()).not.toContain('创建月计划后，任务和成果提交情况将在这里显示')
  })

  test('切到本月 → 空态区**给**「新建月计划」按钮', async () => {
    routeQuery.value = { month: thisMonth() }

    const wrapper = await mountWith([])

    expect(wrapper.text()).toContain('创建月计划后，任务和成果提交情况将在这里显示')
    expect(wrapper.findAll('button').some((b) => b.text().trim() === '新建月计划')).toBe(true)
  })
})
