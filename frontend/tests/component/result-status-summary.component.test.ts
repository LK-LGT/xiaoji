/**
 * 🔴 **组件测试示范**（CI-5 阶段 1）—— 成果状态统计条。
 *
 * ⚠️ **选它的理由**
 * ------------------------------------------------------------------
 * ``ResultStatusSummary.vue`` 是 4 个统计卡片的点击筛选器：
 * 每个卡片显示一个计数，点一下就 ``emit('select', <状态>)``。
 *
 * 这类组件的 bug 几乎总是这几种：
 * 1. **点错卡片发错状态**（比如「审批中」发成了 ``IN_REVIEW`` 之外的值）；
 * 2. **计数和标签错位**（``counts.todo`` 的数字配上了「审批中」的文案）；
 * 3. **active 高亮错位**（选中「待填写」却高亮「已完成」）。
 *
 * ⇒ 三种都**必须挂载才能测**。
 * ⇒ 而「读源码断言」对这三类 bug **一个都抓不到**
 *    （它只能确认 ``emit('select', 'TODO')`` 这个字符串在文件里）。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test } from 'vitest'

import ResultStatusSummary from '../../src/components/results/ResultStatusSummary.vue'

const counts = { todo: 3, returned: 1, inReview: 7, done: 12 }

function mountSummary(active?: 'TODO' | 'RETURNED' | 'IN_REVIEW' | 'DONE') {
  return mount(ResultStatusSummary, { props: { counts, active } })
}

/** 拿到 4 个卡片按钮（按模板里的顺序）。 */
function cards(wrapper: ReturnType<typeof mountSummary>) {
  return wrapper.findAll('button')
}

describe('四个状态各显示一个计数与文案', () => {
  test('计数与文案正确对应', () => {
    const wrapper = mountSummary()
    const texts = cards(wrapper).map((b) => b.text())

    expect(texts[0]).toBe('3待填写')
    expect(texts[1]).toBe('1已退回')
    expect(texts[2]).toBe('7审批中')
    expect(texts[3]).toBe('12已完成')
  })

  test('四个卡片都有「类型」标记（不是 div）', () => {
    // ⚠️ **语义断言** —— 键盘能聚焦、读屏能播报。
    const wrapper = mountSummary()
    for (const card of cards(wrapper)) {
      expect(card.attributes('type')).toBe('button')
    }
  })
})

describe('点击卡片发出对应的状态', () => {
  const cases = [
    { index: 0, status: 'TODO' },
    { index: 1, status: 'RETURNED' },
    { index: 2, status: 'IN_REVIEW' },
    { index: 3, status: 'DONE' },
  ] as const

  for (const item of cases) {
    test(`第 ${item.index + 1} 个卡片 → ${item.status}`, async () => {
      const wrapper = mountSummary()

      await cards(wrapper)[item.index].trigger('click')

      const emitted = wrapper.emitted('select')
      expect(emitted).toHaveLength(1)
      expect(emitted![0]).toEqual([item.status])
    })
  }
})

describe('active 高亮跟着状态走', () => {
  test('选中 TODO 时只有第一张卡带 is-active', () => {
    const wrapper = mountSummary('TODO')
    const actives = cards(wrapper).map((c) => c.classes().includes('is-active'))

    expect(actives).toEqual([true, false, false, false])
  })

  test('选中 DONE 时只有最后一张卡带 is-active', () => {
    const wrapper = mountSummary('DONE')
    const actives = cards(wrapper).map((c) => c.classes().includes('is-active'))

    expect(actives).toEqual([false, false, false, true])
  })

  test('没有 active 时不高亮任何一张', () => {
    const wrapper = mountSummary()
    const actives = cards(wrapper).map((c) => c.classes().includes('is-active'))

    expect(actives).toEqual([false, false, false, false])
  })
})
