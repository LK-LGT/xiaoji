/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— AI 月计划变更预览。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/month-plan-ai-diff-ui.test.ts``）：
 *
 * ```typescript
 * assert.match(source, /v-model="draft\.summary"/)
 * assert.match(source, /<details v-if="suggestion\?\.warnings\?\.length"/)
 * assert.match(source, /<el-alert v-for="warning/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   ① ``v-model="draft.summary"`` 只证明「源码里有这串字」——
 *      改成 ``v-model="draft.summry"`` 照样匹配得上；
 *   ② 更要命的是 **apply 的 mode**：
 *      ``emit('apply', { mode: append ? 'append' : 'replace', ... })``
 *      —— 若把 ``append`` 写成 ``replace``，
 *      **「补充」模式会变成「整体替换」⇒ 用户原有任务被覆盖**。
 *      而源码里 ``'append'`` / ``'replace'`` 两个字符串都还在，正则照样匹配得上。
 *
 * ⚠️ **本文件真正测的东西**
 *   1. **打开时默认全选**；
 *   2. **取消勾选 → 「应用到当前计划」按钮禁用**；
 *   3. ⭐⭐⭐ **apply 的 mode**：``SUPPLEMENT`` → ``append``，其他 → ``replace``；
 *   4. ⭐⭐ **「修改前」只在 OPTIMIZE / REVISE_REJECTION 模式显示**；
 *   5. **「有修改」标记的条件**；
 *   6. **警告区 / 知识引用区按有无数据显示**；
 *   7. **应用时只带上被勾选的任务**。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, test, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'

import MonthPlanAiDiff from '../../src/views/employee/components/MonthPlanAiDiff.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

/** AI 建议的一条任务。 */
function aiItem(overrides: Record<string, unknown> = {}) {
  return {
    taskName: '剪辑短视频',
    taskContent: '按脚本完成剪辑',
    deliverable: '成片',
    quality: '1080P',
    estimatedStartDate: '2026-11-16',
    estimatedEndDate: '2026-11-20',
    performanceWeight: 30,
    estimatedValue: 5000,
    ...overrides,
  }
}

function suggestion(overrides: Record<string, unknown> = {}) {
  return {
    suggestionId: 'SUG_1',
    items: [aiItem(), aiItem({ taskName: '调色' })],
    warnings: [] as string[],
    knowledgeReferences: [] as unknown[],
    ...overrides,
  }
}

function mountDiff(props: Record<string, unknown> = {}) {
  return mount(MonthPlanAiDiff, {
    props: {
      visible: true,
      operationMode: 'OPTIMIZE',
      suggestion: suggestion(),
      currentItems: [],
      ...props,
    },
  })
}

/** 建议卡片。 */
function cards(wrapper: ReturnType<typeof mountDiff>) {
  return wrapper.findAll('.suggestion-card')
}

/** 「应用到当前计划」按钮。 */
function applyButton(wrapper: ReturnType<typeof mountDiff>) {
  return wrapper.findAll('button').find((b) => b.text().includes('应用到当前计划'))
}

/**
 * 勾选 / 取消第 N 个复选框。
 *
 * ⚠️ **不能点 ``.el-checkbox`` 根元素** —— Element Plus 的 checkbox 是
 * **受控组件**（``:model-value`` + ``@change``），
 * change 事件由**内部的 input** 触发。
 * ⇒ 直接触发组件的 ``change`` 事件最稳。
 */
async function setChecked(wrapper: ReturnType<typeof mountDiff>, index: number, checked: boolean) {
  const box = wrapper.findAllComponents({ name: 'ElCheckbox' })[index]
  await box.vm.$emit('change', checked)
  await flushPromises()
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐⭐ 默认全选
// ═══════════════════════════════════════════════════════════════════════════

describe('打开时默认全选', () => {
  test('⚠️ 所有建议卡片默认都是选中态', () => {
    const wrapper = mountDiff()

    const list = cards(wrapper)
    expect(list).toHaveLength(2)
    for (const card of list) {
      expect(card.classes()).toContain('is-selected')
    }
  })

  test('⚠️ 复选框都勾上', () => {
    const wrapper = mountDiff()

    const checkboxes = wrapper.findAll('.el-checkbox')
    expect(checkboxes).toHaveLength(2)
    for (const box of checkboxes) {
      expect(box.classes()).toContain('is-checked')
    }
  })

  test('⚠️ 有建议时「应用到当前计划」可用', () => {
    const wrapper = mountDiff()

    expect(applyButton(wrapper)!.attributes('disabled')).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐⭐ 取消勾选 → 应用按钮禁用
// ═══════════════════════════════════════════════════════════════════════════

describe('取消勾选', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('⚠️ 取消勾选一个 → 该卡片不再是选中态', async () => {
    const wrapper = mountDiff()

    await setChecked(wrapper, 0, false)

    expect(cards(wrapper)[0].classes()).not.toContain('is-selected')
    expect(cards(wrapper)[1].classes()).toContain('is-selected')
  })

  test('⚠️ 全部取消 → 「应用到当前计划」**禁用**', async () => {
    const wrapper = mountDiff()

    await setChecked(wrapper, 0, false)
    await setChecked(wrapper, 1, false)

    expect(applyButton(wrapper)!.attributes('disabled')).toBeDefined()
  })

  test('⚠️ 取消后再勾回 → 按钮恢复可用', async () => {
    const wrapper = mountDiff()

    // ⚠️ **全部取消** ⇒ 才禁用（还剩一个被选中时按钮本来就该可用）。
    await setChecked(wrapper, 0, false)
    await setChecked(wrapper, 1, false)
    expect(applyButton(wrapper)!.attributes('disabled')).toBeDefined()

    // ⚠️ 勾回第一个 ⇒ 恢复可用。
    await setChecked(wrapper, 0, true)
    expect(applyButton(wrapper)!.attributes('disabled')).toBeUndefined()
    expect(cards(wrapper)[0].classes()).toContain('is-selected')
  })

  test('⚠️ 只取消一个（还剩一个被选中）→ 按钮**仍可用**', async () => {
    // ⚠️ 这条容易被写错：取消 1 个 ≠ 全部取消。
    const wrapper = mountDiff()

    await setChecked(wrapper, 0, false)

    expect(applyButton(wrapper)!.attributes('disabled')).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐⭐⭐ apply 的 mode —— 最关键的一条
// ═══════════════════════════════════════════════════════════════════════════

describe('应用时的写入模式', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('⚠️ SUPPLEMENT（补充）→ mode = **append**', async () => {
    // ⚠️⭐⭐⭐ **写反就会覆盖用户原有任务** ——
    //    「补充」应当是追加，绝不能是整体替换。
    const wrapper = mountDiff({ operationMode: 'SUPPLEMENT' })

    await applyButton(wrapper)!.trigger('click')

    const payload = wrapper.emitted('apply')![0][0] as { mode: string }
    expect(payload.mode).toBe('append')
  })

  test('⚠️ OPTIMIZE（优化）→ mode = **replace**', async () => {
    const wrapper = mountDiff({ operationMode: 'OPTIMIZE' })

    await applyButton(wrapper)!.trigger('click')

    const payload = wrapper.emitted('apply')![0][0] as { mode: string }
    expect(payload.mode).toBe('replace')
  })

  test('⚠️ REVISE_REJECTION（按驳回意见改）→ mode = **replace**', async () => {
    const wrapper = mountDiff({ operationMode: 'REVISE_REJECTION' })

    await applyButton(wrapper)!.trigger('click')

    const payload = wrapper.emitted('apply')![0][0] as { mode: string }
    expect(payload.mode).toBe('replace')
  })

  test('⚠️ apply 时只带上**被勾选**的任务', async () => {
    const wrapper = mountDiff({ operationMode: 'SUPPLEMENT' })

    // ⚠️ 取消第一个。
    await setChecked(wrapper, 0, false)

    await applyButton(wrapper)!.trigger('click')

    const payload = wrapper.emitted('apply')![0][0] as {
      indexes: number[]
      items: Array<{ taskName: string }>
    }
    expect(payload.indexes).toEqual([1])
    expect(payload.items).toHaveLength(1)
    expect(payload.items[0].taskName).toBe('调色')
  })

  test('⚠️ apply 时带上 adoptedWithEdit=false（表示未经人工编辑）', async () => {
    const wrapper = mountDiff()

    await applyButton(wrapper)!.trigger('click')

    const payload = wrapper.emitted('apply')![0][0] as { adoptedWithEdit: boolean }
    expect(payload.adoptedWithEdit).toBe(false)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐⭐ 「修改前」只在特定模式显示
// ═══════════════════════════════════════════════════════════════════════════

describe('「修改前」的显示条件', () => {
  test('⚠️ OPTIMIZE → 显示「修改前」区块', () => {
    const wrapper = mountDiff({
      operationMode: 'OPTIMIZE',
      currentItems: [{ id: '1', ...aiItem({ taskName: '旧任务名' }) }],
    })

    expect(wrapper.find('.current-copy').exists()).toBe(true)
    expect(wrapper.text()).toContain('修改前')
  })

  test('⚠️ REVISE_REJECTION → 显示「修改前」区块', () => {
    const wrapper = mountDiff({
      operationMode: 'REVISE_REJECTION',
      currentItems: [{ id: '1', ...aiItem({ taskName: '旧任务名' }) }],
    })

    expect(wrapper.find('.current-copy').exists()).toBe(true)
  })

  test('⚠️ SUPPLEMENT → **不显示**「修改前」', () => {
    // ⚠️ 「补充」模式本来就没有"原来的东西"，显示「修改前」是误导。
    const wrapper = mountDiff({
      operationMode: 'SUPPLEMENT',
      currentItems: [{ id: '1', ...aiItem({ taskName: '旧任务名' }) }],
    })

    expect(wrapper.find('.current-copy').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('修改前')
  })

  test('⚠️ SUPPLEMENT → 卡片显示「新增」标签', () => {
    const wrapper = mountDiff({ operationMode: 'SUPPLEMENT' })

    expect(wrapper.text()).toContain('新增')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 「有修改」标记
// ═══════════════════════════════════════════════════════════════════════════

describe('「有修改」标记', () => {
  test('⚠️ 内容不同 → 标记「有修改」', () => {
    const wrapper = mountDiff({
      operationMode: 'OPTIMIZE',
      // ⚠️ currentItems 要与建议**逐条对应**（按 index 比较）。
      currentItems: [
        { id: '1', ...aiItem({ taskName: '完全不同的名字' }) },
        { id: '2', ...aiItem({ taskName: '调色' }) },
      ],
    })

    // ⚠️ 只看**第一张卡** —— 第二张没改，不该标记。
    expect(cards(wrapper)[0].text()).toContain('有修改')
    expect(cards(wrapper)[1].text()).not.toContain('有修改')
  })

  test('⚠️ 内容相同 → **不**标记「有修改」', () => {
    const wrapper = mountDiff({
      operationMode: 'OPTIMIZE',
      // ⚠️ 除 id 外与建议完全一致 ⇒ 没改。
      currentItems: [
        { id: '1', ...aiItem() },
        { id: '2', ...aiItem({ taskName: '调色' }) },
      ],
    })

    expect(cards(wrapper)[0].text()).not.toContain('有修改')
    expect(cards(wrapper)[1].text()).not.toContain('有修改')
  })

  test('⚠️ 没有对应原任务 → 视为「有修改」', () => {
    const wrapper = mountDiff({ operationMode: 'OPTIMIZE', currentItems: [] })

    expect(cards(wrapper)[0].text()).toContain('有修改')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. 警告区 / 知识引用区
// ═══════════════════════════════════════════════════════════════════════════

describe('警告与知识引用', () => {
  test('⚠️ 没有警告 → 不显示警告区', () => {
    const wrapper = mountDiff({ suggestion: suggestion({ warnings: [] }) })

    expect(wrapper.find('.warning-summary').exists()).toBe(false)
  })

  test('⚠️ 有警告 → 显示条数与内容', () => {
    const wrapper = mountDiff({
      suggestion: suggestion({ warnings: ['缺少验收标准', '权重偏重'] }),
    })

    expect(wrapper.find('.warning-summary').exists()).toBe(true)
    expect(wrapper.text()).toContain('AI 参考提醒 2 条')
    expect(wrapper.text()).toContain('缺少验收标准')
  })

  test('⚠️ 有知识引用 → 显示份数与绑定类型', () => {
    const wrapper = mountDiff({
      suggestion: suggestion({
        knowledgeReferences: [
          { knowledgeId: 'K1', version: 'v2', title: '剪辑规范', binding: 'NORMATIVE' },
        ],
      }),
    })

    expect(wrapper.text()).toContain('本次按分类召回 1 份企业知识')
    expect(wrapper.text()).toContain('剪辑规范')
    // ⚠️ 绑定类型要翻译成中文。
    expect(wrapper.text()).toContain('正式规范')
  })

  test('⚠️ 无建议 → 显示空状态', () => {
    const wrapper = mountDiff({ suggestion: suggestion({ items: [] }) })

    expect(wrapper.text()).toContain('AI 没有返回可应用的任务')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. 事件契约
// ═══════════════════════════════════════════════════════════════════════════

describe('事件契约', () => {
  test('⚠️ 点「返回助手」→ 发 close', async () => {
    const wrapper = mountDiff()

    await wrapper.findAll('button').find((b) => b.text().includes('返回助手'))!.trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  test('⚠️ 点「放弃本次建议」→ 发 ignore', async () => {
    const wrapper = mountDiff()

    await wrapper.findAll('button').find((b) => b.text().includes('放弃本次建议'))!.trigger('click')

    expect(wrapper.emitted('ignore')).toHaveLength(1)
  })

  test('⚠️ 重新打开（suggestionId 变）→ 重新全选', async () => {
    // ⚠️ **幂等/重置语义** —— 换一批建议时，勾选状态必须重置，
    //    否则会带着上一批的索引去应用新一批 ⇒ 应用错数据。
    const wrapper = mountDiff()

    // ⚠️ 取消第一个。
    await setChecked(wrapper, 0, false)
    expect(cards(wrapper)[0].classes()).not.toContain('is-selected')

    // ⚠️ 换一份建议。
    await wrapper.setProps({
      suggestion: suggestion({ suggestionId: 'SUG_2', items: [aiItem({ taskName: '新建议' })] }),
    })
    await flushPromises()

    expect(cards(wrapper)).toHaveLength(1)
    expect(cards(wrapper)[0].classes()).toContain('is-selected')
  })
})
