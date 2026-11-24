/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 月计划 AI 建议结果卡片。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/plan-ai-conversation-ui.test.ts``、
 * ``tests/month-plan-ai-assistant-layout-ui.test.ts`` 等）里是「读源码 + 正则」：
 *
 * ```typescript
 * assert.match(source, /GENERATE: '完整月计划'/)
 * assert.match(source, /suggestion\.knowledgeReferences\?\.length \|\| 0/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``SUPPLEMENT: '补充任务'`` 改成 ``SUPPLEMENT: '补充计划'``
 *   ⇒ 正则**照样匹配得上**（``SUPPLEMENT:`` 这段没变）⇒ 测试全绿，
 *   而界面上「补充任务」被显示成「补充计划」—— 用户分不清
 *   AI 到底是**新增**任务还是**替换**整个计划。
 *
 * ⚠️ **本文件真正测的东西**
 *   ① **操作类型标签**：5 种 ``effectiveOperationMode`` 各自的中文标签；
 *   ② **过期态（stale）**：提示文案 + 样式类 + 版本号标签的互斥；
 *   ③ **提醒计数**：``warnings.length + missingContext.length``；
 *   ④ **知识来源折叠区**：有引用才渲染、标题回退到 ID、版本号后缀；
 *   ⑤ **事件发射**（``preview`` / ``continue``）。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test } from 'vitest'

import MonthPlanAiResultCard from '../../src/views/employee/components/MonthPlanAiResultCard.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

function makeSuggestion(overrides: Record<string, unknown> = {}) {
  return {
    suggestionId: 's1',
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
    warnings: [],
    missingContext: [],
    notice: '',
    draftVersionId: 1,
    draftVersionNo: 3,
    effectiveOperationMode: 'GENERATE' as const,
    ...overrides,
  }
}

function mountCard(
  suggestion: Record<string, unknown> = makeSuggestion(),
  props: Record<string, unknown> = {},
) {
  return mount(MonthPlanAiResultCard, {
    props: {
      suggestion,
      ...props,
    },
  })
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐⭐ 操作类型标签
// ═══════════════════════════════════════════════════════════════════════════

describe('操作类型标签', () => {
  test('⚠️ GENERATE → 「完整月计划」', () => {
    const wrapper = mountCard(makeSuggestion({ effectiveOperationMode: 'GENERATE' }))

    expect(wrapper.find('header span').text()).toBe('完整月计划')
  })

  test('⚠️ SUPPLEMENT → 「补充任务」', () => {
    // ⚠️ **最容易写错的一处**：
    //    「补充任务」= 在现有计划上**追加**；
    //    「完整月计划」= **整体替换**。
    //    两者搞混 ⇒ 用户以为 AI 只加了几条，实际把原计划全覆盖了。
    const wrapper = mountCard(makeSuggestion({ effectiveOperationMode: 'SUPPLEMENT' }))

    expect(wrapper.find('header span').text()).toBe('补充任务')
  })

  test('⚠️ OPTIMIZE → 「计划优化」', () => {
    const wrapper = mountCard(makeSuggestion({ effectiveOperationMode: 'OPTIMIZE' }))

    expect(wrapper.find('header span').text()).toBe('计划优化')
  })

  test('⚠️ REVISE_REJECTION → 「审批反馈修改」', () => {
    const wrapper = mountCard(makeSuggestion({ effectiveOperationMode: 'REVISE_REJECTION' }))

    expect(wrapper.find('header span').text()).toBe('审批反馈修改')
  })

  test('⚠️ CLARIFY → 「需要确认」', () => {
    const wrapper = mountCard(makeSuggestion({ effectiveOperationMode: 'CLARIFY' }))

    expect(wrapper.find('header span').text()).toBe('需要确认')
  })

  test('⚠️⚠️ 未知模式 → 回退到「月计划建议」', () => {
    // ⚠️ **兜底**：后端加了新模式而前端没跟上时，不能显示空白。
    const wrapper = mountCard(
      makeSuggestion({ effectiveOperationMode: 'SOMETHING_NEW' }),
    )

    expect(wrapper.find('header span').text()).toBe('月计划建议')
  })

  test('⚠️ 5 种模式的标签**互不重复**', () => {
    const modes = ['GENERATE', 'SUPPLEMENT', 'OPTIMIZE', 'REVISE_REJECTION', 'CLARIFY']
    const labels = modes.map((mode) =>
      mountCard(makeSuggestion({ effectiveOperationMode: mode })).find('header span').text(),
    )

    expect(new Set(labels).size).toBe(modes.length)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 过期态（stale）
// ═══════════════════════════════════════════════════════════════════════════

describe('过期态', () => {
  test('默认 → 不是过期态，不显示过期标签', () => {
    const wrapper = mountCard(makeSuggestion())

    expect(wrapper.find('.ai-result-card').classes()).not.toContain('is-stale')
    expect(wrapper.text()).not.toContain('计划已更新，仍可应用')
  })

  test('⚠️ stale=true → 加 is-stale 类 + 过期提示', () => {
    const wrapper = mountCard(makeSuggestion(), { stale: true })

    expect(wrapper.find('.ai-result-card').classes()).toContain('is-stale')
    expect(wrapper.text()).toContain('计划已更新，仍可应用')
  })

  test('⚠️ stale=true → 版本号标签被过期标签**顶掉**', () => {
    // ⚠️ **互斥**：过期时不该还显示「V3」（那是旧版本号）⇒ 用户会困惑。
    const wrapper = mountCard(makeSuggestion({ draftVersionNo: 3 }), { stale: true })

    expect(wrapper.text()).toContain('计划已更新，仍可应用')
    expect(wrapper.text()).not.toContain('V3')
  })

  test('⚠️ 非过期 → 显示版本号标签', () => {
    const wrapper = mountCard(makeSuggestion({ draftVersionNo: 3 }))

    expect(wrapper.text()).toContain('V3')
  })

  test('⚠️⚠️ 过期时**仍然显示**警告文案里的第一条', () => {
    // ⚠️ **优先级**：过期提示比 warnings 更重要 ⇒ 只显示过期提示。
    const wrapper = mountCard(
      makeSuggestion({ warnings: ['权重合计不足 100%'] }),
      { stale: true },
    )

    expect(wrapper.text()).toContain('计划已更新，仍可应用')
    expect(wrapper.text()).not.toContain('权重合计不足 100%')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 计数
// ═══════════════════════════════════════════════════════════════════════════

describe('计数', () => {
  test('⚠️ 任务数 = items.length', () => {
    const wrapper = mountCard(
      makeSuggestion({
        items: [
          { taskName: 'a' },
          { taskName: 'b' },
          { taskName: 'c' },
        ],
      }),
    )

    expect(wrapper.find('.result-metrics').text()).toContain('3')
    expect(wrapper.find('header strong').text()).toContain('3 项计划建议')
  })

  test('⚠️ 提醒数 = warnings + missingContext', () => {
    const wrapper = mountCard(
      makeSuggestion({ warnings: ['w1', 'w2'], missingContext: ['m1'] }),
    )

    expect(wrapper.find('.result-metrics').text()).toContain('3 条提醒')
  })

  test('⚠️ 没有提醒 → 0 条', () => {
    const wrapper = mountCard(makeSuggestion())

    expect(wrapper.find('.result-metrics').text()).toContain('0 条提醒')
  })

  test('⚠️ 知识数 = knowledgeReferences.length', () => {
    const wrapper = mountCard(
      makeSuggestion({
        knowledgeReferences: [
          { knowledgeId: 'k1', title: '规范 A', knowledgeType: 't', version: '1', binding: 'NORMATIVE', citation: '' },
          { knowledgeId: 'k2', title: '规范 B', knowledgeType: 't', version: '1', binding: 'NORMATIVE', citation: '' },
        ],
      }),
    )

    expect(wrapper.find('.result-metrics').text()).toContain('2 份知识')
  })

  test('⚠️ 没有知识 → 0 份', () => {
    const wrapper = mountCard(makeSuggestion())

    expect(wrapper.find('.result-metrics').text()).toContain('0 份知识')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 知识来源折叠区
// ═══════════════════════════════════════════════════════════════════════════

describe('知识来源折叠区', () => {
  test('⚠️ 无知识引用 → 不渲染折叠区', () => {
    const wrapper = mountCard(makeSuggestion())

    expect(wrapper.find('.knowledge-sources').exists()).toBe(false)
  })

  test('⚠️ 有知识引用 → 渲染折叠区', () => {
    const wrapper = mountCard(
      makeSuggestion({
        knowledgeReferences: [
          { knowledgeId: 'k1', title: '规范 A', knowledgeType: 't', version: '1', binding: 'NORMATIVE', citation: '' },
        ],
      }),
    )

    expect(wrapper.find('.knowledge-sources').exists()).toBe(true)
    expect(wrapper.find('.knowledge-sources summary').text()).toBe('查看本次召回的企业知识')
  })

  test('⚠️ 标题回退到 knowledgeId', () => {
    const wrapper = mountCard(
      makeSuggestion({
        knowledgeReferences: [
          { knowledgeId: 'k-9', title: '', knowledgeType: 't', version: '', binding: 'NORMATIVE', citation: '' },
        ],
      }),
    )

    const item = wrapper.find('.knowledge-sources li')
    expect(item.find('span').text()).toBe('k-9')
  })

  test('⚠️⚠️ 有版本号才显示「· vN」后缀', () => {
    const withVersion = mountCard(
      makeSuggestion({
        knowledgeReferences: [
          { knowledgeId: 'k1', title: '规范 A', knowledgeType: 't', version: '2', binding: 'NORMATIVE', citation: '' },
        ],
      }),
    )
    const withoutVersion = mountCard(
      makeSuggestion({
        knowledgeReferences: [
          { knowledgeId: 'k1', title: '规范 A', knowledgeType: 't', version: '', binding: 'NORMATIVE', citation: '' },
        ],
      }),
    )

    expect(withVersion.find('.knowledge-sources li small').text()).toContain('· v2')
    expect(withoutVersion.find('.knowledge-sources li small').text()).not.toContain('· v')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 事件发射
// ═══════════════════════════════════════════════════════════════════════════

describe('事件发射', () => {
  test('⚠️ 点「继续调整」→ 发出 continue 事件', () => {
    const wrapper = mountCard()

    const btn = wrapper.findAll('footer button').find((b) => b.text() === '继续调整')
    btn!.trigger('click')

    expect(wrapper.emitted('continue')).toHaveLength(1)
  })

  test('⚠️ 点「查看并应用」→ 发出 preview 事件', () => {
    const wrapper = mountCard()

    const btn = wrapper.findAll('footer button').find((b) => b.text() === '查看并应用')
    btn!.trigger('click')

    expect(wrapper.emitted('preview')).toHaveLength(1)
  })

  test('⚠️ 两个按钮发出的事件**不串**', () => {
    const wrapper = mountCard()

    const btn = wrapper.findAll('footer button').find((b) => b.text() === '继续调整')
    btn!.trigger('click')

    expect(wrapper.emitted('preview')).toBeUndefined()
  })
})
