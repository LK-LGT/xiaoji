import assert from 'node:assert/strict'
import test from 'node:test'
import {
  availableWorkspaces,
  isModuleSubmodulePath,
  resolveActiveWorkspace,
  workspaceForPath,
} from '../src/navigation/workspaces.ts'
import {
  findModuleSubmodule,
  resolveBreadcrumbNavigation,
} from '../src/navigation/breadcrumbs.ts'
import type { ModuleMenuGroup } from '../src/api/auth.ts'

/**
 * 🔴 **「点板块内容就跳到另一个层级」回归守卫**（2026-11-24）
 *
 * 用户投诉原话
 * ------------
 * 「管理员，为什么**每次点这个板块的内容都会跳到另一个层级**，
 *   这个是什么问题，请修复」
 *
 * ⚠️ **复现路径**
 * 1. 管理员登录 ⇒ 默认落在「系统管理端」（`/system/dashboard`）
 * 2. 侧边栏底部出现动态板块分组【剪辑工作量 → 剪辑工作量】
 * 3. 点这个入口 ⇒ 路由跳到 `/performance/pan-workload`
 * 4. **侧边栏整体变成「绩效管理」**（标题、菜单全换）
 * 5. 面包屑变成兜底的「成果计划」
 *
 * ⇒ 用户看到的现象就是「**跳到另一个层级**」。
 *
 * ⚠️ **根因**
 * 板块子模块的路径带 `/performance` 前缀 ⇒ 纯前缀匹配
 * （`workspaceForPath`）把它判成「绩效管理」工作区。
 */

const PAN_PATH = '/performance/pan-workload'
const PAN_PERMISSION = 'pan:workload:view'
const PAN_MODULE = 'pan_workload'

/** 构造后端下发的板块菜单（模拟 `/auth/me` 的 `moduleMenus`）。 */
function panMenu(role: 'reporter' | 'reviewer' | 'none' = 'reporter'): ModuleMenuGroup[] {
  return [
    {
      code: PAN_MODULE,
      label: '剪辑工作量',
      role,
      children: [
        {
          code: PAN_MODULE,
          label: '剪辑工作量',
          path: PAN_PATH,
          icon: '▦',
          permission: PAN_PERMISSION,
          moduleCode: PAN_MODULE,
        },
      ],
    },
  ]
}

/** 管理员/HR 的权限（同时能进系统管理端与绩效管理端）。 */
const ADMIN_PERMISSIONS = [
  'system:dashboard:view',
  'department:dashboard:view',
  'leader:workbench:view',
  'dashboard:view',
  'performance:dashboard:view',
  'dispute:dashboard:view',
  PAN_PERMISSION,
]

// ═════════════════════════════════════════════════════════════════════════
// ★ **工作区归属**（核心断言）
// ═════════════════════════════════════════════════════════════════════════

test('板块子模块路径不再被判成「绩效管理」工作区', () => {
  /**
   * ⚠️ **正向锁定根因**
   * 纯前缀匹配必然命中 `/performance` ⇒ 必然跳到「绩效管理」。
   * 这个断言的作用是：**提醒后来人别把前缀匹配直接用在板块路径上**。
   */
  assert.equal(
    workspaceForPath(PAN_PATH)?.id,
    'performance',
    '⚠️ 前缀匹配本身仍然会判成 performance —— 修复必须靠 resolveActiveWorkspace',
  )

  // ✅ 真正的修复入口：命中板块子模块时**保持当前工作区**
  const workspaces = availableWorkspaces(ADMIN_PERMISSIONS)
  const active = resolveActiveWorkspace(PAN_PATH, panMenu(), workspaces, 'system')
  assert.equal(active?.id, 'system', '管理员从「系统管理端」点板块 ⇒ 应仍停在系统管理端')
})

test('从各工作区点板块子模块都不会跳走', () => {
  /**
   * ⚠️ **逐个工作区断言**
   * 不能只测 system —— 用户可能从部门端、领导端、员工端点进去。
   */
  const workspaces = availableWorkspaces(ADMIN_PERMISSIONS)
  for (const from of ['system', 'department', 'leader', 'employee'] as const) {
    const active = resolveActiveWorkspace(PAN_PATH, panMenu(), workspaces, from)
    assert.equal(active?.id, from, `从「${from}」点板块子模块 ⇒ 工作区不应改变`)
  }
})

test('板块子模块的详情页也保持当前工作区', () => {
  const workspaces = availableWorkspaces(ADMIN_PERMISSIONS)
  const active = resolveActiveWorkspace(
    `${PAN_PATH}/detail/123`,
    panMenu(),
    workspaces,
    'department',
  )
  assert.equal(active?.id, 'department', '板块子模块的详情页也不该跳走')
})

