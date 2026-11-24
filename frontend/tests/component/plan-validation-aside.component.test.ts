/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 提交前检查侧栏。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/plan-check-aside-layout-ui.test.ts`` 等）里有「读源码 + 正则」
 * 的逻辑型断言，例如：
 *
 * ```typescript
 * assert.match(validationAside, /<slot name="ai-review"/)
 * assert.match(validationAside, /plan-checklist__ai-review/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``<slot name="ai-review" />`` 改成 ``<slot name="ai-review-panel" />``
 *   ⇒ 正则**照样匹配得上**（因为 ``ai-review`` 是子串）⇒ 测试全绿，
 *   而父组件传入的 ``ai-review`` 插槽**再也渲染不出来** ——
 *   AI 检查面板从侧栏里凭空消失。
 *
 * ⚠️ **本文件真正测的东西**
 *   ① **阻塞项统计**：只有 ``blocking: true`` 的问题计入「N 项待处理」，
 *      建议项（``blocking: false``）**不计入**但仍要显示；
 *   ② **提交按钮守卫**：有阻塞项 ⇒ 提交禁用；无阻塞项 ⇒ 提交可用；
 *   ③ **保存态三态**：已保存 / 有未保存修改 / 保存失败；
 *   ④ **只读态**：``editable=false`` ⇒ 保存与提交都禁用；
 *   ⑤ **事件发射**：``save`` / ``submit`` / ``locate``；
 *   ⑥ **ai-review 插槽**：真的渲染出来了（这条正是原正则测不准的）。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test } from 'vitest'

import PlanValidationAside from '../../src/views/employee/components/PlanValidationAside.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

interface Issue {
  key: string
  label: string
  targetId?: string
  blocking: boolean
}

function makeIssue(overrides: Partial<Issue> = {}): Issue {
  return { key: 'k1', label: '缺少交付物', blocking: true, ...overrides }
}

function mountAside(props: Record<string, unknown> = {}, slots: Record<string, unknown> = {}) {
  return mount(PlanValidationAside, {
    props: {
      issues: [],
      dirty: false,
      saving: false,
      submitting: false,
      editable: true,
      ...props,
    },
    slots,
  })
}

/** 头部那句「N 项待处理 / 可以提交」。 */
function headText(wrapper: ReturnType<typeof mountAside>): string {
  return wrapper.find('.plan-checklist__head strong').text()
}

/** 「保存草稿」按钮。 */
function saveButton(wrapper: ReturnType<typeof mountAside>) {
  return wrapper.findAll('.plan-checklist__actions button')[0]
}

/** 「提交审批」按钮。 */
function submitButton(wrapper: ReturnType<typeof mountAside>) {
  return wrapper.findAll('.plan-checklist__actions button')[1]
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 阻塞项统计
// ═══════════════════════════════════════════════════════════════════════════

describe('阻塞项统计', () => {
  test('无问题 → 显示「可以提交」', () => {
    const wrapper = mountAside({ issues: [] })

    expect(headText(wrapper)).toBe('可以提交')
  })

  test('⚠️ 1 个阻塞项 → 「1 项待处理」', () => {
    const wrapper = mountAside({ issues: [makeIssue()] })

    expect(headText(wrapper)).toBe('1 项待处理')
  })

  test('⚠️ 多个阻塞项 → 计数正确', () => {
    const wrapper = mountAside({
      issues: [
        makeIssue({ key: 'a' }),
        makeIssue({ key: 'b' }),
        makeIssue({ key: 'c' }),
      ],
    })

    expect(headText(wrapper)).toBe('3 项待处理')
  })

  test('⚠️⚠️ 建议项（blocking=false）**不计入**待处理计数', () => {
    // ⚠️ **这是最容易写反的一处**：
    //    若把「所有 issue」都算进 blockingIssues ⇒ 用户看到「2 项待处理」，
    //    但其中一条其实只是建议 ⇒ 提交按钮被错误禁用，用户永远提交不了。
    const wrapper = mountAside({
      issues: [
        makeIssue({ key: 'a', blocking: true }),
        makeIssue({ key: 'b', blocking: false, label: '建议补充验收标准' }),
      ],
    })

    expect(headText(wrapper)).toBe('1 项待处理')
  })

  test('⚠️ 全部是建议项 → 仍显示「可以提交」', () => {
    const wrapper = mountAside({
      issues: [makeIssue({ key: 'a', blocking: false })],
    })

    expect(headText(wrapper)).toBe('可以提交')
  })

  test('⚠️ 就绪信号灯：有无阻塞项决定 is-ready 类', () => {
    const ready = mountAside({ issues: [] })
    const blocked = mountAside({ issues: [makeIssue()] })

    expect(ready.find('.plan-checklist__signal').classes()).toContain('is-ready')
    expect(blocked.find('.plan-checklist__signal').classes()).not.toContain('is-ready')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 问题列表渲染
// ═══════════════════════════════════════════════════════════════════════════

describe('问题列表', () => {
  test('⚠️ 阻塞项与建议项都要渲染出来', () => {
    const wrapper = mountAside({
      issues: [
        makeIssue({ key: 'a', label: '缺少交付物', blocking: true }),
        makeIssue({ key: 'b', label: '建议补充验收标准', blocking: false }),
      ],
    })

    const buttons = wrapper.findAll('.plan-checklist__issues button')
    expect(buttons).toHaveLength(2)
    expect(buttons[0].text()).toContain('缺少交付物')
    expect(buttons[1].text()).toContain('建议补充验收标准')
  })

  test('⚠️ 阻塞项与建议项的样式标记不同', () => {
    const wrapper = mountAside({
      issues: [
        makeIssue({ key: 'a', blocking: true }),
        makeIssue({ key: 'b', blocking: false }),
      ],
    })

    const buttons = wrapper.findAll('.plan-checklist__issues button')
    // 阻塞项：红色系（无 is-advisory）；建议项：灰绿系（is-advisory）。
    expect(buttons[0].classes()).not.toContain('is-advisory')
    expect(buttons[1].classes()).toContain('is-advisory')
  })

  test('⚠️ 阻塞标记字符：阻塞「!」/ 建议「i」', () => {
    const wrapper = mountAside({
      issues: [
        makeIssue({ key: 'a', blocking: true }),
        makeIssue({ key: 'b', blocking: false }),
      ],
    })

    const buttons = wrapper.findAll('.plan-checklist__issues button')
    expect(buttons[0].find('span').text()).toBe('!')
    expect(buttons[1].find('span').text()).toBe('i')
  })

  test('⚠️ 无问题 → 显示「内容完整」就绪态', () => {
    const wrapper = mountAside({ issues: [] })

    expect(wrapper.find('.plan-checklist__ready').exists()).toBe(true)
    expect(wrapper.find('.plan-checklist__ready strong').text()).toBe('内容完整')
    expect(wrapper.findAll('.plan-checklist__issues button')).toHaveLength(0)
  })

  test('⚠️ 点问题条目 → 发出 locate 事件并带上该 issue', () => {
    const target = makeIssue({ key: 'a', label: '缺少交付物', targetId: 'task-0-deliverable' })
    const wrapper = mountAside({ issues: [target] })

    wrapper.find('.plan-checklist__issues button').trigger('click')

    expect(wrapper.emitted('locate')).toBeTruthy()
    expect(wrapper.emitted('locate')![0]).toEqual([target])
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 保存态三态
// ═══════════════════════════════════════════════════════════════════════════

describe('保存态', () => {
  test('默认 → 「当前内容已保存」', () => {
    const wrapper = mountAside({ dirty: false })

    expect(wrapper.find('.plan-save-state strong').text()).toBe('当前内容已保存')
    expect(wrapper.find('.plan-save-state').classes()).not.toContain('is-dirty')
    expect(wrapper.find('.plan-save-state').classes()).not.toContain('is-error')
  })

  test('⚠️ dirty=true → 「有未保存修改」+ is-dirty 类', () => {
    const wrapper = mountAside({ dirty: true })

    expect(wrapper.find('.plan-save-state strong').text()).toBe('有未保存修改')
    expect(wrapper.find('.plan-save-state').classes()).toContain('is-dirty')
    expect(wrapper.find('.plan-save-state').classes()).not.toContain('is-error')
  })

  test('⚠️ saveError 非空 → 「保存失败」+ is-error 类（文案优先级高于 dirty）', () => {
    // ⚠️ **优先级**：保存失败比「有未保存修改」更严重 ⇒ 文案必须显示保存失败。
    //
    // ⚠️ **注意**：类名上 ``is-dirty`` 与 ``is-error`` 是**并列**的
    //    （模板写的是 ``{ 'is-dirty': dirty, 'is-error': saveError }``），
    //    两者同时为真时两个类都在 ⇒ 样式上「错误色」后写、覆盖前者。
    //    ⇒ 这里断言**文案**的优先级，不断言类名的互斥。
    const wrapper = mountAside({ dirty: true, saveError: '网络异常' })

    expect(wrapper.find('.plan-save-state strong').text()).toBe('保存失败')
    expect(wrapper.find('.plan-save-state').classes()).toContain('is-error')
  })

  test('⚠️ saveError 文案透传出来', () => {
    const wrapper = mountAside({ saveError: '草稿保存失败：网络异常' })

    expect(wrapper.find('.plan-save-state small').text()).toBe('草稿保存失败：网络异常')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 提交按钮守卫
// ═══════════════════════════════════════════════════════════════════════════

describe('提交按钮守卫', () => {
  test('无阻塞项 → 提交按钮可用', () => {
    const wrapper = mountAside({ issues: [] })

    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  test('⚠️ 有阻塞项 → 提交按钮禁用', () => {
    const wrapper = mountAside({ issues: [makeIssue()] })

    expect(submitButton(wrapper).attributes('disabled')).toBeDefined()
  })

  test('⚠️ 只有建议项 → 提交按钮**仍可用**', () => {
    // ⚠️ **建议项不该卡住提交** —— 这是「阻塞项统计写错」最直接的用户可见后果。
    const wrapper = mountAside({ issues: [makeIssue({ blocking: false })] })

    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  test('⚠️ 有阻塞项 → 显示「完成上方 N 项后可提交」提示', () => {
    const wrapper = mountAside({
      issues: [makeIssue({ key: 'a' }), makeIssue({ key: 'b' })],
    })

    expect(wrapper.find('.plan-checklist__actions small').text()).toBe('完成上方 2 项后可提交')
  })

  test('⚠️ 无阻塞项 → 不显示提示文案', () => {
    const wrapper = mountAside({ issues: [] })

    expect(wrapper.find('.plan-checklist__actions small').exists()).toBe(false)
  })

  test('⚠️ editable=false → 保存与提交都禁用', () => {
    const wrapper = mountAside({ editable: false })

    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
    expect(submitButton(wrapper).attributes('disabled')).toBeDefined()
  })

  test('⚠️ saving=true → 保存按钮禁用（防重复提交）', () => {
    const wrapper = mountAside({ saving: true })

    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
  })

  test('⚠️ submitting=true → 提交按钮禁用（防重复提交）', () => {
    const wrapper = mountAside({ submitting: true })

    expect(submitButton(wrapper).attributes('disabled')).toBeDefined()
  })

  test('⚠️ saving=true → 保存按钮进入 loading 态', () => {
    const wrapper = mountAside({ saving: true })

    expect(saveButton(wrapper).classes()).toContain('is-loading')
  })

  test('⚠️ submitting=true → 提交按钮进入 loading 态', () => {
    const wrapper = mountAside({ submitting: true })

    expect(submitButton(wrapper).classes()).toContain('is-loading')
  })

  test('⚠️ 提交按钮文案可自定义', () => {
    const wrapper = mountAside({ submitLabel: '提交周计划' })

    expect(submitButton(wrapper).text()).toBe('提交周计划')
  })

  test('⚠️ 提交按钮默认文案是「提交审批」', () => {
    const wrapper = mountAside()

    expect(submitButton(wrapper).text()).toBe('提交审批')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 事件发射
// ═══════════════════════════════════════════════════════════════════════════

describe('事件发射', () => {
  test('⚠️ 点「保存草稿」→ 发出 save 事件', () => {
    const wrapper = mountAside()

    saveButton(wrapper).trigger('click')

    expect(wrapper.emitted('save')).toHaveLength(1)
  })

  test('⚠️ 点「提交审批」→ 发出 submit 事件', () => {
    const wrapper = mountAside()

    submitButton(wrapper).trigger('click')

    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  test('⚠️ 禁用状态下点击不应触发保存事件', () => {
    // ⚠️ Element Plus 的 disabled 按钮会吞掉 click ⇒ 父组件不会收到 save。
    const wrapper = mountAside({ editable: false })

    saveButton(wrapper).trigger('click')

    expect(wrapper.emitted('save')).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. 汇总项与插槽
// ═══════════════════════════════════════════════════════════════════════════

describe('汇总项与 ai-review 插槽', () => {
  test('⚠️ summaryItems 渲染成 label/value 对', () => {
    const wrapper = mountAside({
      summaryItems: [
        { label: '任务数', value: 3 },
        { label: '总权重', value: '100%' },
      ],
    })

    const rows = wrapper.findAll('.plan-checklist__summary div')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('dt').text()).toBe('任务数')
    expect(rows[0].find('dd').text()).toBe('3')
    expect(rows[1].find('dt').text()).toBe('总权重')
    expect(rows[1].find('dd').text()).toBe('100%')
  })

  test('⚠️ 无 summaryItems → 不渲染汇总区', () => {
    const wrapper = mountAside()

    expect(wrapper.find('.plan-checklist__summary').exists()).toBe(false)
  })

  test('⭐⭐ ai-review 插槽内容**真的渲染出来了**', () => {
    // ⚠️ **这正是原正则断言测不准的那一条**：
    //    把 ``<slot name="ai-review" />`` 改成别的插槽名 ⇒
    //    源码正则照样匹配 ⇒ 旧测试全绿 ⇒ 但 AI 检查面板从侧栏消失。
    //    ⇒ 只有**真实挂载 + 真实插槽内容**才能抓到。
    const wrapper = mountAside(
      {},
      { 'ai-review': '<p class="probe">AI 检查面板</p>' },
    )

    expect(wrapper.find('.plan-checklist__ai-review').exists()).toBe(true)
    expect(wrapper.find('.plan-checklist__ai-review .probe').text()).toBe('AI 检查面板')
  })

  test('⭐ 未提供 ai-review 插槽 → 不渲染 ai-review 容器', () => {
    const wrapper = mountAside()

    expect(wrapper.find('.plan-checklist__ai-review').exists()).toBe(false)
  })

  test('⚠️ actionsFirst → 侧栏加 is-actions-first 类', () => {
    const wrapper = mountAside({ actionsFirst: true })

    expect(wrapper.find('.plan-checklist').classes()).toContain('is-actions-first')
  })
})
