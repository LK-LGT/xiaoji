/**
 * 🔴 **组件测试** —— 「角色与权限」已下线说明页。
 *
 * ⚠️ **为什么这个页面值得挂载测试**
 * ------------------------------------------------------------------
 * 它是「假功能下线」的**唯一可见载体**：路由还保留（老链接不 404），
 * 但页面必须说清楚「这功能没了、为什么没、该去哪」。
 *
 * 如果这个页面渲染成空白、或说明文案缺失、或替代入口点不动，
 * 用户会以为「系统出 bug 了」⇒ 反而比直接 404 更糟。
 *
 * ⇒ 三件事必须挂载才能验：
 *   1. **说清楚了「已下线」**（标题 + 警告块）
 *   2. **给出了替代入口**（部门功能板块 / 部门项目组 / 员工管理）
 *   3. **替代入口点得动**（router.push 真被调用）
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import RetiredRoleManagement from '../../src/views/system/RetiredRoleManagement.vue'

const push = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}))

function mountRetired() {
  // ⚠️ **stub 必须正确还原「表格行 → 列插槽」的传参关系**，
  //   否则表体内容根本渲染不出来（``el-table-column`` 拿不到 ``row``）。
  //
  //   Element Plus 的真实行为是：
  //     <el-table>  拿到 data 数组 → 为每行创建作用域
  //     <el-table-column> 在**每行作用域内**渲染 default 插槽，slotProps = { row }
  //
  //   ⇒ 这里用 provide/inject 还原它。
  return mount(RetiredRoleManagement, {
    global: {
      stubs: {
        'el-table': {
          props: ['data'],
          template: '<div class="el-table"><slot /></div>',
          provide() {
            // ⚠️ 用 shallowRef 存行 —— 模板里按行循环渲染列插槽
            return { tableRows: this.data || [] }
          },
        },
        'el-table-column': {
          inject: ['tableRows'],
          props: ['prop', 'label'],
          template:
            '<div class="el-table-column">' +
            '<span class="col-label">{{ label }}</span>' +
            '<slot v-for="row in tableRows" :row="row" :key="row.path" />' +
            '<span class="col-cell" v-for="row in tableRows" :key="row.path + \'-\' + prop">' +
            '{{ row[prop] }}' +
            '</span>' +
            '</div>',
        },
        'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        'el-alert': { template: '<div><slot name="default" /></div>' },
      },
    },
  })
}

describe('已下线说明页的基本表达', () => {
  beforeEach(() => {
    push.mockClear()
  })

  test('标题说清楚是「角色与权限」且已下线', () => {
    const wrapper = mountRetired()
    const text = wrapper.text()

    expect(text).toContain('角色与权限')
    expect(text).toContain('已下线')
  })

  test('解释了为什么下线（硬编码假数据 + 存不进去）', () => {
    const text = mountRetired().text()

    expect(text).toContain('硬编码假数据')
    expect(text).toContain('存不进去')
  })

  test('说明了现在的权限是「按真实身份动态判定」', () => {
    const text = mountRetired().text()

    expect(text).toContain('真实身份')
    expect(text).toContain('动态判定')
  })

  test('⚠️ 不出现「超级管理员」这种误导词', () => {
    // 「超级管理员」会让人误以为存在能伪装身份的万能账号
    // —— 而真实的角色词表里根本没有这个角色。
    expect(mountRetired().text()).not.toContain('超级管理员')
  })
})

describe('给出了可用的替代入口', () => {
  beforeEach(() => {
    push.mockClear()
  })

  test('列出三个替代功能', () => {
    const text = mountRetired().text()

    expect(text).toContain('部门功能板块')
    expect(text).toContain('部门/项目组')
    expect(text).toContain('员工管理')
  })

  test('点「前往」会真的跳转', async () => {
    const wrapper = mountRetired()

    // 找到所有「前往」按钮
    const buttons = wrapper.findAll('button')
    expect(buttons.length).toBeGreaterThan(0)

    await buttons[0].trigger('click')
    expect(push).toHaveBeenCalled()
    expect(push.mock.calls[0][0]).toMatch(/^\/system\//)
  })
})
