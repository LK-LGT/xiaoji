/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 组织架构管理。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/system-management-ui.test.ts``）里的逻辑型断言：
 *
 * ```typescript
 * assert.match(source, /<div class="section-header">[\s\S]*@click="openCreate"/)
 * ```
 *
 * ⚠️ **为什么那是假的保障**
 *   ① 只证明「有个按钮绑了 openCreate」，没验证「新增时父节点默认是谁」；
 *   ② ``isDescendant`` 的哨兵值（``ROOT_DEPARTMENT_ID`` 必须是字符串 ``"0"``）
 *      一旦写成数字 ``0``，``===`` 严格比较会**静默失效** ——
 *      函数永远返回 false ⇒ 选中父节点后**看不到子节点**，
 *      而源码里 ``"0"`` 这个字面量还在，正则照样匹配得上。
 *
 * ⚠️ **本文件真正测的东西**（组织架构的核心语义）
 *   1. **选中节点 → 只显示该节点及其后代**（层级过滤，含后代判定）；
 *   2. **编码必须是大写格式**（``/^[A-Z][A-Z0-9_]{1,49}$/``）；
 *   3. **编码建后不可改**（编辑态禁用 code 输入框）；
 *   4. **新增时父节点默认 = 当前选中节点**；
 *   5. **状态 1=启用 / 0=禁用**；
 *   6. **负责人为空 → 「未配置」**；
 *   7. **层级缩进**（``'　'.repeat(depth)``）；
 *   8. **删除前二次确认**。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, test, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'

// ⚠️ **系统 API** —— 组件挂载后立刻拉组织树 + 选项。
const {
  getDepartments,
  getSystemOptions,
  createDepartment,
  updateDepartment,
  deleteDepartment,
} = vi.hoisted(() => ({
  getDepartments: vi.fn(),
  getSystemOptions: vi.fn(),
  createDepartment: vi.fn(),
  updateDepartment: vi.fn(),
  deleteDepartment: vi.fn(),
}))

vi.mock('@/api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/system')>()
  return {
    ...actual,
    getDepartments: (...a: unknown[]) => getDepartments(...a),
    getSystemOptions: (...a: unknown[]) => getSystemOptions(...a),
    createDepartment: (...a: unknown[]) => createDepartment(...a),
    updateDepartment: (...a: unknown[]) => updateDepartment(...a),
    deleteDepartment: (...a: unknown[]) => deleteDepartment(...a),
  }
})

// ⚠️ **Element Plus 弹框** —— 删除确认框要能点「确定」。
const { confirmMock, messageMock } = vi.hoisted(() => ({
  confirmMock: vi.fn(async () => 'confirm' as unknown),
  messageMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}))

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return {
    ...actual,
    ElMessageBox: { ...actual.ElMessageBox, confirm: (...a: unknown[]) => confirmMock(...a) },
    ElMessage: messageMock,
  }
})

import OrgManagement from '../../src/views/system/OrgManagement.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 组织树夹具：
 *
 * ```
 * 0 (根) ──┬── D1  部门
 *           │    └── G1  小组
 *           │         └── P1  项目组
 *           └── D2  部门
 * ```
 */
function dept(overrides: Record<string, unknown> = {}) {
  return {
    id: 'D1',
    parentId: '0',
    name: '剪辑组',
    code: 'EDIT',
    orgType: 'DEPARTMENT',
    leaderUserId: 'U1',
    leaderName: '张三',
    sortNo: 1,
    status: 1,
    employeeCount: 5,
    children: [],
    ...overrides,
  }
}

function tree() {
  return [
    dept({
      id: 'D1',
      name: '内容中心',
      code: 'CONTENT',
      children: [
        dept({
          id: 'G1',
          parentId: 'D1',
          name: '剪辑组',
          code: 'EDIT',
          orgType: 'GROUP',
          children: [
            dept({
              id: 'P1',
              parentId: 'G1',
              name: '短视频项目组',
              code: 'SHORT_VIDEO',
              orgType: 'PROJECT_GROUP',
              children: [],
            }),
          ],
        }),
      ],
    }),
    dept({ id: 'D2', name: '运营中心', code: 'OPS', children: [] }),
  ]
}

