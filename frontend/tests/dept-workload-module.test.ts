/**
 * ★ **「部门工作量」前端**的回归测试（2026-11-25）。
 *
 * ⚠️ **为什么要有这个测试**
 * ----------------------
 * 这次给 20 个真实部门开了新板块 ⇒ 前端**必须**做到：
 *
 * ① **菜单由后端下发** —— 前端不写死「部门工作量」这个入口
 * ② **两个板块的入口互不串** —— 剪辑不能看到「部门工作量」
 * ③ **指标由后端下发** —— 前端**不定义**指标标签/量纲
 *    （指标后续才定 ⇒ 硬编码必然是错的）
 *
 * ⚠️ **第 ③ 点最关键**
 * `panWorkload.ts` 里硬编码了 5 个视频指标（`MANUAL_METRIC_LABELS`），
 * 而通用板块的指标**还没定** ⇒ 通用板块必须走**后端下发**的路子。
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (relativePath: string) =>
  readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')

/**
 * ⚠️ **去掉注释**再断言。
 *
 * 注释里会**引用**旧代码作反例（说明「为什么不用它」）
 * ⇒ 直接搜原文会**误报**。
 * ⇒ 只看**真正的代码**。
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

const workspaces = stripComments(read('../src/navigation/workspaces.ts'))
const router = stripComments(read('../src/router/index.ts'))
const api = stripComments(read('../src/api/deptWorkload.ts'))
const page = stripComments(read('../src/views/performance/DeptWorkloadPage.vue'))

const DEPT_PATH = '/performance/dept-workload'

// ═════════════════════════════════════════════════════════════════════════
// ★ **菜单由后端下发**
// ═════════════════════════════════════════════════════════════════════════

test('写死的工作区清单里没有「部门工作量」', () => {
  /**
   * ⚠️ **核心断言** —— 菜单**不能**写死在前端。
   * 写死 ⇒ 每个部门的人都能看到 ⇒ 板块隔离失效。
   */
  assert.ok(
    !workspaces.includes('部门工作量'),
    '工作区清单里硬编码了「部门工作量」⇒ 板块隔离失效',
  )
  assert.ok(
    !workspaces.includes(DEPT_PATH),
    '工作区清单里硬编码了部门工作量路径 ⇒ 板块隔离失效',
  )
})

test('路由已挂载 /performance/dept-workload', () => {
  assert.ok(router.includes(DEPT_PATH), '路由缺少部门工作量入口')
  assert.ok(router.includes('DeptWorkloadPage'), '路由缺少页面组件')
})

test('路由用剪辑板块那个权限串（同一套填报机制）', () => {
  /**
   * ⚠️ 两个板块是**同一套填报机制**，区别只在
   * 「哪个部门开通」+「填什么指标」⇒ 权限串共用。
   */
  const idx = router.indexOf(DEPT_PATH)
  assert.ok(idx > -1, '路由缺少部门工作量入口')
  assert.ok(
    router.slice(idx, idx + 200).includes('pan:workload:view'),
    '部门工作量路由应复用 pan:workload:view 权限串',
  )
})

// ═════════════════════════════════════════════════════════════════════════
// ★ **指标由后端下发**
// ═════════════════════════════════════════════════════════════════════════

test('API 客户端不硬编码任何指标标签', () => {
  /**
   * ⚠️ **核心断言** —— 指标还没定，
   * 前端若写死标签 ⇒ 业务定了新指标前端就得改代码。
   */
  assert.ok(!api.includes('MANUAL_METRIC_LABELS'), '不应复用剪辑的硬编码指标表')
  for (const label of ['普通视频', '完播率', 'OOTD', '智能体']) {
    assert.ok(!api.includes(label), `API 客户端不应硬编码指标「${label}」`)
  }
})

test('API 客户端调用后端的 my-scope 取口径', () => {
  /**
   * ⚠️ **路径不能带 `/v1`** —— vite 代理会把 ``/api/xxx`` 重写成 ``/v1/xxx``。
   * ⇒ 这里写 ``/v1/xxx`` 会变成 ``/v1/v1/xxx`` ⇒ **404**。
   */
  assert.ok(api.includes('my-scope'), '缺少 my-scope 调用')
  assert.ok(api.includes('my-metrics'), '缺少 my-metrics 调用')
  assert.ok(api.includes('manual-metrics'), '缺少填报提交调用')
  assert.ok(
    !api.includes("'/v1/dept/workload"),
    '前端路径不能带 /v1（vite 代理会再加一层 ⇒ 404）',
  )
  assert.ok(api.includes("const BASE = '/dept/workload'"), 'BASE 常量口径不对')
})

test('API 客户端声明指标元数据字段', () => {
  assert.ok(api.includes('kind'), '缺少指标量纲字段 kind')
  assert.ok(api.includes("'count'"), '缺少 count 量纲')
  assert.ok(api.includes("'rate'"), '缺少 rate 量纲')
})

test('页面按后端下发的 metrics 渲染，不写死指标', () => {
  /**
   * ⚠️ 页面必须**遍历后端给的 metrics**，
   * 而不是渲染一个写死的「普通视频/OOTD/智能体」表单。
   */
  assert.ok(
    page.includes('v-for="spec in metrics"'),
    '页面必须遍历后端下发的 metrics',
  )
  for (const label of ['普通视频', 'OOTD', '智能体']) {
    assert.ok(!page.includes(label), `页面不应硬编码指标「${label}」`)
  }
})

test('页面区分 count 与 rate 两种量纲', () => {
  /**
   * ⚠️ **量纲不同显示方式不同**：
   * count → 「8」；rate → 「35.0%」（库里存 0.35）。
   * 一律当比率处理 ⇒ 「8 条」会显示成「800.0%」。
   */
  assert.ok(page.includes("spec.kind === 'rate'"), '页面未按 kind 区分量纲')
  assert.ok(page.includes('* 100'), '比率应换算成百分比显示')
})

// ═════════════════════════════════════════════════════════════════════════
// ★ **空指标要有明确提示**
// ═════════════════════════════════════════════════════════════════════════

test('指标为空时显示提示而不是空白页', () => {
  /**
   * ⚠️ **指标还没定** ⇒ 后端下发空列表
   * ⇒ 页面必须**明确告诉用户「指标待确认」**
   * ⇒ 否则用户以为坏了。
   */
  assert.ok(page.includes('填报指标待业务确认'), '指标为空时应给出明确提示')
})

test('角色为 none 时给出说明', () => {
  assert.ok(page.includes('暂未纳入该板块'), '角色为 none 时应说明原因')
})
