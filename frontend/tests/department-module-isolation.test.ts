import assert from 'node:assert/strict'
import test from 'node:test'
import {
  availableWorkspaces,
  canAccessPath,
  hasModule,
  visibleWorkspaceItems,
} from '../src/navigation/workspaces.ts'

/**
 * ⚠️⚠️ **部门功能板块隔离**（2026-11-21 用户投诉）
 *
 * 用户原话：「**还有为什么别的部门为什么会有剪辑板块？**
 * 这个没有做各部门功能板块隔离，我要的是对应的部门有对应的功能。」
 *
 * **根因**：菜单过滤的维度**只有权限串**。
 * `pan:workload:view` 授予了基础 `employee` 角色
 * ⇒ **全公司每个部门**的侧边栏都出现「剪辑工作量」。
 *
 * ⇒ 修复 = **部门维度**（`department_module`）独立于**角色维度**（权限）。
 * 前端拿到的 `modules` 来自后端 `/auth/me` 的 `modules` 字段。
 */

const PAN_PERMISSION = 'pan:workload:view'
const PAN_PATH = '/performance/pan-workload'
const PAN_MODULE = 'pan_workload'

test('板块未开通时，剪辑工作量菜单项在所有工作区都不可见', () => {
  /**
   * ⚠️ **核心断言** —— 这就是用户投诉的那件事。
   *
   * 权限**完全给足**（模拟「基础 employee 角色拿到了 pan:workload:view」）
   * 但部门**没开通**板块 ⇒ 菜单**不能**出现。
   */
  const permissions = [
    'dashboard:view',
    'leader:workbench:view',
    'department:dashboard:view',
    'performance:dashboard:view',
    PAN_PERMISSION,
  ]

  for (const workspace of availableWorkspaces(permissions)) {
    const items = visibleWorkspaceItems(workspace, permissions, [])
    assert.ok(
      !items.some((item) => item.path === PAN_PATH),
      `${workspace.id} 工作区不该显示「剪辑工作量」（部门未开通板块）`,
    )
  }
})

test('板块开通后，剪辑工作量菜单项在有权限的工作区可见', () => {
  /**
   * ⚠️ **肯定式断言** —— 只测「挡住」的话，
   * 把整个板块逻辑删掉也能通过。
   */
  const permissions = ['dashboard:view', 'performance:dashboard:view', PAN_PERMISSION]

  const employee = availableWorkspaces(permissions).find((w) => w.id === 'employee')
  assert.ok(employee)
  assert.ok(
    visibleWorkspaceItems(employee, permissions, [PAN_MODULE])
      .some((item) => item.path === PAN_PATH),
    '短视频部的员工应能看到「剪辑工作量」',
  )

  const performance = availableWorkspaces(permissions).find((w) => w.id === 'performance')
  assert.ok(performance)
  assert.ok(
    visibleWorkspaceItems(performance, permissions, [PAN_MODULE])
      .some((item) => item.path === PAN_PATH),
    '绩效管理工作区应能看到「剪辑工作量」',
  )
})

test('权限与板块两个维度必须同时满足', () => {
  const employee = availableWorkspaces(['dashboard:view']).find((w) => w.id === 'employee')
  assert.ok(employee)

  // ① 有板块、无权限 ⇒ 不显示
  assert.ok(
    !visibleWorkspaceItems(employee, ['dashboard:view'], [PAN_MODULE])
      .some((item) => item.path === PAN_PATH),
    '没有权限就不该显示（哪怕板块开通了）',
  )

  // ② 有权限、无板块 ⇒ 不显示
  assert.ok(
    !visibleWorkspaceItems(employee, ['dashboard:view', PAN_PERMISSION], [])
      .some((item) => item.path === PAN_PATH),
    '板块没开通就不该显示（哪怕权限给足了）',
  )

  // ③ 两个都有 ⇒ 显示
  assert.ok(
    visibleWorkspaceItems(employee, ['dashboard:view', PAN_PERMISSION], [PAN_MODULE])
      .some((item) => item.path === PAN_PATH),
    '权限与板块都满足才显示',
  )
})

test('未声明模块的菜单项不受板块限制', () => {
  /**
   * ⚠️ **通用功能对所有部门开放** —— 不能因为板块列表为空
   * 就把「月计划」「绩效问答」这些全藏了。
   */
  const employee = availableWorkspaces(['dashboard:view']).find((w) => w.id === 'employee')
  assert.ok(employee)

  const items = visibleWorkspaceItems(employee, ['dashboard:view'], [])
  assert.ok(items.some((item) => item.path === '/employee/dashboard'))
  assert.ok(items.some((item) => item.path === '/employee/week-plans'))
})

test('hasModule 的判定语义', () => {
  assert.equal(hasModule([], undefined), true, '未声明模块 ⇒ 不限制')
  assert.equal(hasModule([], '*'), true)
  assert.equal(hasModule([], PAN_MODULE), false)
  assert.equal(hasModule([PAN_MODULE], PAN_MODULE), true)
})

test('路由守卫也按板块拦截', () => {
  /**
   * ⚠️⚠️ **光隐藏菜单不够** —— 用户仍可直接敲 URL 访问。
   * ⇒ 前端路由守卫 + 后端 API 守卫，**两层都拦**。
   */
  const permissions = ['dashboard:view', 'performance:dashboard:view', PAN_PERMISSION]

  assert.equal(
    canAccessPath(PAN_PATH, permissions, []),
    false,
    '板块未开通 ⇒ 路由应被拦',
  )
  assert.equal(
    canAccessPath(PAN_PATH, permissions, [PAN_MODULE]),
    true,
    '板块已开通 ⇒ 路由应放行',
  )
})

test('路由守卫对没有菜单项的路径放行', () => {
  /**
   * ⚠️ **不能因为「菜单里没有」就把合法用户挡在外面**
   * —— 有些路由本来就没有对应菜单项（详情页、兼容别名）。
   */
  assert.equal(
    canAccessPath('/employee/month-plans/123', ['dashboard:view'], []),
    true,
    '详情页没有菜单项 ⇒ 应放行',
  )
  assert.equal(
    canAccessPath('/no-such-path', ['dashboard:view'], []),
    true,
    '不属于任何工作区的路径 ⇒ 应放行',
  )
})