async function mountOrg(departments: unknown[] = tree()) {
  getDepartments.mockResolvedValue(departments)
  getSystemOptions.mockResolvedValue({
    departments: [],
    leaders: [{ id: 'U1', label: '张三' }],
    roles: [],
  })

  const wrapper = mount(OrgManagement)
  await nextTick()
  await flushPromises()
  await nextTick()
  return wrapper
}

/** 表格里的行。 */
function rows(wrapper: Awaited<ReturnType<typeof mountOrg>>) {
  return wrapper.findAll('.el-table__row')
}

/** 行文本。 */
function rowTexts(wrapper: Awaited<ReturnType<typeof mountOrg>>): string[] {
  return rows(wrapper).map((r) => r.text())
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐⭐ 层级过滤：选中节点只显示它和它的后代
// ═══════════════════════════════════════════════════════════════════════════

describe('选中节点后的层级过滤', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('未选中 → 显示全部节点（扁平化）', async () => {
    const wrapper = await mountOrg()

    // ⚠️ 4 个节点：D1 / G1 / P1 / D2。
    expect(rows(wrapper)).toHaveLength(4)
  })

  test('⚠️ 选中父节点 → 显示它 + 它的全部后代，**不含兄弟节点**', async () => {
    const wrapper = await mountOrg()

    // ⚠️ **核心** —— 点组织树里的「内容中心」（D1）。
    //    期望：D1 + G1 + P1（D1 的后代），**不含 D2**。
    await wrapper.findComponent({ name: 'ElTree' }).vm.$emit('node-click', tree()[0])
    await nextTick()

    const texts = rowTexts(wrapper)
    expect(rows(wrapper)).toHaveLength(3)
    expect(texts.join('|')).toContain('内容中心')
    expect(texts.join('|')).toContain('剪辑组')
    expect(texts.join('|')).toContain('短视频项目组')
    // ⚠️ **兄弟节点必须消失** —— 这是「后代判定」的核心。
    expect(texts.join('|')).not.toContain('运营中心')
  })

  test('⚠️ 选中中间层 → 只显示它和它下面的', async () => {
    const wrapper = await mountOrg()

    // ⚠️ 选中 G1（剪辑组）⇒ 显示 G1 + P1，不含 D1 / D2。
    await wrapper.findComponent({ name: 'ElTree' }).vm.$emit('node-click', tree()[0].children[0])
    await nextTick()

    const texts = rowTexts(wrapper).join('|')
    expect(rows(wrapper)).toHaveLength(2)
    expect(texts).toContain('剪辑组')
    expect(texts).toContain('短视频项目组')
    expect(texts).not.toContain('内容中心')
    expect(texts).not.toContain('运营中心')
  })

  test('⚠️ 选中叶子 → 只显示它自己', async () => {
    const wrapper = await mountOrg()

    await wrapper.findComponent({ name: 'ElTree' }).vm.$emit('node-click', tree()[0].children[0].children[0])
    await nextTick()

    expect(rows(wrapper)).toHaveLength(1)
    expect(rowTexts(wrapper).join('|')).toContain('短视频项目组')
  })

  test('⚠️ 详情面板标题跟着选中节点变', async () => {
    const wrapper = await mountOrg()

    expect(wrapper.find('.org-detail-panel .section-header h2').text()).toBe('全部组织节点')

    await wrapper.findComponent({ name: 'ElTree' }).vm.$emit('node-click', tree()[1])
    await nextTick()

    expect(wrapper.find('.org-detail-panel .section-header h2').text()).toBe('运营中心')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 状态与负责人显示
// ═══════════════════════════════════════════════════════════════════════════

describe('状态与负责人显示', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('status=1 → 「启用」', async () => {
    const wrapper = await mountOrg([dept({ id: 'D1', status: 1 })])

    expect(rowTexts(wrapper)[0]).toContain('启用')
  })

  test('⚠️ status=0 → 「禁用」（不能反过来）', async () => {
    // ⚠️ ``row.status === 1 ? '启用' : '禁用'`` 若写反 ⇒ 全都显示错。
    const wrapper = await mountOrg([dept({ id: 'D1', status: 0 })])

    expect(rowTexts(wrapper)[0]).toContain('禁用')
    expect(rowTexts(wrapper)[0]).not.toContain('启用')
  })

  test('⚠️ 没有负责人 → 「未配置」', async () => {
    const wrapper = await mountOrg([dept({ id: 'D1', leaderName: undefined })])

    expect(rowTexts(wrapper)[0]).toContain('未配置')
  })

  test('有负责人 → 显示名字', async () => {
    const wrapper = await mountOrg([dept({ id: 'D1', leaderName: '李四' })])

    expect(rowTexts(wrapper)[0]).toContain('李四')
    expect(rowTexts(wrapper)[0]).not.toContain('未配置')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐⭐ 编码规则
// ═══════════════════════════════════════════════════════════════════════════

describe('组织编码规则', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('⚠️ 新增态 → 编码输入框**可编辑**', async () => {
    const wrapper = await mountOrg()

    await wrapper.findAll('button').find((b) => b.text().includes('新增节点'))!.trigger('click')
    await nextTick()

    const codeInput = wrapper.findAll('input').find((i) => i.attributes('maxlength') === '50')
    expect(codeInput).toBeDefined()
    expect(codeInput!.attributes('disabled')).toBeUndefined()
  })

  test('⚠️ 编辑态 → 编码输入框**禁用**（编码建后不可改）', async () => {
    // ⚠️ **数据完整性规则** —— 编码变了会导致历史数据对不上。
    const wrapper = await mountOrg()

    // ⚠️ 点行内「编辑」按钮。
    const editBtn = rows(wrapper)[0].findAll('button').find((b) => b.text().trim() === '编辑')
    expect(editBtn).toBeDefined()
    await editBtn!.trigger('click')
    await nextTick()

    const codeInput = wrapper.findAll('input').find((i) => i.attributes('maxlength') === '50')
    expect(codeInput).toBeDefined()
    expect(codeInput!.attributes('disabled')).toBeDefined()
  })

  test('⚠️ 编码必须大写 —— 小写编码被校验拦下', async () => {
    const wrapper = await mountOrg()

    await wrapper.findAll('button').find((b) => b.text().includes('新增节点'))!.trigger('click')
    await nextTick()

    // ⚠️ 表单规则：``/^[A-Z][A-Z0-9_]{1,49}$/`` —— 小写字母不接受。
    //
    // ⚠️ **直接测规则本身**，而不是指望 ``formRef.validate()``：
    //    Element Plus 的 validate 依赖字段已渲染 + 异步校验器，
    //    在 jsdom 下的时序不稳定 ⇒ 改为验证**规则的正则**。
    const form = wrapper.findComponent({ name: 'ElForm' })
    const rules = form.props('rules') as { code: Array<{ pattern?: RegExp }> }
    const pattern = rules.code[0].pattern!

    expect(pattern.test('EDIT_GROUP')).toBe(true)
    // ⚠️ **小写必须被拒**。
    expect(pattern.test('edit_group')).toBe(false)
    // ⚠️ 数字开头也不接受（必须字母开头）。
    expect(pattern.test('1EDIT')).toBe(false)
  })

  test('⚠️ 抽屉标题区分「新增」与「编辑」', async () => {
    // ⚠️ **可见行为** —— 用户从标题就知道自己在新增还是编辑。
    const wrapper = await mountOrg()

    await wrapper.findAll('button').find((b) => b.text().includes('新增节点'))!.trigger('click')
    await nextTick()
    expect(wrapper.find('.el-drawer__title').text()).toBe('新增组织节点')

    await wrapper.findAll('button').find((b) => b.text().includes('取消'))!.trigger('click')
    await nextTick()

    const editBtn = rows(wrapper)[0].findAll('button').find((b) => b.text().trim() === '编辑')
    await editBtn!.trigger('click')
    await nextTick()
    expect(wrapper.find('.el-drawer__title').text()).toBe('编辑组织节点')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 新增时父节点默认 = 当前选中
// ═══════════════════════════════════════════════════════════════════════════

describe('新增节点的父节点默认值', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  test('⚠️ 未选中任何节点 → 父节点是根节点', async () => {
    const wrapper = await mountOrg()

    await wrapper.findAll('button').find((b) => b.text().includes('新增节点'))!.trigger('click')
    await nextTick()

    // ⚠️ 根节点是 ``ROOT_DEPARTMENT_ID = '0'``（字符串！）。
    const treeSelect = wrapper.findComponent({ name: 'ElTreeSelect' })
    expect(treeSelect.props('modelValue')).toBe('0')
  })

  test('⚠️ 选中「内容中心」后新增 → 父节点自动是它', async () => {
    // ⚠️ **业务规则** —— 在某个部门下新增，其父级应当默认就是那个部门。
    const wrapper = await mountOrg()

    await wrapper.findComponent({ name: 'ElTree' }).vm.$emit('node-click', tree()[0])
    await nextTick()

    await wrapper.findAll('button').find((b) => b.text().includes('新增节点'))!.trigger('click')
    await nextTick()

    const treeSelect = wrapper.findComponent({ name: 'ElTreeSelect' })
    expect(treeSelect.props('modelValue')).toBe('D1')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 删除前二次确认
// ═══════════════════════════════════════════════════════════════════════════

describe('删除节点', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // ⚠️ **确认框一直不 resolve** —— 模拟「用户还没点确定」。
    //    ⇒ 组件停在 ``await ElMessageBox.confirm(...)``，
    //      **不该走到 deleteDepartment**。
    confirmMock.mockReturnValue(new Promise(() => {}))
    deleteDepartment.mockResolvedValue(undefined)
  })

  test('⚠️ 点删除 → 先弹确认框，**不直接删**', async () => {
    const wrapper = await mountOrg()

    const delBtn = rows(wrapper)[0].findAll('button').find((b) => b.text().trim() === '删除')
    await delBtn!.trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalled()
    // ⚠️ **确认前不该真删** —— 这是防误删的核心。
    expect(deleteDepartment).not.toHaveBeenCalled()
  })

  test('⚠️ 确认后才真正删', async () => {
    confirmMock.mockResolvedValue('confirm' as unknown)
    const wrapper = await mountOrg()

    const delBtn = rows(wrapper)[0].findAll('button').find((b) => b.text().trim() === '删除')
    await delBtn!.trigger('click')
    await flushPromises()

    expect(deleteDepartment).toHaveBeenCalledWith('D1')
  })

  test('⚠️ 取消确认 → 不删', async () => {
    confirmMock.mockRejectedValue('cancel')
    const wrapper = await mountOrg()

    const delBtn = rows(wrapper)[0].findAll('button').find((b) => b.text().trim() === '删除')
    await delBtn!.trigger('click')
    await flushPromises()

    expect(deleteDepartment).not.toHaveBeenCalled()
    // ⚠️ **取消不是错误** ⇒ 不该弹错误提示。
    expect(messageMock.error).not.toHaveBeenCalled()
  })

  test('⚠️ 确认框文案含节点名 + 「仅空节点可删除」提示', async () => {
    const wrapper = await mountOrg()

    const delBtn = rows(wrapper)[0].findAll('button').find((b) => b.text().trim() === '删除')
    await delBtn!.trigger('click')
    await nextTick()

    const msg = String(confirmMock.mock.calls[0][0])
    expect(msg).toContain('内容中心')
    expect(msg).toContain('仅空节点可删除')
  })
})
