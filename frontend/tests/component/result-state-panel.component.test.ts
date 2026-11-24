/**
 * 🔴 **组件测试示范**（CI-5 阶段 1）—— 成果状态面板。
 *
 * ⚠️ **选它的理由**
 * ------------------------------------------------------------------
 * ``ResultStatePanel.vue`` 是本项目里**状态分支最密**的小组件：
 * 5 种状态（loading / error / empty / no-results / no-access）
 * ⇒ 每种有**不同的图标、不同的标题、不同的默认文案、不同的按钮、不同的事件**。
 *
 * ⇒ 这类「状态 → 表现」的映射是**源码断言最抓不到**的：
 *    把 ``state === 'error'`` 写成 ``state === 'errror'``，
 *    源码里字符串还在，**读源码的测试照样全绿**，
 *    但用户看到的是「error 状态显示成了空状态」。
 *
 * ⚠️ **本文件还覆盖了「事件契约」**
 *    ``error`` 状态点按钮要发 ``retry``，``no-results`` 要发 ``clear`` ——
 *    发错事件 ⇒ 父组件不响应 ⇒ 按钮点了没反应。
 *    源码断言**完全测不到**事件名。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test } from 'vitest'

import ResultStatePanel from '../../src/components/results/ResultStatePanel.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 1. 状态 → 标题/文案
// ═══════════════════════════════════════════════════════════════════════════

describe('每个状态显示对应的标题与默认文案', () => {
  const cases = [
    { state: 'loading', title: '正在加载成果', message: '请稍候。' },
    { state: 'error', title: '成果加载失败', message: '请检查网络后重试。' },
    { state: 'empty', title: '还没有成果记录', message: '成果提交并完成审批后，会在这里形成档案。' },
    { state: 'no-results', title: '没有匹配的成果', message: '请调整筛选条件后重试。' },
    { state: 'no-access', title: '暂无成果权限', message: '当前账号没有查看该成果范围的权限。' },
  ] as const

  for (const item of cases) {
    test(`${item.state} → 「${item.title}」`, () => {
      const wrapper = mount(ResultStatePanel, { props: { state: item.state } })
      const text = wrapper.text()

      expect(text).toContain(item.title)
      expect(text).toContain(item.message)
    })
  }

  test('自定义 message 覆盖默认文案', () => {
    // ⚠️ **否定式断言** —— 覆盖后默认文案**不该**再出现。
    const wrapper = mount(ResultStatePanel, {
      props: { state: 'error', message: '后端说数据库连不上' },
    })

    expect(wrapper.text()).toContain('后端说数据库连不上')
    expect(wrapper.text()).not.toContain('请检查网络后重试。')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 状态 → 按钮（不同状态有**不同**的按钮）
// ═══════════════════════════════════════════════════════════════════════════

describe('只有需要动作的状态才有按钮', () => {
  test('error → 「重新加载」', () => {
    const wrapper = mount(ResultStatePanel, { props: { state: 'error' } })
    const labels = wrapper.findAll('button').map((b) => b.text())
    expect(labels).toContain('重新加载')
    expect(labels).not.toContain('清除筛选')
  })

  test('no-results → 「清除筛选」', () => {
    const wrapper = mount(ResultStatePanel, { props: { state: 'no-results' } })
    const labels = wrapper.findAll('button').map((b) => b.text())
    expect(labels).toContain('清除筛选')
    expect(labels).not.toContain('重新加载')
  })

  test('loading / empty / no-access → **没有**任何按钮', () => {
    // ⚠️ 三个状态都测 —— 因为分支写错时，通常只错其中一个。
    for (const state of ['loading', 'empty', 'no-access'] as const) {
      const wrapper = mount(ResultStatePanel, { props: { state } })
      expect(wrapper.findAll('button'), `${state} 不该有按钮`).toHaveLength(0)
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 状态 → 事件契约（发错事件 ⇒ 点了没反应）
// ═══════════════════════════════════════════════════════════════════════════

describe('按钮发出正确的事件', () => {
  test('error 的「重新加载」发 retry', async () => {
    const wrapper = mount(ResultStatePanel, { props: { state: 'error' } })

    await wrapper.find('button').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
    // ⚠️ 不能发错事件 —— 父组件监听的是 clear。
    expect(wrapper.emitted('clear')).toBeUndefined()
  })

  test('no-results 的「清除筛选」发 clear', async () => {
    const wrapper = mount(ResultStatePanel, { props: { state: 'no-results' } })

    await wrapper.find('button').trigger('click')

    expect(wrapper.emitted('clear')).toHaveLength(1)
    expect(wrapper.emitted('retry')).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 状态 → 语义 class / 无障碍属性
// ═══════════════════════════════════════════════════════════════════════════

describe('状态反映在语义标记上', () => {
  test('根元素带上 is-<state> 的 class', () => {
    const wrapper = mount(ResultStatePanel, { props: { state: 'no-access' } })
    expect(wrapper.classes()).toContain('is-no-access')
  })

  test('根元素有 role=status（读屏软件可播报）', () => {
    const wrapper = mount(ResultStatePanel, { props: { state: 'loading' } })
    expect(wrapper.attributes('role')).toBe('status')
  })
})
