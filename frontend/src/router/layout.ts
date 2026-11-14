/**
 * 后台框架布局的路由组件工厂。
 *
 * 采用标准 Vue Router 嵌套路由：
 *   { path: '/system/dashboard', component: AppShell,
 *     children: [{ path: '', component: SystemDashboard }] }
 *
 * AppShell 内部用 <router-view /> 渲染 depth=1 的子路由（页面组件），
 * App.vue 里的顶层 outlet 渲染 depth=0 的 AppShell。
 *
 * 为什么不用「组件包装」（每条路由 component = withAppShell(页面组件)）：
 * 那样所有后台路由会共用同一个 AppShell 实例，
 * 路由切换时「旧 AppShell 被卸载」与「页面组件的响应式更新」时序冲突，
 * Vue 的 DOM patch 会抛
 * "Cannot read properties of null (reading 'parentNode')"，
 * patch 中断后内容区会永远停在旧页面 —— 表现为
 * 「左侧菜单能点、右侧内容区一直空白 / 停在上一页」。
 *
 * 交给 Vue Router 的嵌套路由管理 outlet 深度就没有这个问题。
 */
import type { RouteRecordRaw } from 'vue-router'
import AppShell from '@/components/AppShell.vue'

/**
 * 把一条「页面路由」包装成「AppShell 父路由 + 页面子路由」。
 *
 * 用 pathless 子路由（path: ''）保持对外 URL 完全不变：
 *   withAppShell('/system/dashboard', SystemDashboard, { permission: 'x' })
 *   -> /system/dashboard  (URL 不变)
 *
 * @param path     对外的完整路径
 * @param page     页面组件
 * @param meta     路由 meta（权限等）
 * @param alias    额外的别名路径
 */
export function withAppShell(
  path: string,
  page: NonNullable<RouteRecordRaw['component']>,
  meta?: RouteRecordRaw['meta'],
  alias?: string | string[],
): RouteRecordRaw {
  const record: RouteRecordRaw = {
    path,
    component: AppShell,
    meta,
    children: [{ path: '', component: page }],
  }
  // alias 必须是字符串或数组；传 undefined 会让 Vue Router 的 addRoute
  // 在 "for (const alias of aliases)" 处抛 "aliases is not iterable"。
  if (alias !== undefined) record.alias = alias
  return record
}
