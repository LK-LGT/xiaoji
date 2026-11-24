/**
 * 🔴 **组件测试示范**（CI-5 阶段 1）—— 周计划审批列表。
 *
 * ⚠️ **这个文件想证明什么**
 * ------------------------------------------------------------------
 * 同一个组件（``WeekPlanApproval.vue``），本项目已有一个「读源码断言」测试：
 * ``tests/approval-long-text-ui.test.ts``。它断言的是：
 *
 * ```typescript
 * assert.match(weekApproval, /white-space:\s*pre-wrap/)
 * assert.match(weekApproval, /overflow-wrap:\s*anywhere/)
 * ```
 *
 * ⇒ 那验证的是「**源码里有这个 CSS 字符串**」。
 *
 * 本文件验证的是「**组件真的渲染成了那个样子**」。
 * 两者能抓到的缺陷集合**完全不同**（见下方对照表）。
 *
 * | 缺陷类型 | 读源码断言 | 本文件（挂载） |
 * |---|---|---|
 * | 审批按钮的条件渲染写反了 | ❌ 抓不到 | ✅ 抓到 |
 * | 路由分支判断写反了 | ❌ 抓不到 | ✅ 抓到 |
 * | 批量审批的失败计数算错 | ❌ 抓不到 | ✅ 抓到 |
 * | CSS 换了个类名 | 🔴 **误报** | ✅ 不受影响 |
 * | 样式被误删 | ✅ 抓到 | ❌ 抓不到（jsdom 不做布局） |
 *
 * ⚠️ **注意最后一行** —— 两种方式**互补**，不是替代。
 * 样式类约定仍由 ``tests/approval-long-text-ui.test.ts`` 守着；
 * 本文件只管**行为**。
 *
 * ⚠️ **为什么用「审批按钮」当第一个示范**
 *   它是本组件最容易出错的逻辑：``v-if="row.status === 'PENDING'``。
 *   一旦有人把 ``PENDING`` 写成 ``APPROVED``，
 *   源码断言**一个都不会红**，而用户会看到「已通过的没法审批」。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'

// ⚠️ **必须 mock 路由** —— 组件用 ``useRoute()`` 判断自己在
//    领导工作台还是部门工作台（``route.path.startsWith('/department/')``）。
//    不 mock 的话 ``useRoute()`` 会抛 "no active router"。
//
// ⚠️ **``vi.hoisted`` 是必须的** —— ``vi.mock`` 的工厂会被**提升**到文件顶部，
//    ⇒ 工厂里不能引用普通顶层变量（会读到 undefined）。
//    ⇒ 用 ``vi.hoisted`` 把变量一起提升，才能在工厂里引用。
const { routePath } = vi.hoisted(() => ({ routePath: { value: '/leader/workbench' } }))

vi.mock('vue-router', () => ({
  // ⚠️ **``query`` 也要 mock** —— 组件的 ``onMounted`` 里会读
  //    ``route.query.id``（深链接到某条周计划）。
  //    缺了它 ⇒ "Cannot read properties of undefined (reading 'id')"。
  useRoute: () => ({
    get path() { return routePath.value },
    query: {},
    params: {},
  }),
}))

// ⚠️ **必须 mock API** —— 组件挂载后 ``onMounted`` 会真的去拉列表。
//    同上，用 ``vi.hoisted`` 包裹，避免提升问题。
const {
  listLeaderWeekPlansApi,
  listDepartmentWeekPlanApprovalsApi,
  approveLeaderWeekPlanApi,
  rejectLeaderWeekPlanApi,
  getLeaderWeekPlanApi,
  getDepartmentWeekPlanApprovalApi,
} = vi.hoisted(() => ({
  listLeaderWeekPlansApi: vi.fn(),
  listDepartmentWeekPlanApprovalsApi: vi.fn(),
  approveLeaderWeekPlanApi: vi.fn(),
  rejectLeaderWeekPlanApi: vi.fn(),
  getLeaderWeekPlanApi: vi.fn(),
  getDepartmentWeekPlanApprovalApi: vi.fn(),
}))

vi.mock('@/api/weekPlan', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/weekPlan')>()
  return {
    ...actual,
    listLeaderWeekPlansApi: (...args: unknown[]) => listLeaderWeekPlansApi(...args),
    listDepartmentWeekPlanApprovalsApi: (...args: unknown[]) =>
      listDepartmentWeekPlanApprovalsApi(...args),
    approveLeaderWeekPlanApi: (...args: unknown[]) => approveLeaderWeekPlanApi(...args),
    rejectLeaderWeekPlanApi: (...args: unknown[]) => rejectLeaderWeekPlanApi(...args),
    getLeaderWeekPlanApi: (...args: unknown[]) => getLeaderWeekPlanApi(...args),
    getDepartmentWeekPlanApprovalApi: (...args: unknown[]) =>
      getDepartmentWeekPlanApprovalApi(...args),
  }
})

// ⚠️ AI 评审 composable 会自己发请求 —— 一并 stub 掉。
vi.mock('@/composables/useAiReviewCollection', () => ({
  useAiReviewCollection: () => ({
    reviewFor: () => null,
    loading: { value: false },
    load: vi.fn(async () => {}),
    loadOne: vi.fn(async () => {}),
  }),
}))

import WeekPlanApproval from '../../src/views/leader/WeekPlanApproval.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 测试夹具
// ═══════════════════════════════════════════════════════════════════════════

function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: 'WP_1',
    title: '第 1 周计划',
    weekStart: '2026-11-16',
    weekEnd: '2026-11-22',
    status: 'PENDING',
    versionNo: 1,
    ownerUserId: 1001,
    employeeName: '李婵雨',
    departmentName: '短视频部',
    itemCount: 3,
    ...overrides,
  }
}

async function mountApproval(rows: unknown[] = [summary()]) {
  listLeaderWeekPlansApi.mockResolvedValue(rows)
  listDepartmentWeekPlanApprovalsApi.mockResolvedValue(rows)

  const wrapper = mount(WeekPlanApproval)
  // ⚠️ **必须等异步** —— 列表是 ``onMounted`` 里 await 出来的。
  await nextTick()
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
  return wrapper
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 审批按钮的条件渲染
// ═══════════════════════════════════════════════════════════════════════════

describe('审批按钮只出现在待审批行', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routePath.value = '/leader/workbench'
  })

  test('PENDING 行有「通过」和「驳回」', async () => {
    const wrapper = await mountApproval([summary({ status: 'PENDING' })])

    const buttons = wrapper.findAll('button')
    const labels = buttons.map((b) => b.text())
    expect(labels).toContain('通过')
    expect(labels).toContain('驳回')
  })

  test('APPROVED 行**没有**「通过」和「驳回」', async () => {
    // ⚠️ 这条断言是「否定式断言」—— 若条件写反了（比如写成
    //    ``v-if="row.status === 'APPROVED'"``），这里会红。
    const wrapper = await mountApproval([summary({ status: 'APPROVED' })])

    const labels = wrapper.findAll('button').map((b) => b.text())
    expect(labels).not.toContain('通过')
    expect(labels).not.toContain('驳回')
    // 但「查看详情」始终在 —— 证明不是整个表格没渲染。
    expect(labels).toContain('查看详情')
  })

  test('REJECTED 行同样没有审批按钮', async () => {
    const wrapper = await mountApproval([summary({ status: 'REJECTED' })])

    const labels = wrapper.findAll('button').map((b) => b.text())
    expect(labels).not.toContain('通过')
    expect(labels).not.toContain('驳回')
    expect(labels).toContain('查看详情')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 路由分支 —— 同一个组件在不同工作台走不同 API 与不同标签
// ═══════════════════════════════════════════════════════════════════════════

describe('按当前工作台走不同数据源', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('领导工作台：调 listLeaderWeekPlansApi', async () => {
    routePath.value = '/leader/workbench'
    await mountApproval([summary()])

    expect(listLeaderWeekPlansApi).toHaveBeenCalled()
    expect(listDepartmentWeekPlanApprovalsApi).not.toHaveBeenCalled()
  })

  test('部门工作台：调 listDepartmentWeekPlanApprovalsApi', async () => {
    // ⚠️ 这条断言是**互斥**的 —— 它锁定「两个 API 不能同时调」。
    routePath.value = '/department/workbench'
    await mountApproval([summary()])

    expect(listDepartmentWeekPlanApprovalsApi).toHaveBeenCalled()
    expect(listLeaderWeekPlansApi).not.toHaveBeenCalled()
  })
})
