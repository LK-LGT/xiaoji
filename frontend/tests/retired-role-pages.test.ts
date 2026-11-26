/**
 * 护栏：「角色管理 / 权限管理」两个**假页面**不许复活。
 *
 * ⚠️ **背景（2026-11-26）**
 *   这两个页面原来对应后端 `GET /v1/system/roles` / `/v1/system/permissions`，
 *   但那俩接口返回的是**硬编码假数据**，数据库里**根本没有 `role` 表**
 *   ⇒ 页面看起来能增删改，实际**存不进去**（POST/PUT 是 no-op）。
 *
 * ⚠️ **为什么不接真实数据**
 *   现在的权限模型**不是** RBAC 角色表，而是「角色 × 部门 × 汇报线」的
 *   **动态判定** —— 角色由 `services/access_control.py` 根据员工的
 *   部门、岗位、汇报线**自动推导**，不人工分配。
 *   ⇒ 做「角色表」等于倒退回被明确废弃的模型。
 *
 * ✅ **替代口径**
 *   - 权限配置 → `/system/department-modules`（部门功能板块）
 *   - 组织与汇报线 → `/system/orgs`（部门/项目组）
 */
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { WORKSPACES, visibleWorkspaceItems } from '../src/navigation/workspaces.ts'

const SRC = join(import.meta.dirname, '..', 'src')
const VIEWS_SYSTEM = join(SRC, 'views', 'system')

test('菜单里不再有「角色管理」/「权限管理」入口', () => {
  const system = WORKSPACES.find((workspace) => workspace.id === 'system')
  assert.ok(system)

  const items = visibleWorkspaceItems(system, [
    'system:dashboard:view',
    'system:role:view',
    'system:permission:view',
  ])
  const paths = items.map((item) => item.path)

  assert.ok(!paths.includes('/system/roles'), '「角色管理」菜单入口必须下线')
  assert.ok(!paths.includes('/system/permissions'), '「权限管理」菜单入口必须下线')
})

test('两个假页面文件已被删除', () => {
  assert.equal(existsSync(join(VIEWS_SYSTEM, 'RoleManagement.vue')), false)
  assert.equal(existsSync(join(VIEWS_SYSTEM, 'PermissionManagement.vue')), false)
})

test('下线的路由改指向「已下线说明页」', () => {
  const router = readFileSync(join(SRC, 'router', 'index.ts'), 'utf-8')
  assert.ok(
    router.includes("withAppShell('/system/roles', RetiredRoleManagement"),
    '/system/roles 必须指向 RetiredRoleManagement',
  )
  assert.ok(
    router.includes("withAppShell('/system/permissions', RetiredRoleManagement"),
    '/system/permissions 必须指向 RetiredRoleManagement',
  )
  assert.ok(!router.includes('import RoleManagement from'), '不得再 import RoleManagement')
  assert.ok(
    !router.includes('import PermissionManagement from'),
    '不得再 import PermissionManagement',
  )
})

test('已下线说明页存在且给出替代入口', () => {
  const retired = join(VIEWS_SYSTEM, 'RetiredRoleManagement.vue')
  assert.equal(existsSync(retired), true)

  const source = readFileSync(retired, 'utf-8')
  assert.ok(source.includes('/system/department-modules'), '要指向部门功能板块')
  assert.ok(source.includes('/system/orgs'), '要指向部门/项目组')
})

test('面包屑说清楚「已下线」', () => {
  const breadcrumbs = readFileSync(join(SRC, 'navigation', 'breadcrumbs.ts'), 'utf-8')
  assert.ok(breadcrumbs.includes("'/system/roles': '角色与权限（已下线）'"))
  assert.ok(breadcrumbs.includes("'/system/permissions': '角色与权限（已下线）'"))
})

test('系统页里没有指向已下线页面的入口', () => {
  const files = readdirSync(VIEWS_SYSTEM).filter((name) => name.endsWith('.vue'))
  for (const name of files) {
    const source = readFileSync(join(VIEWS_SYSTEM, name), 'utf-8')
    assert.ok(
      !source.includes("'/system/roles'") && !source.includes('"/system/roles"'),
      `${name} 不得再链接到 /system/roles`,
    )
    assert.ok(
      !source.includes("'/system/permissions'") && !source.includes('"/system/permissions"'),
      `${name} 不得再链接到 /system/permissions`,
    )
  }
})

test('前端不再调用已下线的角色/权限接口', () => {
  const source = readFileSync(join(SRC, 'api', 'system.ts'), 'utf-8')
  assert.ok(!source.includes("http.get<unknown, RoleItem[]>('/system/roles')"))
  assert.ok(!source.includes("'/system/permissions'"))
  assert.ok(!source.includes('`/system/roles/${id}/permissions`'))
})
