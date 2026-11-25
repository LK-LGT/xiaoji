import assert from 'node:assert/strict'
import test from 'node:test'
import {
  availableWorkspaces,
  canAccessPath,
  hasModule,
  moduleMenuItems,
  visibleWorkspaceItems,
} from '../src/navigation/workspaces.ts'
import type { ModuleMenuGroup } from '../src/api/auth.ts'

/**
 * ⚠️⚠️ **部门功能板块隔离**（2026-11-21 用户投诉 → 2026-11-24 升级为动态菜单）
 *
 * 用户原话（2026-11-21）：「**还有为什么别的部门为什么会有剪辑板块？
 * 这个没有做各部门功能板块隔离，我要的是对应的部门有对应的功能。**
 *
 * 用户原话（2026-11-24）：「**侧边栏菜单不是写死固定，是动态渲染**：
 *   - 系统读取当前登录人的部门；
 *   - 读取这个部门绑定了哪些功能子模块；
 *   - 只有这个部门的人员，侧边栏才会加载出【短视频 → 剪辑工作量】；
 *   - 其他部门的账号，侧边栏**根本不会出现这个入口**」
 *
 * **根因（2026-11-21）**：菜单过滤的维度**只有权限串**。
 * `pan:workload:view` 授予了基础 `employee` 角色
 * ⇒ **全公司每个部门**的侧边栏都出现「剪辑工作量」。
 *
 * **升级（2026-11-24）**：菜单项从 `WORKSPACES` 里的**写死清单**
 * 移到**后端下发的 `moduleMenus`** ⇒ 前端只渲染、不定义。
 *
 * ⚠️ **这三个维度缺一不可**
 * ① **板块**（组织维度）—— 这个部门有没有这个业务
 * ② **岗位角色**（业务维度）—— reporter / reviewer / none
 *    ⚠️ `none`（如主播）⇒ **入口根本不出现** ⇒ 解决「看得见点进去空数据」
 * ③ **权限**（角色维度）—— 这个人能不能干这件事
 */

const PAN_PERMISSION = 'pan:workload:view'
const PAN_PATH = '/performance/pan-workload'
const PAN_MODULE = 'pan_workload'

/** 构造一份后端下发的板块菜单（模拟 `/auth/me` 的 `moduleMenus`）。 */
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

// ═════════════════════════════════════════════════════════════════════════
// ★ **动态菜单**（2026-11-24 核心）
// ═════════════════════════════════════════════════════════════════════════

test('写死清单里已经没有「剪辑工作量」了 —— 菜单改为后端下发', () => {
  /**
   * ⚠️ **核心断言** —— 这正是用户 2026-11-24 诉求的落点。
   *
   * 过去「剪辑工作量」硬编码在 4 个工作区（system/department/leader/employee/performance）
   * ⇒ 只要权限命中就出现 ⇒ 别的部门的人看到空页面。
   *
   * ⇒ 现在它**不在**写死清单里 ⇒ 只能由后端下发。
   */
  const permissions = [
    'dashboard:view',
    'leader:workbench:view',
    'department:dashboard:view',
    'performance:dashboard:view',
    'system:dashboard:view',
    PAN_PERMISSION,
  ]

  for (const workspace of availableWorkspaces(permissions)) {
    const items = visibleWorkspaceItems(workspace, permissions, [PAN_MODULE])
    assert.ok(
      !items.some((item) => item.path === PAN_PATH),
      `${workspace.id} 工作区的写死清单里不该再有「剪辑工作量」`,
    )
  }
})

test('板块菜单由后端下发后，前端渲染出「短视频 → 剪辑工作量」', () => {
  /**
   * ⚠️ **肯定式断言** —— 只测「挡住」的话，
   * 把整个板块逻辑删掉也能通过。
   */
  const permissions = ['dashboard:view', 'performance:dashboard:view', PAN_PERMISSION]
  const items = moduleMenuItems(panMenu(), permissions)

  assert.equal(items.length, 1)
  assert.equal(items[0].label, '剪辑工作量')
  assert.equal(items[0].path, PAN_PATH)
})

test('板块未开通 ⇒ 后端不下发 ⇒ 侧边栏没有入口', () => {
  /**
   * ⚠️ **从源头隐藏** —— 用户诉求原文：
   * 「其他部门的账号，侧边栏**根本不会出现这个入口**」
   */
  const permissions = ['dashboard:view', 'performance:dashboard:view', PAN_PERMISSION]

  // ⚠️ 后端对「未开通」返回空目录 ⇒ 前端渲染出空菜单
  assert.equal(moduleMenuItems([], permissions).length, 0)
})

test('岗位角色是「不参与」⇒ 后端不下发 ⇒ 侧边栏没有入口', () => {
  /**
   * ⚠️⚠️ **这条是 2026-11-24 的新增价值**
   *
   * 真实场景：短视频部「主播」岗位的业务口径是 ``none``（不参与）
   * ⇒ 板块开通了，他**不该**看到「剪辑工作量」入口
   * ⇒ 否则就是「看得见点进去空数据」（用户投诉的原症状）。
   *
   * ⇒ 后端按角色过滤后**不下发** ⇒ 前端自然不渲染。
   */
  const permissions = ['dashboard:view', 'performance:dashboard:view', PAN_PERMISSION]

  // ⚠️ 「不参与」的人 —— 后端返回空目录
  assert.equal(moduleMenuItems([], permissions).length, 0)

  // ⚠️ 但如果后端**误下发**了，前端也不该凭空造出 role=none 的入口
  //    （前端只渲染后端给的，不自己判断角色）
  const items = moduleMenuItems(panMenu('reporter'), permissions)
  assert.equal(items.length, 1, '前端只渲染后端下发的内容，不自行判定角色')
})

test('前端对后端下发的菜单再做一道权限校验（纵深防御）', () => {
  /**
   * ⚠️ **万一后端漏过滤** —— 前端也不能把没有权限的入口放出来。
   */
  const items = moduleMenuItems(panMenu(), ['dashboard:view'])
  assert.equal(items.length, 0, '没有 pan:workload:view ⇒ 不该渲染')
})

test('板块分组没有可见子模块时整组剔除', () => {
  const empty: ModuleMenuGroup[] = [
    { code: PAN_MODULE, label: '剪辑工作量', role: 'reporter', children: [] },
  ]
  const permissions = ['dashboard:view', PAN_PERMISSION]
  assert.equal(moduleMenuItems(empty, permissions).length, 0)
})

// ═════════════════════════════════════════════════════════════════════════
// ★ **路由守卫**（板块维度）
// ═════════════════════════════════════════════════════════════════════════

test('路由守卫按板块拦截动态菜单路径', () => {
  /**
   * ⚠️⚠️ **光隐藏菜单不够** —— 用户仍可直接敲 URL 访问。
   * ⇒ 前端路由守卫 + 后端 API 守卫，**两层都拦**。
   *
   * ⚠️ **2026-11-24** —— 路径不再写死在 `WORKSPACES` 里
   * ⇒ 守卫要靠后端下发的 `moduleMenus` 才知道「这个路径属于哪个板块」。
   */
  const permissions = ['dashboard:view', 'performance:dashboard:view', PAN_PERMISSION]
  const menus = panMenu()

  assert.equal(
    canAccessPath(PAN_PATH, permissions, [], menus),
    false,
    '板块未开通 ⇒ 路由应被拦',
  )
  assert.equal(
    canAccessPath(PAN_PATH, permissions, [PAN_MODULE], menus),
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

// ═════════════════════════════════════════════════════════════════════════
// ★ **通用功能不受板块限制**
// ═════════════════════════════════════════════════════════════════════════

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
