/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 日计划任务编辑卡片。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/employee-day-plan-efficiency-ui.test.ts``）里是
 * 「读源码 + 正则」：
 *
 * ```typescript
 * assert.match(source, /countWeekWorkPoints\(content\.value\)/)
 * assert.match(source, /v-if="editable"/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``countWeekWorkPoints(content.value)`` 改成
 *   ``countWeekWorkPoints(content.value + 'x')`` ⇒ 正则**照样匹配** ⇒ 测试全绿，
 *   而卡片上的「N 个工作要点」**永远多 1** —— 用户按要点填，
 *   系统却告诉他还差很多。
 *
 * ⚠️ **本文件真正测的东西**
 *   ① **要点计数**：按**非空行**计数（空行、纯空格行都不算）；
 *   ② **只读态**：``editable=false`` ⇒ 用 ``<p>`` 展示而不是输入框；
 *   ③ **错误提示**：``showContentError`` ⇒ 卡片 + 字段都加错误类 + 提示文案；
 *   ④ **展开/收起**与 ``aria`` 属性；
 *   ⑤ **两个入口都能切换展开态**（点标题 / 点工具按钮）。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test } from 'vitest'

import DayPlanTaskEditorCard from '../../src/views/employee/components/DayPlanTaskEditorCard.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

function mountCard(
  content = '',
  props: Record<string, unknown> = {},
) {
  return mount(DayPlanTaskEditorCard, {
    props: {
      // ⚠️ **带名字的 ``defineModel<string>('content')``** 展开后是
      //    prop ``content`` + 事件 ``update:content`` ——
      //    **不是** ``modelValue``（那是无参 ``defineModel()`` 的形状）。
      content,
      expanded: true,
      editable: true,
      disabled: false,
      showContentError: false,
      'onUpdate:content': () => {},
      ...props,
    },
  })
}

/** 头部那句「N 个工作要点 · …」。 */
function pointCountText(wrapper: ReturnType<typeof mountCard>): string {
  return wrapper.find('.day-plan-card__toggle small').text()
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 要点计数
// ═══════════════════════════════════════════════════════════════════════════

describe('工作要点计数', () => {
  test('空内容 → 0 个工作要点', () => {
    const wrapper = mountCard('')

    expect(pointCountText(wrapper)).toContain('0 个工作要点')
  })

  test('3 行内容 → 3 个工作要点', () => {
    const wrapper = mountCard('1. 完成接口开发\n2. 完成接口联调\n3. 整理验收材料')

    expect(pointCountText(wrapper)).toContain('3 个工作要点')
  })

  test('⚠️ 空白行**不计入**要点数', () => {
    // ⚠️ **业务语义**：``countWeekWorkPoints`` 数的是**非空行**。
    //    若改成数总行数，用户敲了几个空行就会看到虚高的数字。
    const wrapper = mountCard('1. 完成接口开发\n\n   \n2. 完成接口联调')

    expect(pointCountText(wrapper)).toContain('2 个工作要点')
  })

  test('⚠️⚠️ 纯空格的行**不计入**要点数', () => {
    const wrapper = mountCard('1. 一件事\n     \n2. 两件事')

    expect(pointCountText(wrapper)).toContain('2 个工作要点')
  })

  test('⚠️ 没有编号前缀的行也算一个要点', () => {
    // ⚠️ **计数不依赖编号格式** —— 用户不一定按「1. 2. 3.」写。
    const wrapper = mountCard('写周报\n和开需求会')

    expect(pointCountText(wrapper)).toContain('2 个工作要点')
  })

  test('⚠️ 计数随输入**实时更新**', async () => {
    // ⚠️ 绑定写错（``content`` → ``contents``）时，
    //    计数会永远停在旧值 ⇒ 这条能抓到。
    //
    // ⚠️ **必须用 ``setProps``** —— 直接改传进去的原始对象不生效：
    //    Vue 挂载时把 prop 转成了 reactive proxy，
    //    外部改**原始对象**不会触发渲染更新。
    const wrapper = mountCard('1. 一件事')

    expect(pointCountText(wrapper)).toContain('1 个工作要点')

    await wrapper.setProps({ content: '1. 一件事\n2. 两件事\n3. 三件事' })

    expect(pointCountText(wrapper)).toContain('3 个工作要点')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 只读态
// ═══════════════════════════════════════════════════════════════════════════

describe('只读态（editable=false）', () => {
  test('⚠️ editable=true → 渲染输入框', () => {
    const wrapper = mountCard('内容', { editable: true })

    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(wrapper.find('.day-plan-card__readonly').exists()).toBe(false)
  })

  test('⚠️ editable=false → 不渲染输入框，改用只读段落', () => {
    const wrapper = mountCard('内容', { editable: false })

    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.find('.day-plan-card__readonly').exists()).toBe(true)
  })

  test('⚠️ 只读段落展示已有内容', () => {
    const wrapper = mountCard('已有内容', { editable: false })

    expect(wrapper.find('.day-plan-card__readonly').text()).toContain('已有内容')
  })

  test('⚠️ 只读且内容为空 → 显示「未填写工作内容」', () => {
    const wrapper = mountCard('', { editable: false })

    expect(wrapper.find('.day-plan-card__readonly').text()).toContain('未填写工作内容')
  })

  test('⚠️ disabled=true → 输入框禁用', () => {
    const wrapper = mountCard('内容', { disabled: true })

    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
  })

  test('⚠️ disabled=false → 输入框可编辑', () => {
    const wrapper = mountCard('内容', { disabled: false })

    expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 错误提示
// ═══════════════════════════════════════════════════════════════════════════

describe('内容错误提示', () => {
  test('默认 → 不带 has-error，也不显示提示文案', () => {
    const wrapper = mountCard('', { showContentError: false })

    expect(wrapper.find('.day-plan-card').classes()).not.toContain('has-error')
    expect(wrapper.find('.day-plan-field__error').exists()).toBe(false)
  })

  test('⚠️ showContentError=true → 卡片带 has-error', () => {
    const wrapper = mountCard('', { showContentError: true })

    expect(wrapper.find('.day-plan-card').classes()).toContain('has-error')
  })

  test('⚠️ showContentError=true → 字段区带 is-error', () => {
    const wrapper = mountCard('', { showContentError: true })

    expect(wrapper.find('.day-plan-field').classes()).toContain('is-error')
  })

  test('⚠️ showContentError=true → 显示错误文案', () => {
    const wrapper = mountCard('', { showContentError: true })

    expect(wrapper.find('.day-plan-field__error').text()).toBe('请填写当天工作内容')
  })

  test('⚠️⚠️ 错误态与内容是否为空**无关**（由父组件决定）', () => {
    // ⚠️ 组件**不自己判断**内容为空要不要报错 ⇒
    //    即便有内容，只要父组件说「有问题」，错误提示照样显示。
    const wrapper = mountCard('我明明写了内容', { showContentError: true })

    expect(wrapper.find('.day-plan-field__error').exists()).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 展开 / 收起
// ═══════════════════════════════════════════════════════════════════════════

describe('展开与收起', () => {
  test('expanded=true → 卡片体可见', () => {
    const wrapper = mountCard('内容', { expanded: true })

    expect(wrapper.find('.day-plan-card__body').isVisible()).toBe(true)
  })

  test('expanded=false → 卡片体不可见（v-show 语义，仍在 DOM）', () => {
    const wrapper = mountCard('内容', { expanded: false })

    expect(wrapper.find('.day-plan-card__body').exists()).toBe(true)
    expect(wrapper.find('.day-plan-card__body').isVisible()).toBe(false)
  })

  test('⚠️ 点标题 → 发出 toggle 事件', () => {
    const wrapper = mountCard('内容')

    wrapper.find('.day-plan-card__toggle').trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })

  test('⚠️ 点工具按钮 → 也发出 toggle 事件', () => {
    // ⚠️ 两个入口都要能收起 —— 漏掉一个就是「点不动」。
    const wrapper = mountCard('内容')

    const toolbarBtn = wrapper.findAll('button').find((b) => b.attributes('aria-label')?.includes('日计划'))
    expect(toolbarBtn).toBeDefined()
    toolbarBtn!.trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })

  test('⚠️ 展开态与收起态的 aria-label 不同', () => {
    // ⚠️ **无障碍**：读屏用户靠 aria-label 判断当前能不能点。
    const expanded = mountCard('内容', { expanded: true })
    const collapsed = mountCard('内容', { expanded: false })

    const findLabel = (w: typeof expanded) =>
      w.findAll('button').map((b) => b.attributes('aria-label')).filter(Boolean)

    expect(findLabel(expanded)).toContain('收起日计划')
    expect(findLabel(collapsed)).toContain('展开日计划')
  })

  test('⚠️ 标题按钮的 aria-expanded 跟随展开态', () => {
    const expanded = mountCard('内容', { expanded: true })
    const collapsed = mountCard('内容', { expanded: false })

    expect(expanded.find('.day-plan-card__toggle').attributes('aria-expanded')).toBe('true')
    expect(collapsed.find('.day-plan-card__toggle').attributes('aria-expanded')).toBe('false')
  })
})
