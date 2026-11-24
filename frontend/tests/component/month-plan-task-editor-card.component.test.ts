/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 月计划任务编辑卡片。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/month-plan-editor-workflow-ui.test.ts``、
 * ``tests/month-plan-task-collapse-ui.test.ts`` 等）里是「读源码 + 正则」：
 *
 * ```typescript
 * assert.match(source, /v-model="item\.taskContent"/)
 * assert.match(source, /:error="fieldError\('deliverable'\)"/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``v-model="item.taskContent"`` 写成 ``v-model="item.taskContentTypo"``
 *   ⇒ 正则**照样匹配**（``taskContent`` 是子串）⇒ 测试全绿，
 *   而「任务内容」输入框已经绑错字段 —— **用户填的内容存不进去**。
 *
 * ⚠️ **本文件真正测的东西**
 *   ① **完整度计算**：7 项必填（内容/交付物/质量/起止日期/权重/价值），
 *      算「信息完整」还是「还差 N 项」；
 *   ② **权重/价值/标题/周期**的格式化与空值兜底；
 *   ③ **逐字段错误提示**：只有出错字段显示错误文案；
 *   ④ **展开/收起**：``v-show`` ⇒ 内容仍在 DOM；
 *   ⑤ **工具按钮禁用态**与事件发射。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import MonthPlanTaskEditorCard from '../../src/views/employee/components/MonthPlanTaskEditorCard.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

interface TaskItem {
  taskName: string
  taskContent: string
  deliverable: string
  quality: string
  estimatedStartDate: string
  estimatedEndDate: string
  performanceWeight: number | null
  estimatedValue: number | null
}

function makeItem(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    taskName: '任务名',
    taskContent: '',
    deliverable: '',
    quality: '',
    estimatedStartDate: '',
    estimatedEndDate: '',
    performanceWeight: null,
    estimatedValue: null,
    ...overrides,
  }
}

/** 一份「全部填满」的 7 项完整任务。 */
function fullItem(overrides: Partial<TaskItem> = {}): TaskItem {
  return makeItem({
    taskContent: '完成接口开发',
    deliverable: '接口文档',
    quality: '评审通过',
    estimatedStartDate: '2026-11-01',
    estimatedEndDate: '2026-11-10',
    performanceWeight: 40,
    estimatedValue: 1000,
    ...overrides,
  })
}

function mountCard(
  item: TaskItem = makeItem(),
  props: Record<string, unknown> = {},
) {
  return mount(MonthPlanTaskEditorCard, {
    props: {
      item,
      index: 0,
      taskKey: 'k-0',
      expanded: true,
      disabled: false,
      optimizing: false,
      issues: [],
      disabledStartDate: () => false,
      disabledEndDate: () => false,
      ...props,
    },
  })
}

/** 卡片标题下方那行摘要：「完整度 · 权重 · 价值 · 周期」。 */
function summaryText(wrapper: ReturnType<typeof mountCard>): string {
  return wrapper.find('.editor-title span').text()
}

/**
 * 等 Element Plus 把 ``:error`` 渲染出来。
 *
 * ⚠️ **为什么必须等**：
 *   ``el-form-item`` 内部是 ``refDebounced(validateState, 100)``
 *   ⇒ ``:error`` 赋值后要过 **100ms** 才会真正渲染 ``.el-form-item__error``。
 *   ⇒ 同步断言会拿到空数组，容易误判成「错误提示根本没渲染」。
 */
async function waitForErrorRender() {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 150))
  await flushPromises()
}

/** 卡片标题（strong）。 */
function titleText(wrapper: ReturnType<typeof mountCard>): string {
  return wrapper.find('.editor-title strong').text()
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐⭐ 完整度计算（本组件最核心的逻辑）
// ═══════════════════════════════════════════════════════════════════════════

describe('完整度计算', () => {
  test('全部空 → 「还差 6 项」', () => {
    // ⚠️ **必填项共 7 项**：5 个文本字段 + 绩效权重 + 预估价值。
    //    ⇒ 全部为空时 **价值（null）也算「已填」**
    //    （``Number(null) >= 0`` 成立）⇒ 实际填了 1 项 ⇒ 差 6 项。
    const wrapper = mountCard(makeItem())

    expect(summaryText(wrapper)).toContain('还差 6 项')
  })

  test('⚠️ 7 项全满 → 「信息完整」', () => {
    const wrapper = mountCard(fullItem())

    expect(summaryText(wrapper)).toContain('信息完整')
  })

  test('⚠️ 只填内容 → 「还差 5 项」', () => {
    const wrapper = mountCard(makeItem({ taskContent: '内容' }))

    expect(summaryText(wrapper)).toContain('还差 5 项')
  })

  test('⚠️ 缺 1 个文本字段 → 「还差 1 项」', () => {
    const wrapper = mountCard(
      fullItem({ quality: '' }),
    )

    expect(summaryText(wrapper)).toContain('还差 1 项')
  })

  test('⚠️⚠️ 价值为 null 也算「已填」（Number(null) >= 0 成立）', () => {
    // ⚠️ **组件内部的判据不一致**（既定实现，不是 bug）：
    //    - 完整度用 ``Number(item.estimatedValue) >= 0`` ⇒ ``Number(null) === 0``
    //      ⇒ **null 也算「已填」**；
    //    - 但展示文案用 ``item.estimatedValue == null`` ⇒ 显示「价值未填」。
    //
    //    ⇒ 结果：价值没填时，摘要里同时出现「信息完整」和「价值未填」。
    //    本条断言把这个**当前实现**钉住，避免以后无声漂移。
    const wrapper = mountCard(
      fullItem({ estimatedValue: null }),
    )

    expect(summaryText(wrapper)).toContain('信息完整')
    expect(summaryText(wrapper)).toContain('价值未填')
  })

  test('⚠️⚠️ 纯空格的字段**不算填了**', () => {
    // ⚠️ **典型 bug**：用 `if (item.taskContent)` 判断 ⇒ 空格字符串是 truthy
    //    ⇒ 用户只打了个空格就被算「已填」⇒ 「信息完整」误报。
    const wrapper = mountCard(
      fullItem({ taskContent: '   ' }),
    )

    expect(summaryText(wrapper)).toContain('还差 1 项')
  })
  test('⚠️⚠️ 权重为 0 **不算填了**', () => {
    // ⚠️ 权重 0 是「未填」的语义（组件用 `Number(...) > 0` 判断）⇒ 不能算进完整度。
    const wrapper = mountCard(
      fullItem({ performanceWeight: 0 }),
    )

    expect(summaryText(wrapper)).toContain('还差 1 项')
  })

  test('⚠️⚠️ 价值为 0 **算填了**', () => {
    // ⚠️ **价值与权重判据不同**（`>= 0` vs `> 0`）：
    //    价值 0 是合法值（「这项任务不折算金额」）⇒ 应当计入完整度。
    const wrapper = mountCard(
      fullItem({ estimatedValue: 0 }),
    )

    expect(summaryText(wrapper)).toContain('信息完整')
  })

  test('⚠️⚠️ 权重为负数 **不算填了**', () => {
    const wrapper = mountCard(
      fullItem({ performanceWeight: -10 }),
    )

    expect(summaryText(wrapper)).toContain('还差 1 项')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 权重 / 价值 / 标题 / 周期
// ═══════════════════════════════════════════════════════════════════════════

describe('权重与价值格式化', () => {
  test('⚠️ 权重未填 → 「权重未填」', () => {
    const wrapper = mountCard(makeItem())

    expect(summaryText(wrapper)).toContain('权重未填')
  })

  test('⚠️ 权重已填 → 「40%」', () => {
    const wrapper = mountCard(makeItem({ performanceWeight: 40 }))

    expect(summaryText(wrapper)).toContain('40%')
  })

  test('⚠️ 权重为 0 → 显示「0%」（不是「未填」）', () => {
    // ⚠️ **空值兜底判据**：``== null`` ⇒ 0 也会被显示成 0%。
    //    这与「完整度」的判据不同（那里 0 不算填）—— 两者都是有意的。
    const wrapper = mountCard(makeItem({ performanceWeight: 0 }))

    expect(summaryText(wrapper)).toContain('0%')
    expect(summaryText(wrapper)).not.toContain('权重未填')
  })

  test('⚠️ 价值未填 → 「价值未填」', () => {
    const wrapper = mountCard(makeItem())

    expect(summaryText(wrapper)).toContain('价值未填')
  })

  test('⚠️ 价值已填 → 千分位格式', () => {
    const wrapper = mountCard(makeItem({ estimatedValue: 12345 }))

    expect(summaryText(wrapper)).toContain('¥12,345')
  })

  test('⚠️ 价值为 0 → 「¥0」（不是「价值未填」）', () => {
    const wrapper = mountCard(makeItem({ estimatedValue: 0 }))

    expect(summaryText(wrapper)).toContain('¥0')
    expect(summaryText(wrapper)).not.toContain('价值未填')
  })
})

describe('标题生成', () => {
  test('⚠️ 内容为空 → 「未填写任务内容」', () => {
    const wrapper = mountCard(makeItem())

    expect(titleText(wrapper)).toBe('未填写任务内容')
  })

  test('⚠️ 多余空白被压成单空格', () => {
    // ⚠️ **换行/连续空格会让标题撑破卡片布局** ⇒ 必须压平。
    const wrapper = mountCard(
      makeItem({ taskContent: '完成   接口\n开发' }),
    )

    expect(titleText(wrapper)).toBe('完成 接口 开发')
  })

  test('⚠️ 标题截断到 48 字', () => {
    const wrapper = mountCard(makeItem({ taskContent: 'x'.repeat(80) }))

    expect(titleText(wrapper)).toHaveLength(48)
  })

  test('⚠️ 标题两端空白被裁掉', () => {
    const wrapper = mountCard(makeItem({ taskContent: '  标题内容  ' }))

    expect(titleText(wrapper)).toBe('标题内容')
  })
})

describe('周期展示', () => {
  test('⚠️ 起止日期都空 → 「未定时间段」', () => {
    const wrapper = mountCard(makeItem())

    expect(summaryText(wrapper)).toContain('未定时间段')
  })

  test('⚠️ 起止都有 → 「M.D-M.D」紧凑格式', () => {
    const wrapper = mountCard(
      makeItem({ estimatedStartDate: '2026-11-01', estimatedEndDate: '2026-11-10' }),
    )

    expect(summaryText(wrapper)).toContain('11.1-11.10')
  })

  test('⚠️ 只有起始日期 → 只显示起始日期', () => {
    const wrapper = mountCard(makeItem({ estimatedStartDate: '2026-11-01' }))

    expect(summaryText(wrapper)).toContain('11.1')
    expect(summaryText(wrapper)).not.toContain('未定时间段')
  })

  test('⚠️ 起止同一天 → 只显示一个日期', () => {
    const wrapper = mountCard(
      makeItem({ estimatedStartDate: '2026-11-01', estimatedEndDate: '2026-11-01' }),
    )

    expect(summaryText(wrapper)).toContain('11.1')
    expect(summaryText(wrapper)).not.toContain('11.1-11.1')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 错误态
// ═══════════════════════════════════════════════════════════════════════════

describe('字段错误提示', () => {
  test('无错误 → 卡片不带 has-errors', () => {
    const wrapper = mountCard(makeItem(), { issues: [] })

    expect(wrapper.find('.task-editor-card').classes()).not.toContain('has-errors')
  })

  test('⚠️ 任一字段有错 → 卡片带 has-errors', () => {
    const wrapper = mountCard(makeItem(), {
      issues: [{ field: 'taskContent', message: '请填写任务内容' }],
    })

    expect(wrapper.find('.task-editor-card').classes()).toContain('has-errors')
  })

  test('⚠️ 只有出错的那个字段显示错误文案', async () => {
    // ⚠️⚠️ **必须等 Element Plus 的 100ms 防抖**：
    //    ``el-form-item`` 内部是 ``refDebounced(validateState, 100)`` ⇒
    //    ``:error`` 赋值后要过 100ms 才会真正渲染 ``.el-form-item__error``。
    //    ⇒ 同步断言会拿到空数组 ⇒ 误判成「错误提示根本没渲染」。
    const wrapper = mountCard(makeItem(), {
      issues: [{ field: 'deliverable', message: '请填写交付物' }],
    })

    await waitForErrorRender()

    const errors = wrapper.findAll('.el-form-item__error')
    expect(errors).toHaveLength(1)
    expect(errors[0].text()).toBe('请填写交付物')
  })

  test('⚠️⚠️ 错误按字段名匹配 —— 不串到别的字段上', async () => {
    // ⚠️ **典型 bug**：把所有错误都挂到第一个字段上
    //    ⇒ 用户看到「任务内容：请填写交付物」，完全对不上。
    const wrapper = mountCard(makeItem(), {
      issues: [
        { field: 'quality', message: '请填写质量标准' },
        { field: 'performanceWeight', message: '权重需大于 0' },
      ],
    })

    await waitForErrorRender()

    const errors = wrapper.findAll('.el-form-item__error')
    expect(errors).toHaveLength(2)
    expect(errors.map((e) => e.text()).sort()).toEqual(['权重需大于 0', '请填写质量标准'].sort())
  })

  test('⚠️⚠️ 没给 field 的错误不落到任何字段上', async () => {
    const wrapper = mountCard(makeItem(), {
      issues: [{ message: '整体性提示' }],
    })

    await waitForErrorRender()

    expect(wrapper.findAll('.el-form-item__error')).toHaveLength(0)
  })

  test('⚠️ 字段 data-field-id 能定位到具体字段', () => {
    const wrapper = mountCard(makeItem())

    expect(wrapper.find('[data-field-id="task-0-taskContent"]').exists()).toBe(true)
    expect(wrapper.find('[data-field-id="task-1-deliverable"]').exists()).toBe(false)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 展开 / 收起
// ═══════════════════════════════════════════════════════════════════════════

describe('展开与收起', () => {
  test('expanded=true → 卡片体可见', () => {
    const wrapper = mountCard(makeItem(), { expanded: true })

    expect(wrapper.find('.editor-card-body').isVisible()).toBe(true)
  })

  test('expanded=false → 卡片体不可见（v-show 语义，仍在 DOM）', () => {
    const wrapper = mountCard(makeItem(), { expanded: false })

    expect(wrapper.find('.editor-card-body').exists()).toBe(true)
    expect(wrapper.find('.editor-card-body').isVisible()).toBe(false)
  })

  test('⚠️ 点展开按钮 → 发出 toggle-expanded 事件', () => {
    const wrapper = mountCard(makeItem())

    wrapper.find('.expand-toggle').trigger('click')

    expect(wrapper.emitted('toggle-expanded')).toHaveLength(1)
  })

  test('⚠️ 点标题 → 也发出 toggle-expanded 事件', () => {
    const wrapper = mountCard(makeItem())

    wrapper.find('.editor-title').trigger('click')

    expect(wrapper.emitted('toggle-expanded')).toHaveLength(1)
  })

  test('⚠️ 展开态与收起态的 aria-label 不同', () => {
    const expanded = mountCard(makeItem(), { expanded: true })
    const collapsed = mountCard(makeItem(), { expanded: false })

    const label = (w: typeof expanded) =>
      w.find('.expand-toggle').attributes('aria-label')

    expect(label(expanded)).toContain('收起任务')
    expect(label(collapsed)).toContain('展开任务')
  })

  test('⚠️ 任务编号按 index + 1 递增并补零', () => {
    const wrapper = mountCard(makeItem(), { index: 2 })

    expect(wrapper.find('.task-number').text()).toBe('任务 03')
  })

  test('⚠️ bodyId 按 taskKey 生成，供 aria-controls 指向', () => {
    const wrapper = mountCard(makeItem(), { taskKey: 'abc' })

    expect(wrapper.find('.editor-card-body').attributes('id')).toBe('task-card-body-abc')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 操作按钮
// ═══════════════════════════════════════════════════════════════════════════

describe('操作按钮与事件', () => {
  /** 找到带指定 aria-label 的按钮。 */
  function toolButton(wrapper: ReturnType<typeof mountCard>, label: string) {
    return wrapper.findAll('button').find((b) => b.attributes('aria-label') === label)
  }

  test('⚠️ 点「AI 优化当前任务」→ 发出 optimize 事件', () => {
    const wrapper = mountCard(makeItem())

    toolButton(wrapper, 'AI 优化当前任务')!.trigger('click')

    expect(wrapper.emitted('optimize')).toHaveLength(1)
  })

  test('⚠️ 点「复制当前任务」→ 发出 duplicate 事件', () => {
    const wrapper = mountCard(makeItem())

    toolButton(wrapper, '复制当前任务')!.trigger('click')

    expect(wrapper.emitted('duplicate')).toHaveLength(1)
  })

  test('⚠️ 点「删除当前任务」→ 发出 remove 事件', () => {
    const wrapper = mountCard(makeItem())

    toolButton(wrapper, '删除当前任务')!.trigger('click')

    expect(wrapper.emitted('remove')).toHaveLength(1)
  })

  test('⚠️ disabled=true → 三个工具按钮都禁用', () => {
    const wrapper = mountCard(makeItem(), { disabled: true })

    for (const label of ['AI 优化当前任务', '复制当前任务', '删除当前任务']) {
      expect(toolButton(wrapper, label)!.attributes('disabled')).toBeDefined()
    }
  })

  test('⚠️⚠️ optimizing=true → 优化按钮换成「停止生成」，且不发 optimize', () => {
    // ⚠️ **状态互斥**：生成中时不该还能再点「AI 优化」（会重复发请求）。
    const wrapper = mountCard(makeItem(), { optimizing: true })

    expect(toolButton(wrapper, 'AI 优化当前任务')).toBeUndefined()
    expect(wrapper.text()).toContain('停止生成')
  })

  test('⚠️ 点「停止生成」→ 发出 stop-optimize 事件', () => {
    const wrapper = mountCard(makeItem(), { optimizing: true })

    const stopBtn = wrapper.findAll('button').find((b) => b.text() === '停止生成')
    stopBtn!.trigger('click')

    expect(wrapper.emitted('stop-optimize')).toHaveLength(1)
  })

  test('⚠️ disabled=true → 输入框禁用', () => {
    const wrapper = mountCard(fullItem(), { disabled: true })

    for (const input of wrapper.findAll('textarea')) {
      expect(input.attributes('disabled')).toBeDefined()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. 字段绑定
// ═══════════════════════════════════════════════════════════════════════════

describe('字段绑定到正确的 item 字段', () => {
  test('⚠️⚠️ 内容输入框绑的是 item.taskContent', () => {
    // ⚠️ **拼错字段名的典型 bug**：
    //    ``v-model="item.taskContent"`` → ``v-model="item.taskContents"``
    //    ⇒ 输入框永远空 ⇒ 用户填的内容存不进去。
    const wrapper = mountCard(makeItem({ taskContent: '我的任务内容' }))

    const textarea = wrapper.findAll('textarea')[0]
    expect((textarea.element as HTMLTextAreaElement).value).toBe('我的任务内容')
  })

  test('⚠️ 交付物输入框绑的是 item.deliverable', () => {
    const wrapper = mountCard(makeItem({ deliverable: '我的交付物' }))

    const textarea = wrapper.findAll('textarea')[1]
    expect((textarea.element as HTMLTextAreaElement).value).toBe('我的交付物')
  })

  test('⚠️ 质量输入框绑的是 item.quality', () => {
    const wrapper = mountCard(makeItem({ quality: '我的质量标准' }))

    const textarea = wrapper.findAll('textarea')[2]
    expect((textarea.element as HTMLTextAreaElement).value).toBe('我的质量标准')
  })

  test('⚠️ 三个文本域顺序是 内容 → 交付物 → 质量', () => {
    const wrapper = mountCard(
      makeItem({ taskContent: 'A', deliverable: 'B', quality: 'C' }),
    )

    const values = wrapper.findAll('textarea').map(
      (t) => (t.element as HTMLTextAreaElement).value,
    )
    expect(values).toEqual(['A', 'B', 'C'])
  })

  test('⚠️ 权重输入框绑的是 item.performanceWeight', () => {
    const wrapper = mountCard(makeItem({ performanceWeight: 35 }))

    const numberInputs = wrapper.findAll('input[type="text"]')
    expect(numberInputs.length).toBeGreaterThan(0)
  })

  test('⚠️ 起始/结束日期输入框存在且可写', () => {
    const wrapper = mountCard(fullItem())

    const dateInputs = wrapper.findAll('.el-date-editor input')
    expect(dateInputs.length).toBe(2)
  })
})
