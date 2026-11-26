import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// ⚠️ 本文件锁定「角色管理 / 权限管理」两个页面的**下线决定**（2026-11-26）。
//
// 背景：
//   这两个页面对应的后端接口返回的是**硬编码假数据**：
//     `GET /v1/system/roles`       → 写死 3 条 ADMIN / MANAGER / EMPLOYEE
//     `GET /v1/system/permissions` → 写死 1 个「系统管理」节点
//   而数据库里**根本没有 `role` 表**（实测 2026-11-26）
//   ⇒ 页面看起来能增删改，实际**存不进去**（POST/PUT 是 no-op，
//     只 return 一个临时 id）⇒ 属于**假功能**。
//
// ❌ **为什么不接真实数据**
//   现在的权限模型**不是** RBAC 角色表，而是「角色 × 部门 × 汇报线」的
//   **动态判定**（见 `backend/app/services/access_control.py`）——
//   角色由系统按员工的部门、岗位、汇报线**自动推导**，**不人工分配**。
//   ⇒ 做一个「角色表」等于倒退回被明确废弃的模型。
//
// ⚠️ **为什么保留路由**
//   ① 老链接（书签、飞书里发过的 URL）直接 404 体验差；
//   ② 前端构建、面包屑、测试都要连带改。
//   ⇒ 保留路由 + 指向「已下线说明页」，是成本最低、体验最好的做法。

const router = readFileSync(
  fileURLToPath(new URL('../src/router/index.ts', import.meta.url)),
  'utf8',
)
const workspaces = readFileSync(
  fileURLToPath(new URL('../src/navigation/workspaces.ts', import.meta.url)),
  'utf8',
)
const breadcrumbs = readFileSync(
  fileURLToPath(new URL('../src/navigation/breadcrumbs.ts', import.meta.url)),
  'utf8',
)
const systemApi = readFileSync(
  fileURLToPath(new URL('../src/api/system.ts', import.meta.url)),
  'utf8',
)
const systemDashboard = readFileSync(
  fileURLToPath(new URL('../src/views/system/SystemDashboard.vue', import.meta.url)),
  'utf8',
)

// ─────────────────────────────────────────────────────────────
// 1. 页面文件已删除
// ─────────────────────────────────────────────────────────────

test('「角色管理」「权限管理」两个假功能页面已删除', () => {
  // ⚠️ 用**源码文本**断言，而不是列目录 ——
  //   目录断言在文件被改名（而非删除）时会假绿。
  assert.doesNotMatch(
    router,
    /import RoleManagement from/,
    'router 不应再 import 已删除的 RoleManagement.vue',
  )
  assert.doesNotMatch(
    router,
    /import PermissionManagement from/,
    'router 不应再 import 已删除的 PermissionManagement.vue',
  )
  assert.match(
    router,
    /import RetiredRoleManagement from/,
    'router 应 import「已下线说明页」',
  )
})

// ─────────────────────────────────────────────────────────────
// 2. 路由保留但指向已下线页
// ─────────────────────────────────────────────────────────────

test('老路由仍可访问（不 404），但指向已下线说明页', () => {
  assert.match(
    router,
    /withAppShell\('\/system\/roles',\s*RetiredRoleManagement/,
    '/system/roles 路由应保留并指向 RetiredRoleManagement',
  )
  assert.match(
    router,
    /withAppShell\('\/system\/permissions',\s*RetiredRoleManagement/,
    '/system/permissions 路由应保留并指向 RetiredRoleManagement',
  )
})

// ─────────────────────────────────────────────────────────────
// 3. 菜单入口已移除
// ─────────────────────────────────────────────────────────────

test('侧边栏菜单不再展示「角色管理」「权限管理」', () => {
  assert.doesNotMatch(
    workspaces,
    /label:\s*'角色管理'/,
    '菜单里不该再有「角色管理」入口（页面是假功能）',
  )
  assert.doesNotMatch(
    workspaces,
    /label:\s*'权限管理'/,
    '菜单里不该再有「权限管理」入口（页面是假功能）',
  )
})

test('系统仪表盘的「角色与权限」卡片已换成真实功能', () => {
  assert.doesNotMatch(
    systemDashboard,
    /label:\s*'角色与权限'/,
    '仪表盘不该再有「角色与权限」卡片',
  )
  assert.match(
    systemDashboard,
    /label:\s*'部门功能板块'/,
    '仪表盘应指向真实的「部门功能板块」',
  )
})

// ─────────────────────────────────────────────────────────────
// 4. 面包屑说清楚「已下线」
// ─────────────────────────────────────────────────────────────

test('面包屑标注「已下线」，不让用户以为进了一个真能用的功能页', () => {
  assert.match(breadcrumbs, /'\/system\/roles':\s*'角色与权限（已下线）'/)
  assert.match(breadcrumbs, /'\/system\/permissions':\s*'角色与权限（已下线）'/)
})

// ─────────────────────────────────────────────────────────────
// 5. 前端 API 层不再暴露假接口
// ─────────────────────────────────────────────────────────────

test('api/system.ts 不再导出读写假角色/假权限的接口', () => {
  // ⚠️ 注释里提到这些名字是允许的（解释「为什么删」），
  //   所以只查**代码行**（`export const xxx =`）。
  assert.doesNotMatch(systemApi, /export const getRoles\b/)
  assert.doesNotMatch(systemApi, /export const createRole\b/)
  assert.doesNotMatch(systemApi, /export const updateRole\b/)
  assert.doesNotMatch(systemApi, /export const listRoles\b/)
  assert.doesNotMatch(systemApi, /export const getPermissions\b/)
  assert.doesNotMatch(systemApi, /export const getRolePermissions\b/)
  assert.doesNotMatch(systemApi, /export const saveRolePermissions\b/)
})

test('「已下线」的理由在代码里写清楚了', () => {
  // 没有理由说明 ⇒ 后人可能「好心」把它接回去，接回假数据。
  assert.match(systemApi, /硬编码假数据/)
  assert.match(workspaces, /硬编码假数据/)
  assert.match(router, /硬编码假数据/)
})

// ─────────────────────────────────────────────────────────────
// 6. 不误伤真实功能
// ─────────────────────────────────────────────────────────────

test('没把「员工注册/员工管理」用的真实 options 接口误删', () => {
  // ⚠️ 员工注册的角色下拉框走 ``GET /system/options``，
  //   那是**真实**接口（部门树 + 负责人 + 角色候选），
  //   与下线的 ``/system/roles`` 是两回事 ⇒ 不能误删。
  assert.match(systemApi, /export const getSystemOptions/)
  assert.match(systemApi, /export interface SystemOptions/)
  assert.match(systemApi, /export interface RoleItem/)
})
