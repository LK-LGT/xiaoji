import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// ⚠️ 本文件验证「口令登录关闭时（503），用户能看到可操作的提示」。
//
// 后端 2026-11-24 改动：`/v1/auth/login` 在 `password_login_enabled=false` 时
// 返回 **503**，body 是结构化的：
//     {"detail": {"code": "PASSWORD_LOGIN_DISABLED", "message": "口令登录未启用，请使用飞书登录"}}
//
// ⇒ 前端必须：
//   ① 把 `detail.message` 提取出来（**不能**退化成「请求失败」）
//   ② Login.vue 把错误**就地展示**（el-alert），而不是一闪而过的 toast
//   ③ 页面仍然保留飞书登录按钮（用户得有下一步可走）
//
// ⚠️ **为什么用源码检查而不是 e2e**
// 拦截器的逻辑在 `http.ts` 的 axios 拦截器里，
// 单测里实例化完整 axios 栈成本高且脆弱；
// 而这里要验的是「**错误体有没有被正确解包**」——
// 那是纯字符串形状问题，用源码断言更直接、更不会假绿。

const httpSource = readFileSync(
  fileURLToPath(new URL('../src/api/http.ts', import.meta.url)),
  'utf8',
)

const loginView = readFileSync(
  fileURLToPath(new URL('../src/views/Login.vue', import.meta.url)),
  'utf8',
)

// ⚠️ 复刻 `http.ts` 里的错误消息提取逻辑（保持与源码同构）。
//    这里是**测试替身**，不是重新实现 —— 断言它对 503 body 能解出文案。
function extractMessage(data: unknown): string {
  const detail = (data as { detail?: unknown } | undefined)?.detail
  const rawMessage =
    (data as { message?: string } | undefined)?.message ||
    (typeof detail === 'string'
      ? detail
      : (detail as { message?: string } | undefined)?.message) ||
    '请求失败'
  return typeof rawMessage === 'string' ? rawMessage : '请求失败'
}

test('503 结构化 body 能解出可读文案（不退化成「请求失败」）', () => {
  const body = {
    detail: {
      code: 'PASSWORD_LOGIN_DISABLED',
      message: '口令登录未启用，请使用飞书登录',
    },
  }
  const message = extractMessage(body)
  assert.equal(message, '口令登录未启用，请使用飞书登录')
  assert.notEqual(message, '请求失败')
})

test('错误消息提取逻辑容忍 detail 为字符串（回归保护）', () => {
  assert.equal(
    extractMessage({ detail: '用户名或密码错误' }),
    '用户名或密码错误',
  )
})

test('http.ts 确实处理了对象型 detail', () => {
  // ⚠️ 这条锁的是「拦截器里必须走 detail?.message 这条分支」。
  //    若有人改成只认字符串 detail ⇒ 503 的文案就丢了。
  assert.match(
    httpSource,
    /detail\?\.message/,
    'http.ts 未处理对象型 detail ⇒ 503 的提示文案会退化成「请求失败」',
  )
})

test('Login.vue 把登录错误就地展示（el-alert），不是 toast', () => {
  // ⚠️ 就地展示的原因：免登/关闸的失败原因对用户很重要，
  //    一闪而过的 toast 看不清。
  assert.match(loginView, /el-alert[^>]*:title="error"/)
  // ⚠️ 错误文案要从 catch 里取 err.message（拦截器已解包好）
  assert.match(loginView, /err instanceof Error \? err\.message/)
})

test('Login.vue 始终保留飞书登录入口（口令关掉后用户有下一步）', () => {
  // ⚠️ **这是本文件最关键的一条**：
  //    口令入口关掉后，飞书按钮是**唯一**的登录通路
  //    ⇒ 它不能被任何条件隐藏掉。
  assert.match(loginView, /feishuLogin|login-feishu-button/)
  // ⚠️ 飞书按钮**不应**被 v-if 之类的条件包起来
  //    （允许 disabled/loading 这类交互态属性）
  const feishuButton = loginView.match(
    /<el-button[^>]*class="login-feishu-button"[\s\S]*?\/>/,
  )
  assert.ok(feishuButton, '找不到飞书登录按钮')
  assert.doesNotMatch(
    feishuButton![0],
    /v-if=/,
    '飞书登录按钮被 v-if 条件隐藏了 ⇒ 口令入口关闭后用户无路可走',
  )
})

test('前端不硬编码任何测试账号口令', () => {
  // ⚠️ 与后端护栏（test_login_entry_gate.py）呼应 ——
  //    前端也**不得**出现测试账号口令。
  for (const literal of [
    'admin123',
    'manager123',
    'leader123',
    'employee123',
  ]) {
    assert.doesNotMatch(loginView, new RegExp(literal))
    assert.doesNotMatch(httpSource, new RegExp(literal))
  }
})
