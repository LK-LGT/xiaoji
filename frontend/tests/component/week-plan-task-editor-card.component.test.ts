/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 周计划任务编辑卡片。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/week-plan-editor-state-ui.test.ts``）里有 19 处逻辑型断言，
 * 但**全部是「源码里有这串字」**：
 *
 * ```typescript
 * assert.match(source, /v-model="model\.deliverable"/)
 * assert.match(source, /v-model="model\.quality"/)
 * assert.doesNotMatch(source, /v-model="model\.completionStatus"/)
 * assert.match(source, /const weekCardExpanded = ref\(false\)/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``v-model="model.deliverable"`` 改成 ``v-model="model.delivrable"``
 *   （拼错一个字母）⇒ 正则**照样匹配得上** ⇒ 测试全绿，
 *   而「交付物」输入框已经绑错了字段 —— **用户填的内容存不进去**。
 *
 * ⚠️ **本文件真正测的东西**（本轮性价比最高的一个）
 *   ① **要点计数**：``countWeekWorkPoints(model.content)`` → 「N 个工作要点」；
 *   ② **整体错误态**：任一字段有错 → 卡片加 ``has-error`` 类；
 *   ③ **逐字段错误提示**：只有出错的那个字段显示错误文案 + ``is-error`` 类；
 *   ④ **展开 / 收起**：``v-show`` ⇒ 内容仍在 DOM，只是不可见；
 *   ⑤ **只读态**：``disabled`` ⇒ 输入框禁用；
 *   ⑥ **统计周期**：``weekStart`` 至 ``weekEnd``。
 *
 * ⚠️ **为什么选它**
 *   75 行、**零外部依赖**（只依赖一个纯函数 ``countWeekWorkPoints``）、
 *   却是 19 处逻辑断言的落点 ⇒ 挂载成本几乎为零，逻辑密度最高。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test } from 'vitest'

import WeekPlanTaskEditorCard from '../../src/views/employee/components/WeekPlanTaskEditorCard.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

interface Model {
  content: string
  deliverable: string
  quality: string
}

function makeModel(overrides: Partial<Model> = {}): Model {
  return { content: '', deliverable: '', quality: '', ...overrides }
}

/**
 * 挂载。
 *
 * ⚠️ **``defineModel`` 展开后是 ``modelValue`` prop + ``update:modelValue`` 事件** ——
 * 不是 ``model`` prop。写错会得到：
 *
 * ```
 * [Vue warn]: Missing required prop: "modelValue"
 * TypeError: Cannot read properties of undefined (reading 'content')
 * ```
 */
function mountCard(
  model: Model = makeModel(),
  props: Record<string, unknown> = {},
) {
  const wrapper = mount(WeekPlanTaskEditorCard, {
    props: {
      weekStart: '2026-11-16',
      weekEnd: '2026-11-22',
      expanded: true,
      disabled: false,
      fieldErrors: {},
      modelValue: model,
      'onUpdate:modelValue': (next: Model) => {
        Object.assign(model, next)
      },
      ...props,
    },
  })
  return { wrapper, model }
}

/** 卡片头部的「N 个工作要点」文本。 */
function pointCountText(wrapper: ReturnType<typeof mountCard>['wrapper']): string {
  return wrapper.find('.week-plan-card__toggle small').text()
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 要点计数
// ═══════════════════════════════════════════════════════════════════════════

describe('工作要点计数', () => {
  test('空内容 → 0 个工作要点', () => {
    const { wrapper } = mountCard(makeModel({ content: '' }))

    expect(pointCountText(wrapper)).toContain('0 个工作要点')
  })

  test('3 行内容 → 3 个工作要点', () => {
    const { wrapper } = mountCard(
      makeModel({ content: '1. 完成接口开发\n2. 完成接口联调\n3. 整理验收材料' }),
    )

    expect(pointCountText(wrapper)).toContain('3 个工作要点')
  })

  test('⚠️ 空白行**不计入**要点数', () => {
    // ⚠️ **业务语义**：``countWeekWorkPoints`` 数的是**非空行**。
    //    若改成数总行数，用户敲了几个空行就会看到虚高的数字。
    const { wrapper } = mountCard(
      makeModel({ content: '1. 完成接口开发\n\n   \n2. 完成接口联调' }),
    )

    expect(pointCountText(wrapper)).toContain('2 个工作要点')
  })

  test('⚠️ 计数随输入**实时更新**', async () => {
    // ⚠️ 绑定写错（``model.content`` → ``model.contents``）时，
    //    计数会永远停在旧值 ⇒ 这条能抓到。
    //
    // ⚠️ **必须用 ``setProps``** —— 直接改传进去的原始对象不生效：
    //    Vue 挂载时把 prop 转成了 reactive proxy，
    //    外部改**原始对象**不会触发渲染更新。
    const { wrapper } = mountCard(makeModel({ content: '1. 一件事' }))

    expect(pointCountText(wrapper)).toContain('1 个工作要点')

    await wrapper.setProps({
      modelValue: makeModel({ content: '1. 一件事\n2. 两件事\n3. 三件事' }),
    })

    expect(pointCountText(wrapper)).toContain('3 个工作要点')
  })

  test('统计周期显示「起 至 止」', () => {
    const { wrapper } = mountCard()

    expect(wrapper.find('.week-plan-card__period').text()).toContain('2026-11-16 至 2026-11-22')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 错误态
// ═══════════════════════════════════════════════════════════════════════════

describe('字段错误提示', () => {
  test('无错误 → 卡片不带 has-error', () => {
    const { wrapper } = mountCard(makeModel(), { fieldErrors: {} })

    expect(wrapper.find('.week-plan-card').classes()).not.toContain('has-error')
  })

  test('⚠️ 任一字段有错 → 卡片带 has-error', () => {
    const { wrapper } = mountCard(makeModel(), {
      fieldErrors: { content: '请填写工作内容' },
    })

    expect(wrapper.find('.week-plan-card').classes()).toContain('has-error')
  })

  test('⚠️ 只有出错的那个字段显示错误文案', () => {
    // ⚠️ **精确性**：若所有字段都无条件显示错误提示，
    //    用户会看到一堆根本不存在的报错。
    const { wrapper } = mountCard(makeModel(), {
      fieldErrors: { deliverable: '请填写交付物' },
    })

    const errors = wrapper.findAll('.week-plan-field__error')
    expect(errors).toHaveLength(1)
    expect(errors[0].text()).toBe('请填写交付物')
  })

  test('⚠️ 出错字段加 is-error 类，其余不加', () => {
    const { wrapper } = mountCard(makeModel(), {
      fieldErrors: { quality: '请填写质量标准' },
    })

    // ⚠️ 质量字段（id=week-item-0-quality）。
    expect(wrapper.find('#week-item-0-quality').classes()).toContain('is-error')
    // ⚠️ 内容字段（id=week-item-0-content）没错 ⇒ 不该有 is-error。
    expect(wrapper.find('#week-item-0-content').classes()).not.toContain('is-error')
  })

  test('⚠️ 三个字段各自独立报错', () => {
    const { wrapper } = mountCard(makeModel(), {
      fieldErrors: {
        content: '内容不能为空',
        deliverable: '交付物不能为空',
        quality: '质量不能为空',
      },
    })

    expect(wrapper.findAll('.week-plan-field__error')).toHaveLength(3)
    expect(wrapper.find('#week-item-0-content').classes()).toContain('is-error')
    expect(wrapper.find('#week-item-0-deliverable').classes()).toContain('is-error')
    expect(wrapper.find('#week-item-0-quality').classes()).toContain('is-error')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 展开 / 收起
// ═══════════════════════════════════════════════════════════════════════════

describe('展开与收起', () => {
  test('expanded=true → 卡片体可见', () => {
    const { wrapper } = mountCard(makeModel(), { expanded: true })

    expect(wrapper.find('.week-plan-card__body').isVisible()).toBe(true)
  })

  test('expanded=false → 卡片体不可见（但仍在 DOM 里，v-show 语义）', () => {
    // ⚠️ **v-show 不是 v-if** ⇒ 内容不销毁，只是 display:none。
    //    ⇒ 断言要用 ``isVisible()`` 而不是 ``exists()``。
    const { wrapper } = mountCard(makeModel(), { expanded: false })

    expect(wrapper.find('.week-plan-card__body').exists()).toBe(true)
    expect(wrapper.find('.week-plan-card__body').isVisible()).toBe(false)
  })

  test('⚠️ 点头部按钮 → 发出 toggle 事件', () => {
    const { wrapper } = mountCard(makeModel(), { expanded: true })

    wrapper.find('.week-plan-card__toggle').trigger('click')

    expect(wrapper.emitted('toggle')).toBeTruthy()
    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })

  test('⚠️ 点工具栏按钮 → 也发出 toggle 事件', () => {
    // ⚠️ 两个入口都要能收起 —— 漏掉一个就是「点不动」。
    const { wrapper } = mountCard(makeModel(), { expanded: true })

    const toolbarBtn = wrapper.findAll('button').find((b) => b.attributes('aria-label')?.includes('周计划'))
    expect(toolbarBtn).toBeDefined()
    toolbarBtn!.trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })

  test('⚠️ 展开态与收起态的 aria-label 不同', () => {
    // ⚠️ **无障碍**：读屏用户靠 aria-label 判断当前能不能点。
    const expanded = mountCard(makeModel(), { expanded: true }).wrapper
    const collapsed = mountCard(makeModel(), { expanded: false }).wrapper

    const findLabel = (w: typeof expanded) =>
      w.findAll('button').map((b) => b.attributes('aria-label')).filter(Boolean)

    expect(findLabel(expanded)).toContain('收起周计划')
    expect(findLabel(collapsed)).toContain('展开周计划')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 只读态
// ═══════════════════════════════════════════════════════════════════════════

describe('只读态（disabled）', () => {
  test('disabled=true → 三个输入框都禁用', () => {
    const { wrapper } = mountCard(makeModel(), { disabled: true })

    const inputs = wrapper.findAll('textarea')
    expect(inputs).toHaveLength(3)
    for (const input of inputs) {
      expect(input.attributes('disabled')).toBeDefined()
    }
  })

  test('disabled=false → 输入框可编辑', () => {
    const { wrapper } = mountCard(makeModel(), { disabled: false })

    for (const input of wrapper.findAll('textarea')) {
      expect(input.attributes('disabled')).toBeUndefined()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 字段绑定
// ═══════════════════════════════════════════════════════════════════════════

describe('三个字段分别绑到正确的 model 字段', () => {
  test('⚠️ 内容 / 交付物 / 质量 → 三个不同的输入框', () => {
    const { wrapper } = mountCard(
      makeModel({ content: '内容值', deliverable: '交付物值', quality: '质量值' }),
    )

    const values = wrapper.findAll('textarea').map((t) => (t.element as HTMLTextAreaElement).value)

    expect(values).toHaveLength(3)
    expect(values[0]).toBe('内容值')
    expect(values[1]).toBe('交付物值')
    expect(values[2]).toBe('质量值')
  })

  test('⚠️ ⚠️ 内容输入框绑的是 model.content，不是别的字段', () => {
    // ⚠️ **拼错字段名的典型 bug**：
    //    把 ``v-model="model.content"`` 写成 ``v-model="model.contents"``
    //    ⇒ 输入框永远空 ⇒ 用户填的内容存不进去。
    const { wrapper } = mountCard(makeModel({ content: '我的工作内容' }))

    const first = wrapper.findAll('textarea')[0]
    expect((first.element as HTMLTextAreaElement).value).toBe('我的工作内容')
  })

  test('⚠️ 交付物输入框绑的是 model.deliverable', () => {
    const { wrapper } = mountCard(makeModel({ deliverable: '我的交付物' }))

    const second = wrapper.findAll('textarea')[1]
    expect((second.element as HTMLTextAreaElement).value).toBe('我的交付物')
  })

  test('⚠️ 质量输入框绑的是 model.quality', () => {
    const { wrapper } = mountCard(makeModel({ quality: '我的质量标准' }))

    const third = wrapper.findAll('textarea')[2]
    expect((third.element as HTMLTextAreaElement).value).toBe('我的质量标准')
  })
})
