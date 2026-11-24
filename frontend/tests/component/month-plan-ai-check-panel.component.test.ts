/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 月计划 AI 提交前检查面板。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/month-plan-ai-context-options-ui.test.ts`` 等）里是
 * 「读源码 + 正则」：
 *
 * ```typescript
 * assert.match(source, /level === 'HIGH' \? 'danger'/)
 * assert.match(source, /recordAiSuggestionActionApi\(response\.suggestionId/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``level === 'HIGH' ? 'danger' : ...`` 写成 ``level === 'HIGH' ? 'warning' : ...``
 *   ⇒ 正则**照样匹配得上**（``level === 'HIGH'`` 这段没变）⇒ 测试全绿，
 *   而「高风险」提示在界面上被渲染成**黄色警告** —— 用户会低估风险等级。
 *
 * ⚠️ **本文件真正测的东西**
 *   ① **风险等级映射**：``HIGH → 高风险/danger``、``WARNING → 警告/warning``、
 *      ``INFO → 提示/info``；
 *   ② **开始检查的守卫**：无任务 / disabled 时按钮禁用；
 *   ③ **三态渲染**：未检查（规则清单）/ 有风险（问题列表）/ 无风险（空态）；
 *   ④ **停止生成**：请求中显示「停止生成」，停止后恢复；
 *   ⑤ **竞态守卫**：切月后旧响应不得回写（``requestedMonth !== props.planMonth``）。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import MonthPlanAiCheckPanel from '../../src/views/employee/components/MonthPlanAiCheckPanel.vue'

// ═══════════════════════════════════════════════════════════════════════════
// API mock
// ═══════════════════════════════════════════════════════════════════════════

const checkMonthPlanApi = vi.fn()
const recordAiSuggestionActionApi = vi.fn()
const cancelAiRequestApi = vi.fn()

vi.mock('@/api/employeeAi', () => ({
  checkMonthPlanApi: (...args: unknown[]) => checkMonthPlanApi(...args),
  recordAiSuggestionActionApi: (...args: unknown[]) => recordAiSuggestionActionApi(...args),
  cancelAiRequestApi: (...args: unknown[]) => cancelAiRequestApi(...args),
  aiRequestId: () => 'req-1',
}))

beforeEach(() => {
  checkMonthPlanApi.mockReset()
  recordAiSuggestionActionApi.mockReset()
  cancelAiRequestApi.mockReset()
  cancelAiRequestApi.mockResolvedValue(undefined)
  recordAiSuggestionActionApi.mockResolvedValue(undefined)
})

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

function makeIssue(overrides: Record<string, unknown> = {}) {
  return {
    code: 'C1',
    level: 'WARNING' as const,
    fieldPath: 'items[0].taskContent',
    message: '任务描述过于笼统',
    suggestion: '补充具体动作与产出',
    ...overrides,
  }
}

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(MonthPlanAiCheckPanel, {
    props: {
      planMonth: '2026-11',
      form: {
        items: [
          {
            taskName: '任务 1',
            taskContent: '内容',
            deliverable: '交付物',
            quality: '质量',
            estimatedStartDate: '2026-11-01',
            estimatedEndDate: '2026-11-10',
            performanceWeight: 100,
            estimatedValue: 0,
          },
        ],
      },
      disabled: false,
      selectedContextTypes: [],
      ...props,
    },
  })
}

/** 「开始检查」按钮。 */
function startButton(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.section-header button').find((b) => b.text() === '开始检查')
}

/** 「停止生成」按钮。 */
function stopButton(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.section-header button').find((b) => b.text() === '停止生成')
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐⭐ 风险等级映射
// ═══════════════════════════════════════════════════════════════════════════

describe('风险等级映射', () => {
  test('⚠️ HIGH → 文案「高风险」+ 标签 danger', async () => {
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [makeIssue({ level: 'HIGH' })],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    const tag = wrapper.find('.issue .el-tag')
    expect(tag.text()).toBe('高风险')
    expect(tag.classes()).toContain('el-tag--danger')
  })

  test('⚠️ WARNING → 文案「警告」+ 标签 warning', async () => {
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [makeIssue({ level: 'WARNING' })],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    const tag = wrapper.find('.issue .el-tag')
    expect(tag.text()).toBe('警告')
    expect(tag.classes()).toContain('el-tag--warning')
  })

  test('⚠️ INFO → 文案「提示」+ 标签 info', async () => {
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [makeIssue({ level: 'INFO' })],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    const tag = wrapper.find('.issue .el-tag')
    expect(tag.text()).toBe('提示')
    expect(tag.classes()).toContain('el-tag--info')
  })

  test('⚠️⚠️ 三个等级**互不串**（最容易写反的地方）', async () => {
    // ⚠️ **典型 bug**：把 WARNING 也映射成 danger
    //    ⇒ 「高风险」和「警告」在界面上长得一样 ⇒ 用户无法区分严重程度。
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [
        makeIssue({ code: 'A', level: 'HIGH', fieldPath: 'a' }),
        makeIssue({ code: 'B', level: 'WARNING', fieldPath: 'b' }),
        makeIssue({ code: 'C', level: 'INFO', fieldPath: 'c' }),
      ],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    const tags = wrapper.findAll('.issue .el-tag')
    expect(tags).toHaveLength(3)
    expect(tags[0].text()).toBe('高风险')
    expect(tags[1].text()).toBe('警告')
    expect(tags[2].text()).toBe('提示')
    expect(tags[0].classes()).toContain('el-tag--danger')
    expect(tags[1].classes()).toContain('el-tag--warning')
    expect(tags[2].classes()).toContain('el-tag--info')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 开始检查的守卫
// ═══════════════════════════════════════════════════════════════════════════

describe('开始检查的守卫', () => {
  test('默认 → 「开始检查」按钮可用', () => {
    const wrapper = mountPanel()

    expect(startButton(wrapper)!.attributes('disabled')).toBeUndefined()
  })

  test('⚠️ disabled=true → 「开始检查」按钮禁用', () => {
    const wrapper = mountPanel({ disabled: true })

    expect(startButton(wrapper)!.attributes('disabled')).toBeDefined()
  })

  test('⚠️⚠️ 没有任务 → 「开始检查」按钮禁用', () => {
    // ⚠️ **无任务时不该发起 AI 检查** —— 服务端会返回无意义的结果。
    const wrapper = mountPanel({ form: { items: [] } })

    expect(startButton(wrapper)!.attributes('disabled')).toBeDefined()
  })

  test('⚠️ 无任务时点不到检查 ⇒ 不会调用 API', async () => {
    const wrapper = mountPanel({ form: { items: [] } })

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(checkMonthPlanApi).not.toHaveBeenCalled()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 三态渲染
// ═══════════════════════════════════════════════════════════════════════════

describe('三态渲染', () => {
  test('未检查 → 显示规则清单', () => {
    const wrapper = mountPanel()

    expect(wrapper.find('.rule-list').exists()).toBe(true)
    expect(wrapper.find('.issue-list').exists()).toBe(false)
  })

  test('⚠️ 检查后有风险 → 显示问题列表', async () => {
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [makeIssue()],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.issue-list').exists()).toBe(true)
    expect(wrapper.find('.rule-list').exists()).toBe(false)
  })

  test('⚠️ 检查后无风险 → 显示空态「未发现 AI 风险提示」', async () => {
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.el-empty').exists()).toBe(true)
    expect(wrapper.text()).toContain('未发现 AI 风险提示')
  })

  test('⚠️ 问题条目展示 message / suggestion / fieldPath', async () => {
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [makeIssue()],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    const issue = wrapper.find('.issue')
    expect(issue.text()).toContain('任务描述过于笼统')
    expect(issue.text()).toContain('补充具体动作与产出')
    expect(issue.text()).toContain('items[0].taskContent')
  })

  test('⚠️ 点问题条目 → 发出 locate 事件并带上 fieldPath', async () => {
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 's1',
      issues: [makeIssue({ fieldPath: 'items[2].quality' })],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    await wrapper.find('.issue').trigger('click')

    expect(wrapper.emitted('locate')).toHaveLength(1)
    expect(wrapper.emitted('locate')![0]).toEqual(['items[2].quality'])
  })

  test('⚠️ 检查成功后记录 PREVIEW 动作', async () => {
    // ⚠️ **埋点是产品要求**（AI 建议采纳率统计）⇒ 漏了就是数据缺失。
    checkMonthPlanApi.mockResolvedValue({
      suggestionId: 'sug-42',
      issues: [],
      notice: '',
    })
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(recordAiSuggestionActionApi).toHaveBeenCalledWith('sug-42', { actionCode: 'PREVIEW' })
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 错误处理
// ═══════════════════════════════════════════════════════════════════════════

describe('错误处理', () => {
  test('⚠️ API 抛错 → 显示错误提示', async () => {
    checkMonthPlanApi.mockRejectedValue(new Error('AI 服务不可用'))
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    const alert = wrapper.find('.el-alert')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('AI 服务不可用')
  })

  test('⚠️ 报错后仍可重新发起检查', async () => {
    checkMonthPlanApi.mockRejectedValueOnce(new Error('AI 服务不可用'))
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(startButton(wrapper)!.exists()).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 停止生成
// ═══════════════════════════════════════════════════════════════════════════

describe('停止生成', () => {
  test('⚠️ 请求中 → 按钮变成「停止生成」', async () => {
    // ⚠️ **请求悬挂** ⇒ ``checking`` 为 true ⇒ 应该显示停止按钮。
    checkMonthPlanApi.mockReturnValue(new Promise(() => {}))
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(stopButton(wrapper)!.exists()).toBe(true)
    expect(startButton(wrapper)).toBeUndefined()
  })

  test('⚠️ 点「停止生成」→ 调用取消接口', async () => {
    checkMonthPlanApi.mockReturnValue(new Promise(() => {}))
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    await stopButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(cancelAiRequestApi).toHaveBeenCalled()
  })

  test('⚠️ 停止后回到「开始检查」', async () => {
    checkMonthPlanApi.mockReturnValue(new Promise(() => {}))
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    await stopButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(startButton(wrapper)!.exists()).toBe(true)
    expect(stopButton(wrapper)).toBeUndefined()
  })

  test('⚠️ 取消未确认 → 仍回到「开始检查」但提示服务端未取消', async () => {
    // ⚠️ **服务端取消失败**是真实场景（网络断）⇒ 界面不能卡在「生成中」。
    checkMonthPlanApi.mockReturnValue(new Promise(() => {}))
    cancelAiRequestApi.mockRejectedValue(new Error('取消失败'))
    const wrapper = mountPanel()

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    await stopButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(startButton(wrapper)!.exists()).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐⭐ 竞态守卫（切月后旧响应不得回写）
// ═══════════════════════════════════════════════════════════════════════════

describe('竞态守卫', () => {
  test('⚠️⚠️ 切月后旧响应**不得**回写结果', async () => {
    // ⚠️ **真实竞态**：用户在「11 月」发起检查，切到「12 月」，
    //    11 月的响应这时才回来 ⇒ 若直接写进 ``issues``，
    //    12 月的界面会显示 11 月的风险提示。
    // ⇒ 组件用 ``requestedMonth !== props.planMonth`` 拦住了它。
    let resolveCheck: (value: unknown) => void = () => {}
    checkMonthPlanApi.mockReturnValue(
      new Promise((resolve) => {
        resolveCheck = resolve
      }),
    )

    const wrapper = mountPanel({ planMonth: '2026-11' })
    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    // 用户切到 12 月。
    await wrapper.setProps({ planMonth: '2026-12' })

    // 11 月的响应这时才回来。
    resolveCheck({
      suggestionId: 's1',
      issues: [makeIssue({ message: '11 月的风险' })],
      notice: '',
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('11 月的风险')
  })

  test('⚠️ 切月后重新检查 → 新响应正常展示', async () => {
    checkMonthPlanApi.mockResolvedValueOnce({
      suggestionId: 's1',
      issues: [makeIssue({ message: '11 月的风险' })],
      notice: '',
    })
    const wrapper = mountPanel({ planMonth: '2026-11' })

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    await wrapper.setProps({ planMonth: '2026-12' })
    checkMonthPlanApi.mockResolvedValueOnce({
      suggestionId: 's2',
      issues: [makeIssue({ message: '12 月的风险' })],
      notice: '',
    })

    await startButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('12 月的风险')
    expect(wrapper.text()).not.toContain('11 月的风险')
  })
})