test('普通工作区路径仍然按前缀归属（原有行为不能被破坏）', () => {
  /**
   * ⚠️ **防回归**
   * 修「板块跳走」不能把「普通路径归属」也一起改坏。
   */
  const workspaces = availableWorkspaces(ADMIN_PERMISSIONS)
  assert.equal(resolveActiveWorkspace('/system/dashboard', [], workspaces)?.id, 'system')
  assert.equal(resolveActiveWorkspace('/department/dashboard', [], workspaces)?.id, 'department')
  assert.equal(resolveActiveWorkspace('/leader/workbench', [], workspaces)?.id, 'leader')
  assert.equal(resolveActiveWorkspace('/employee/dashboard', [], workspaces)?.id, 'employee')
  assert.equal(resolveActiveWorkspace('/performance/dashboard', [], workspaces)?.id, 'performance')
})

test('板块子模块的识别：路径完全相等或以其为前缀', () => {
  assert.equal(isModuleSubmodulePath(PAN_PATH, panMenu()), true)
  assert.equal(isModuleSubmodulePath(`${PAN_PATH}/detail/1`, panMenu()), true)
  assert.equal(isModuleSubmodulePath('/performance/dashboard', panMenu()), false)
  assert.equal(isModuleSubmodulePath('/system/dashboard', panMenu()), false)
  assert.equal(isModuleSubmodulePath(PAN_PATH, []), false, '没有下发板块菜单 ⇒ 不算板块路径')
})

test('直接敲 URL 进板块页时，回落到默认落地工作区而不是「绩效管理」', () => {
  /**
   * ⚠️ **用户诉求原文**：「HR 在自己工作区就能直接看到，不用切」
   *
   * 没有「来源工作区」记忆时（刷新页面 / 直接敲 URL）
   * ⇒ 落在**默认落地工作区**，而不是按前缀跳到「绩效管理」。
   */
  const workspaces = availableWorkspaces(ADMIN_PERMISSIONS)
  const active = resolveActiveWorkspace(PAN_PATH, panMenu(), workspaces, undefined)
  assert.equal(active?.id, 'system', '无记忆 ⇒ 落到默认工作区（管理员默认是系统管理端）')
})

// ═════════════════════════════════════════════════════════════════════════
// ★ **面包屑**
// ═════════════════════════════════════════════════════════════════════════

test('板块子模块的面包屑体现「工作区 → 板块 → 子模块」的层级', () => {
  /**
   * ⚠️ **修复前**：面包屑是兜底的「成果计划」⇒ 完全看不出层级。
   */
  const nav = resolveBreadcrumbNavigation(
    { path: PAN_PATH },
    '/system/dashboard',
    panMenu(),
  )
  assert.equal(nav.title, '剪辑工作量')
  assert.deepEqual(
    nav.breadcrumbs.map((item) => item.title),
    ['系统管理端', '剪辑工作量'],
    '板块名与子模块名同名 ⇒ 不重复显示',
  )
  // ⚠️ 只有中间级是「不可点的」（板块名不是页面）
  assert.equal(nav.breadcrumbs[0].path, '/system/dashboard')
  assert.equal(nav.breadcrumbs[1].path, undefined)
})

test('板块名与子模块名不同时，面包屑显示两级', () => {
  const menus: ModuleMenuGroup[] = [
    {
      code: 'short_video',
      label: '短视频',
      role: 'reporter',
      children: [
        {
          code: 'pan_workload',
          label: '剪辑工作量',
          path: PAN_PATH,
          icon: '▦',
          permission: PAN_PERMISSION,
          moduleCode: 'short_video',
        },
      ],
    },
  ]
  const nav = resolveBreadcrumbNavigation({ path: PAN_PATH }, '/department/dashboard', menus)
  assert.deepEqual(
    nav.breadcrumbs.map((item) => item.title),
    ['部门负责人端', '短视频', '剪辑工作量'],
  )
})

test('板块子模块的「返回上一级」回到当前工作区首页', () => {
  const nav = resolveBreadcrumbNavigation({ path: PAN_PATH }, '/system/dashboard', panMenu())
  assert.equal(nav.parentPath, '/system/dashboard', '返回上一级 ⇒ 回到系统管理端，而不是绩效管理')
})

test('非板块路径的面包屑不受影响', () => {
  /**
   * ⚠️ **防回归** —— 板块逻辑只对**命中板块子模块**的路径生效，
   * 其它路径的面包屑必须跟以前一模一样。
   *
   * ⚠️ 注意：普通路径的面包屑第一级是**工作区首页页名**（「管理工作台」），
   *    而板块子模块的第一级是**工作区名**（「系统管理端」）——
   *    因为板块页要让人一眼看出「我还在原来那个端里」，这正是本次修复的目的。
   */
  const nav = resolveBreadcrumbNavigation({ path: '/system/employees' }, '/system/dashboard', panMenu())
  assert.equal(nav.title, '员工管理')
  assert.deepEqual(
    nav.breadcrumbs.map((item) => item.title),
    ['管理工作台', '员工管理'],
  )
})

test('findModuleSubmodule 能定位板块与子模块名', () => {
  assert.deepEqual(
    findModuleSubmodule(PAN_PATH, panMenu()),
    { groupLabel: '剪辑工作量', itemLabel: '剪辑工作量' },
  )
  assert.equal(findModuleSubmodule('/system/dashboard', panMenu()), undefined)
})
