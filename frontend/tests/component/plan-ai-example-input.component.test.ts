/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 计划示例输入（上传 / 粘贴）。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/month-plan-ai-conversation-workspace-ui.test.ts`` 等）里是
 * 「读源码 + 正则」：
 *
 * ```typescript
 * assert.match(source, /file\.size > 10 \* 1024 \* 1024/)
 * assert.match(source, /emit\('update:modelValue', \{ fileName: '粘贴内容', content \}\)/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``10 * 1024 * 1024`` 改成 ``100 * 1024 * 1024``
 *   ⇒ 正则**照样匹配得上**（``* 1024 * 1024`` 这段没变）⇒ 测试全绿，
 *   而「10MB 上限」变成了 100MB ⇒ 用户传超大文件 ⇒ 服务端 OOM。
 *
 * ⚠️ **本文件真正测的东西**
 *   ① **上传**：选文件 → 调 API → 回填 ``update:modelValue``；
 *   ② **10MB 上限**：超限直接拒绝，**不发请求**；
 *   ③ **粘贴**：trim 后非空才生效，文件名固定为「粘贴内容」；
 *   ④ **清除**：发出 ``update:modelValue = undefined``；
 *   ⑤ **只读态**：``disabled`` 时按钮禁用、且不打开文件选择器。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, test, vi } from 'vitest'

import PlanAiExampleInput from '../../src/views/employee/components/PlanAiExampleInput.vue'

// ═══════════════════════════════════════════════════════════════════════════
// API mock
// ═══════════════════════════════════════════════════════════════════════════

const extractPlanExampleApi = vi.fn()

vi.mock('@/api/employeeAi', () => ({
  extractPlanExampleApi: (...args: unknown[]) => extractPlanExampleApi(...args),
}))

beforeEach(() => {
  extractPlanExampleApi.mockReset()
  extractPlanExampleApi.mockResolvedValue({
    fileName: '示例.docx',
    content: '示例内容',
  })
})

// ⚠️ **``el-popover`` 把内容 teleport 到 ``document.body``** ⇒
//    上一条用例残留的面板会**留在 body 里** ⇒ 后续用例会找到「上一个」的节点。
// ⇒ 每条用例前把 body 里的 popover 残留清掉。
afterEach(() => {
  document.body.querySelectorAll('.el-popper, .el-popover').forEach((el) => el.remove())
})

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

function mountInput(props: Record<string, unknown> = {}) {
  return mount(PlanAiExampleInput, {
    props: {
      modelValue: undefined,
      disabled: false,
      'onUpdate:modelValue': () => {},
      ...props,
    },
  })
}

/** 隐藏的文件输入框。 */
function fileInput(wrapper: ReturnType<typeof mountInput>) {
  return wrapper.find('input[type="file"]')
}

/** 「上传示例」按钮。 */
function uploadButton(wrapper: ReturnType<typeof mountInput>) {
  return wrapper.findAll('button').find((b) => b.text() === '上传示例')
}

/**
 * ⚠️ **粘贴面板在 ``el-popover`` 里** ⇒ 它被**传送到 body**（teleport）
 *    且**只在展开时才渲染**。
 *
 * ⇒ 断言必须：
 *   ① 先点「粘贴示例」触发展开；
 *   ② 再到 ``document.body`` 里找（不在 wrapper 内）。
 */
async function openPastePanel(wrapper: ReturnType<typeof mountInput>) {
  const pasteBtn = wrapper.findAll('button').find((b) => b.text() === '粘贴示例')
  await pasteBtn!.trigger('click')
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 50))
  await flushPromises()
}

/** 在 body（含 teleport 出去的内容）里找粘贴面板的文本域。 */
function pasteTextarea(): HTMLTextAreaElement {
  const el = document.querySelector('.paste-panel textarea') as HTMLTextAreaElement | null
  if (!el) throw new Error('粘贴面板未渲染（el-popover 可能没展开）')
  return el
}

/** 在 body 里找粘贴面板的按钮。 */
function pastePanelButton(label: string): HTMLButtonElement {
  const buttons = Array.from(
    document.querySelectorAll('.paste-panel button'),
  ) as HTMLButtonElement[]
  const found = buttons.find((b) => b.textContent?.trim() === label)
  if (!found) throw new Error(`粘贴面板里找不到按钮「${label}」`)
  return found
}

/** 粘贴面板是否存在。 */
function pastePanelExists(): boolean {
  return document.querySelector('.paste-panel') !== null
}

/** 造一个指定大小的 File。 */
function makeFile(name: string, size: number): File {
  const file = new File(['x'], name)
  // ⚠️ jsdom 的 ``File`` 不支持构造时指定 size ⇒ 用 defineProperty 覆盖。
  Object.defineProperty(file, 'size', { value: size, configurable: true })
  return file
}

/** 触发文件选择。 */
async function selectFile(
  wrapper: ReturnType<typeof mountInput>,
  file: File,
) {
  const input = fileInput(wrapper)
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 上传
// ═══════════════════════════════════════════════════════════════════════════

describe('上传示例', () => {
  test('⚠️ 选文件 → 调用提取接口', async () => {
    const wrapper = mountInput()

    await selectFile(wrapper, makeFile('示例.docx', 1024))

    expect(extractPlanExampleApi).toHaveBeenCalledTimes(1)
  })

  test('⚠️ 上传成功 → 发出 update:modelValue', async () => {
    const wrapper = mountInput()

    await selectFile(wrapper, makeFile('示例.docx', 1024))

    expect(wrapper.emitted('update:modelValue')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')![0][0]).toEqual({
      fileName: '示例.docx',
      content: '示例内容',
    })
  })

  test('⚠️ 上传成功 → 展示文件名与字数', async () => {
    const wrapper = mountInput()

    await selectFile(wrapper, makeFile('示例.docx', 1024))

    // ⚠️ **组件是受控的**：``modelValue`` 由父组件持有，
    //    组件自己只 ``emit('update:modelValue', ...)`` ⇒
    //    上传成功后**必须把 prop 回灌进去**，卡片才会显示。
    await wrapper.setProps({
      modelValue: { fileName: '示例.docx', content: '示例内容' },
    })

    expect(wrapper.find('.example-file strong').text()).toBe('示例.docx')
    expect(wrapper.find('.example-file span').text()).toContain('已读取 4 字')
  })

  test('⚠️⚠️ 超长内容被截断 → 追加「，超长部分已省略」', async () => {
    extractPlanExampleApi.mockResolvedValue({
      fileName: '长文档.pdf',
      content: '一二三四五',
      truncated: true,
    })
    const wrapper = mountInput()

    await selectFile(wrapper, makeFile('长文档.pdf', 1024))
    await wrapper.setProps({
      modelValue: { fileName: '长文档.pdf', content: '一二三四五', truncated: true },
    })

    expect(wrapper.find('.example-file span').text()).toContain('超长部分已省略')
  })

  test('⚠️ 上传失败 → 不发出 update:modelValue', async () => {
    extractPlanExampleApi.mockRejectedValue(new Error('文件解析失败'))
    const wrapper = mountInput()

    await selectFile(wrapper, makeFile('坏文件.docx', 1024))

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  test('⚠️⚠️ 超过 10MB → **不调用**接口', async () => {
    // ⚠️ **上限必须在客户端拦** ⇒ 否则超大文件已经传上去了才被拒。
    const wrapper = mountInput()

    await selectFile(wrapper, makeFile('超大文件.pdf', 10 * 1024 * 1024 + 1))

    expect(extractPlanExampleApi).not.toHaveBeenCalled()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  test('⚠️⚠️ 正好 10MB → 允许上传（边界不含等号）', async () => {
    const wrapper = mountInput()

    await selectFile(wrapper, makeFile('刚好10MB.pdf', 10 * 1024 * 1024))

    expect(extractPlanExampleApi).toHaveBeenCalledTimes(1)
  })

  test('⚠️ 没有选文件 → 不调用接口', async () => {
    const wrapper = mountInput()

    const input = fileInput(wrapper)
    Object.defineProperty(input.element, 'files', { value: [], configurable: true })
    await input.trigger('change')
    await flushPromises()

    expect(extractPlanExampleApi).not.toHaveBeenCalled()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 粘贴
// ═══════════════════════════════════════════════════════════════════════════

describe('粘贴示例', () => {
  test('⚠️ 「使用此示例」默认禁用（内容为空）', async () => {
    const wrapper = mountInput()

    await openPastePanel(wrapper)

    expect(pastePanelButton('使用此示例').disabled).toBe(true)
  })

  test('⚠️ 填入内容 → 「使用此示例」可用', async () => {
    const wrapper = mountInput()

    await openPastePanel(wrapper)
    pasteTextarea().value = '示例计划内容'
    pasteTextarea().dispatchEvent(new Event('input'))
    await flushPromises()

    expect(pastePanelButton('使用此示例').disabled).toBe(false)
  })

  test('⚠️ 只有空白 → 仍禁用', async () => {
    const wrapper = mountInput()

    await openPastePanel(wrapper)
    pasteTextarea().value = '   \n  '
    pasteTextarea().dispatchEvent(new Event('input'))
    await flushPromises()

    expect(pastePanelButton('使用此示例').disabled).toBe(true)
  })

  test('⚠️ 点「使用此示例」→ 发出 update:modelValue，文件名固定为「粘贴内容」', async () => {
    const wrapper = mountInput()

    await openPastePanel(wrapper)
    pasteTextarea().value = '  示例计划内容  '
    pasteTextarea().dispatchEvent(new Event('input'))
    await flushPromises()

    pastePanelButton('使用此示例').click()
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')![0][0]).toEqual({
      fileName: '粘贴内容',
      content: '示例计划内容',
    })
  })

  test('⚠️⚠️ 粘贴内容被 trim（前后空白去掉）', async () => {
    const wrapper = mountInput()

    await openPastePanel(wrapper)
    pasteTextarea().value = '\n  示例计划内容  \n'
    pasteTextarea().dispatchEvent(new Event('input'))
    await flushPromises()

    pastePanelButton('使用此示例').click()
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')![0][0]).toMatchObject({
      content: '示例计划内容',
    })
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 清除
// ═══════════════════════════════════════════════════════════════════════════

describe('清除示例', () => {
  test('⚠️ 点「移除示例」→ 发出 update:modelValue = undefined', async () => {
    const wrapper = mountInput({
      modelValue: { fileName: '示例.docx', content: '内容' },
    })

    const removeBtn = wrapper.findAll('button').find((b) => b.attributes('aria-label') === '移除示例')
    expect(removeBtn).toBeDefined()
    removeBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')![0][0]).toBeUndefined()
  })

  test('⚠️ 无示例 → 显示提示文案，不显示文件卡片', () => {
    const wrapper = mountInput({ modelValue: undefined })

    expect(wrapper.find('.example-file').exists()).toBe(false)
    expect(wrapper.find('.example-note').exists()).toBe(true)
    expect(wrapper.find('.example-note').text()).toContain('支持 Word、PDF、Excel')
  })

  test('⚠️ 有示例 → 显示文件卡片，不显示提示文案', () => {
    const wrapper = mountInput({
      modelValue: { fileName: '示例.docx', content: '内容' },
    })

    expect(wrapper.find('.example-file').exists()).toBe(true)
    expect(wrapper.find('.example-note').exists()).toBe(false)
  })

  test('⚠️⚠️ 「粘贴内容」来源 → prop 变化时回填进粘贴框', async () => {
    // ⚠️ **watch 没有 ``immediate: true``** ⇒ 首次挂载**不回填**，
    //    只有 prop **发生变化**时才回填。
    //
    //    ⇒ 这里测的正是「变化时回填」这条路径（真实场景：
    //    用户在别处粘贴 → 父组件把内容传进来 → 粘贴框要能看到）。
    const wrapper = mountInput()
    await openPastePanel(wrapper)

    await wrapper.setProps({
      modelValue: { fileName: '粘贴内容', content: '之前粘贴的正文' },
    })
    await flushPromises()

    expect(pasteTextarea().value).toBe('之前粘贴的正文')
  })

  test('⚠️ 非「粘贴内容」来源 → 不回填粘贴框', async () => {
    const wrapper = mountInput()
    await openPastePanel(wrapper)

    await wrapper.setProps({
      modelValue: { fileName: '上传的.docx', content: '上传的正文' },
    })
    await flushPromises()

    expect(pasteTextarea().value).toBe('')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 只读态
// ═══════════════════════════════════════════════════════════════════════════

describe('只读态（disabled）', () => {
  test('⚠️ disabled=true → 「上传示例」禁用', () => {
    const wrapper = mountInput({ disabled: true })

    expect(uploadButton(wrapper)!.attributes('disabled')).toBeDefined()
  })

  test('⚠️ disabled=true → 文件输入框禁用', () => {
    const wrapper = mountInput({ disabled: true })

    expect(fileInput(wrapper).attributes('disabled')).toBeDefined()
  })

  test('⚠️ disabled=true → 「粘贴示例」禁用', () => {
    const wrapper = mountInput({ disabled: true })

    const pasteBtn = wrapper.findAll('button').find((b) => b.text() === '粘贴示例')
    expect(pasteBtn!.attributes('disabled')).toBeDefined()
  })

  test('⚠️ disabled=true → 「移除示例」禁用', () => {
    const wrapper = mountInput({
      disabled: true,
      modelValue: { fileName: '示例.docx', content: '内容' },
    })

    const removeBtn = wrapper.findAll('button').find((b) => b.attributes('aria-label') === '移除示例')
    expect(removeBtn!.attributes('disabled')).toBeDefined()
  })

  test('⚠️ disabled=true → 点「上传示例」**不打开**文件选择器', async () => {
    const wrapper = mountInput({ disabled: true })
    const clickSpy = vi.spyOn(fileInput(wrapper).element as HTMLInputElement, 'click')

    await uploadButton(wrapper)!.trigger('click')

    expect(clickSpy).not.toHaveBeenCalled()
  })

  test('⚠️ disabled=false → 点「上传示例」打开文件选择器', async () => {
    const wrapper = mountInput({ disabled: false })
    const clickSpy = vi.spyOn(fileInput(wrapper).element as HTMLInputElement, 'click')

    await uploadButton(wrapper)!.trigger('click')

    expect(clickSpy).toHaveBeenCalled()
  })
})
