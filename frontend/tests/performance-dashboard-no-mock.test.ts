import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

/**
 * 🔴 **绩效工作台「假数据」守卫**（2026-11-24）
 *
 * 用户投诉背景
 * ------------
 * 打开 `/performance/dashboard`（绩效管理 →「管理工作台」，
 * 也是有 `performance:dashboard:view` 权限的人的**默认落地页**）
 * 看到的是**写死的假数据**：
 *
 * - 任务列表里有一条假任务「2026年Q4绩效考核」
 * - 最近记录里有两个**根本不存在的员工**「张三」「李四」
 * - 点「发起考核任务」→ 弹「考核任务已发起」→ **数据库里什么都没变**
 * - 点「查看详情」→ 跳 `/performance/tasks/T001` → **该路由不存在**
 *
 * ⚠️ **为什么这是「最坏的一类缺陷」**
 * 页面**看起来完全正常**：有数据、有按钮、有成功提示。
 * ⇒ 验收的人会以为「系统跑通了」，
 *    而实际上一条请求都没发出去。
 *
 * ⚠️ **这跟 BUG-R6-41 是同一类问题**
 * `EmployeeSelfReview.vue` 当时也是「一个请求都没发」，
 * 已被修掉（本文件会保证它不再回退）。
 *
 * ⚠️ **为什么要用静态扫描而不是组件测试**
 * 组件测试要 mock 掉整个 http 层才能断言「发没发请求」，
 * 反而**掩盖**了「这页根本不调后端」这个事实。
 * ⇒ 直接扫源码，找**不该存在的字面量与 TODO 注释**。
 */

/** 前端源码根目录 */
const SRC = join(import.meta.dirname, '..', 'src')

/** 递归收集所有 .vue / .ts 文件 */
function collectFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...collectFiles(full))
    else if (/\.(vue|ts)$/.test(entry)) out.push(full)
  }
  return out
}

/**
 * ⚠️ **这些标记说明「这个页面没接后端」**
 *
 * - `这里应该调用后端` —— 源码里的原话，作者自己留的 TODO
 * - `模拟数据` —— 硬编码的假数据块
 */
const MOCK_MARKERS = ['这里应该调用后端', '模拟数据']

/**
 * ⚠️ **这些是「假员工」的固定名字**
 *
 * ⚠️ **为什么连假数据两个字都不放过**
 * 因为 `EmployeeSelfReview.vue` 的注释里就**引用**了
 * 「R001 / T001 / "2026年Q4绩效考核"」这段旧 mock 作为反例
 * ⇒ 直接搜字符串会误报 ⇒ 必须**排除注释**。
 */
const FAKE_NAMES = ['张三', '李四', '王五']

/** 去掉 JS/TS/Vue 注释，返回纯代码 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/.*$/gm, '$1 ')
}

/** 去掉 HTML 注释 */
function stripHtmlComments(src: string): string {
  return src.replace(/<!--[\s\S]*?-->/g, ' ')
}

