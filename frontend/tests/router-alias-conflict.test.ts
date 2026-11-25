import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

/**
 * 🔴 **路由 alias 冲突回归守卫**（2026-11-24）
 *
 * 用户投诉：「为什么**每次点这个板块的内容都会跳到另一个层级**」
 *
 * ⚠️ **这个文件锁的是另一个同源缺陷**
 * `/performance/pan-workload` 这条路由曾经**重复挂**了
 * alias `/performance/dashboard` —— 而 `/performance/dashboard`
 * 已经是 `/performance`（绩效工作台）那条路由的 alias。
 *
 * ⚠️ **为什么这是个雷**
 * Vue Router 里**同一个 alias 被两条路由声明时，先注册的那条生效**
 * ⇒ 只要有人调整路由顺序（比如把剪辑工作量往前挪），
 *    绩效工作台就会**渲染成剪辑工作量页**（或反过来）
 * ⇒ 表现为「点一下跳到另一个页面」。
 *
 * ⚠️ **为什么用静态扫描而不是真跑路由**
 * 真跑路由要 import 整个 router/index.ts ⇒ 会连带 import 几十个页面组件
 * ⇒ 在 ``node --test``（无 SFC 编译能力）里跑不起来。
 * ⇒ 这里做源码级断言，成本低且足够。
 */

const routerSource = readFileSync(
  fileURLToPath(new URL('../src/router/index.ts', import.meta.url)),
  'utf8',
)

/** 去掉注释，只留真实代码。 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1 ')
}

const routerCode = stripComments(routerSource)

test('没有任何两条路由声明同一个 alias', () => {
  /**
   * ⚠️ **通用守卫** —— 不只针对剪辑工作量，
   * 将来有人再挂重复 alias 也会被挡住。
   */
  const aliasCounts = new Map<string, number>()

  // 匹配 withAppShell(path, page, meta, alias) 的第四个参数
  const pattern = /withAppShell\(\s*'([^']+)'\s*,\s*[A-Za-z0-9_]+\s*,\s*\{[^}]*\}\s*,\s*'([^']+)'\s*\)/g
  for (const match of routerCode.matchAll(pattern)) {
    const alias = match[2]
    aliasCounts.set(alias, (aliasCounts.get(alias) ?? 0) + 1)
  }

  const duplicated = [...aliasCounts.entries()].filter(([, count]) => count > 1)
  assert.deepEqual(
    duplicated,
    [],
    `这些 alias 被多条路由重复声明（先注册者生效 ⇒ 调换顺序就会「跳到另一个页面」）：\n` +
      duplicated.map(([alias, count]) => `  ${alias} × ${count}`).join('\n'),
  )
})

test('剪辑工作量路由不再重复挂 /performance/dashboard 这个 alias', () => {
  /**
   * ⚠️ **针对性断言** —— 锁定这次真正修掉的那条。
   */
  const panRoute = routerCode.match(
    /withAppShell\(\s*'\/performance\/pan-workload'[^)]*\)/,
  )
  assert.ok(panRoute, '应能找到 /performance/pan-workload 的路由声明')

  assert.doesNotMatch(
    panRoute[0],
    /,\s*'\/performance\/dashboard'\s*\)/,
    '剪辑工作量路由不应再挂 /performance/dashboard 这个 alias（它属于绩效工作台）',
  )
})

test('绩效工作台的 /performance/dashboard alias 仍然保留', () => {
  /**
   * ⚠️ **配对断言** —— 不能为了消冲突把合法的 alias 也删了。
   * ⇒ `/performance/dashboard` 必须仍然能打开绩效工作台。
   */
  assert.match(
    routerCode,
    /withAppShell\(\s*'\/performance'\s*,\s*PerformanceDashboard\s*,\s*\{[^}]*\}\s*,\s*'\/performance\/dashboard'\s*\)/,
    '绩效工作台应继续提供 /performance/dashboard 这个入口',
  )
})
