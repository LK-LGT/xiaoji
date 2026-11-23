import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

/**
 * 百度网盘产出校验 —— 前端契约测试（BP-1）。
 *
 * 覆盖
 * ----
 * 1. 权限位贯通：后端 ROLE_UI_PERMISSIONS → 路由 → 侧边栏
 * 2. 核对面板**只在主管评价时显示**（员工填报时不显示）
 * 3. 核对面板文案**不得出现分数影响**措辞
 * 4. API 层不得暴露 token 字段
 */

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')

function read(relative: string): string {
  return readFileSync(join(root, relative), 'utf-8')
}

const PERM = 'system:pan-verification:manage'

// ─────────────────────────────────────────────────────────────────────────
// 1. 权限位贯通
// ─────────────────────────────────────────────────────────────────────────

test('backend grants the pan-verification permission to hr and admin', () => {
  const auth = read('../backend/app/routes/auth.py')
  assert.ok(auth.includes(`"${PERM}"`), 'auth.py 必须定义该权限位')
  const occurrences = auth.split(`"${PERM}"`).length - 1
  assert.ok(occurrences >= 2, `权限位应出现在 HR 与 admin 两处，实际 ${occurrences} 处`)
})

test('router registers the pan-verification page with the permission', () => {
  const router = read('../frontend/src/router/index.ts')
  assert.ok(router.includes('/system/pan-verification'), '必须注册路由')
  assert.ok(
    router.includes(`{ permission: '${PERM}' }`),
    '路由必须挂上权限位 —— 否则普通员工也能敲 URL 进来',
  )
})

test('sidebar item is guarded by the same permission', () => {
  const workspaces = read('../frontend/src/navigation/workspaces.ts')
  assert.ok(workspaces.includes('/system/pan-verification'), '必须注册侧边栏入口')
  assert.ok(
    workspaces.includes(`permission: '${PERM}'`),
    '侧边栏必须挂上同一个权限位',
  )
})

test('navigation and router use the same permission constant', () => {
  const router = read('../frontend/src/router/index.ts')
  const workspaces = read('../frontend/src/navigation/workspaces.ts')
  const routePerm = /\/system\/pan-verification'.*permission: '([^']+)'/.exec(router)?.[1]
  const navPerm = /\/system\/pan-verification'.*permission: '([^']+)'/.exec(workspaces)?.[1]
  assert.equal(routePerm, navPerm, '路由与侧边栏的权限位必须一致')
  assert.equal(routePerm, PERM)
})

// ─────────────────────────────────────────────────────────────────────────
// 2. 核对面板的显示时机
// ─────────────────────────────────────────────────────────────────────────

test('verification panel is hidden from the employee filling their own form', () => {
  const filling = read('../frontend/src/views/performance/SchemeFilling.vue')
  assert.ok(
    filling.includes('<PanVerificationPanel v-if="showVerification"'),
    '核对面板必须用 showVerification 控制显示',
  )
  assert.ok(
    filling.includes('const showVerification=computed(() => !!form.value && !form.value.is_own)'),
    '⚠️ 员工填报（is_own=true）时必须隐藏 —— ' +
      '网盘计数是部门级总量，在填报阶段给员工看会造成「这是我的产出」的误解',
  )
})

test('verification panel is mounted inside the scheme drawer', () => {
  const filling = read('../frontend/src/views/performance/SchemeFilling.vue')
  assert.ok(filling.includes("import PanVerificationPanel from '@/components/PanVerificationPanel.vue'"))
  const panelIndex = filling.indexOf('<PanVerificationPanel')
  const peerIndex = filling.indexOf('<PeerReviewPanel')
  assert.ok(panelIndex > 0 && peerIndex > 0)
  assert.ok(panelIndex > peerIndex, '核对面板应放在互评面板之后（汇总信息在末尾）')
})

// ─────────────────────────────────────────────────────────────────────────
// 3. 文案约束：只提示不判定
// ─────────────────────────────────────────────────────────────────────────

test('verification panel states that it does not affect scores', () => {
  const panel = read('../frontend/src/components/PanVerificationPanel.vue')
  assert.ok(
    panel.includes('不产生任何分数影响'),
    '⚠️ 面板必须明确声明「不产生任何分数影响」—— ' +
      '否则主管会误以为差异会自动扣分',
  )
})

test('verification panel does not offer score adjustment controls', () => {
  const panel = read('../frontend/src/components/PanVerificationPanel.vue')
  for (const forbidden of ['el-input-number', 'suggestedScore', 'finalScore']) {
    assert.ok(
      !panel.includes(forbidden),
      `⚠️ 核对面板不得包含「${forbidden}」—— 数据源只提供依据，判定权在人`,
    )
  }
})

test('manager page also states that results do not affect scores', () => {
  const page = read('../frontend/src/views/system/PanVerificationManagement.vue')
  assert.ok(
    page.includes('不产生任何分数影响'),
    '管理页也必须声明不影响分数',
  )
})

// ─────────────────────────────────────────────────────────────────────────
// 4. API 层不得暴露 token
// ─────────────────────────────────────────────────────────────────────────

test('api layer never exposes token fields', () => {
  const api = read('../frontend/src/api/baiduPan.ts')
  for (const forbidden of ['access_token', 'refresh_token', 'accessToken', 'refreshToken']) {
    assert.ok(
      !api.includes(forbidden),
      `⚠️ API 类型不得暴露「${forbidden}」—— 后端不返回，前端也不该声明`,
    )
  }
})

test('api layer does not send the app secret', () => {
  const api = read('../frontend/src/api/baiduPan.ts')
  assert.ok(!api.includes('app_secret') && !api.includes('appSecret'))
})