test('没有任何页面残留「这里应该调用后端」的 TODO', () => {
  const offenders: string[] = []
  for (const file of collectFiles(SRC)) {
    const code = stripHtmlComments(stripComments(readFileSync(file, 'utf-8')))
    for (const marker of MOCK_MARKERS) {
      if (code.includes(marker)) {
        offenders.push(`${file.replace(SRC, 'src')}: 「${marker}」`)
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `以下页面还留着「没接后端」的标记：\n${offenders.join('\n')}\n` +
      `⇒ 页面看起来正常、实际一个请求都没发 ⇒ 必须接真实 API。`,
  )
})

test('绩效工作台（PerformanceDashboard）不得再有硬编码假数据', () => {
  const file = join(SRC, 'views', 'performance', 'PerformanceDashboard.vue')
  const raw = readFileSync(file, 'utf-8')
  const code = stripHtmlComments(stripComments(raw))

  // ⚠️ **逐个断言**，报错时要能直接看出是哪一个
  for (const marker of MOCK_MARKERS) {
    assert.ok(
      !code.includes(marker),
      `PerformanceDashboard.vue 仍有「${marker}」⇒ 它还是假数据页`,
    )
  }

  for (const name of FAKE_NAMES) {
    assert.ok(
      !code.includes(name),
      `PerformanceDashboard.vue 仍有假员工「${name}」⇒ ` +
        `用户会以为系统里有这个人`,
    )
  }

  // ⚠️ **必须真的调后端** —— 有 API 调用才算接上
  assert.ok(
    /from '@\/api|await\s+http\.|http\.(get|post|put|delete)/.test(code),
    'PerformanceDashboard.vue 没有调用任何 API ⇒ 看板数据是假的',
  )
})

test('绩效工作台不得跳转到不存在的路由', () => {
  /**
   * ⚠️ **为什么单独断言**
   * 「点查看详情跳到空白页」是最容易被忽略的坏体验 ——
   * 表格点得动、URL 也变了，就是没有页面。
   */
  const file = join(SRC, 'views', 'performance', 'PerformanceDashboard.vue')
  const code = stripHtmlComments(stripComments(readFileSync(file, 'utf-8')))

  const routerSrc = readFileSync(join(SRC, 'router', 'index.ts'), 'utf-8')

  for (const match of code.matchAll(/router\.push\(`([^`]+)`\)/g)) {
    const target = match[1]
    if (!target.startsWith('/performance/tasks/')) continue
    const base = target.replace(/\/[^/]*$/, '')
    assert.ok(
      routerSrc.includes(base),
      `PerformanceDashboard.vue 跳转到 ${target}，但路由表里没有「${base}」`,
    )
  }
})

test('绩效看板确实接了后端（防止「修一半」）', () => {
  /**
   * ⚠️ **配对断言** —— 只断言「没有 mock」不够：
   * 一个**空白页**也没有 mock。
   * ⇒ 必须同时断言「有 API 调用」+「有数据渲染」。
   */
  const file = join(SRC, 'views', 'performance', 'PerformanceDashboard.vue')
  const code = stripHtmlComments(stripComments(readFileSync(file, 'utf-8')))

  assert.ok(
    /from '@\/api/.test(code),
    'PerformanceDashboard.vue 没有 import 任何 api 模块 ⇒ 数据源不对',
  )
  assert.ok(
    /await\s+http\.|await\s+\w+(Api|List|Get|Create)\w*\(/.test(code),
    'PerformanceDashboard.vue 没有发起任何请求 ⇒ 看板数据是假的',
  )
})

test('工作台发起考核必须校验周期格式（只支持 YYYYMM）', () => {
  /**
   * ⚠️ **为什么这条重要**
   *
   * 后端 `POST /v1/performance/tasks` 内部调
   * `scheme_service.resolve(db, e, request.period)`，
   * 而 `resolve` 的 **`period_type` 默认是 `"monthly"`**，
   * 方案绑定又是**按 `period_type` 精确匹配**的。
   *
   * ⇒ 用户若填「2026Q4」或「2026」，
   *   **匹配不到任何已发布方案** ⇒ 后端 409 / 422，
   *   而错误信息（"请从考核方案管理按版本分组发起"）对普通用户并不直观。
   *
   * ⇒ 前端**必须先拦一道**，给出可操作提示。
   */
  const file = join(SRC, 'views', 'performance', 'PerformanceDashboard.vue')
  const code = stripHtmlComments(stripComments(readFileSync(file, 'utf-8')))

  assert.ok(
    /\^\\d\{6\}\$/.test(code) || /\\d\{6\}/.test(code),
    '工作台发起考核没有校验周期格式（应为 YYYYMM）',
  )
  assert.ok(
    /方案管理/.test(code),
    '周期格式受限时没有引导用户去「方案管理」（季度/年度考核的正确入口）',
  )
})
