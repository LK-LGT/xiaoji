/**
 * 🔴 **组件测试（CI-5 阶段 2）** —— 飞书免登回调页。
 *
 * ⚠️ **这个文件替换了什么**
 * ------------------------------------------------------------------
 * 原有断言（``tests/feishu-login.test.ts``）：
 *
 * ```typescript
 * assert.match(callbackView, /v-if="retryable"/)
 * ```
 *
 * ⇒ 那验证的是「**源码里有这一行字**」。
 *
 * ⚠️ **为什么那是假的保障**
 *   把 ``v-if="retryable"`` 改成 ``v-if="retryableX"`` ——
 *   正则 ``/v-if="retryable"/`` **仍然匹配得上**（``retryable`` 是子串）⇒
 *   旧断言**照样全绿**，而「重新发起飞书登录」按钮已经永远不显示了。
 *
 * ⚠️ **本文件真正测的东西**
 *   ``REASON_HINTS`` 里 5 种诊断码 → 「重试按钮」该不该显示。
 *   这是**产品语义**（已离职的人重试必然还是同样结果，
 *   展示一个注定失败的按钮只会让人反复点），
 *   而**源码断言完全测不到它**。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, test, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'

// ⚠️ **路由 mock** —— 组件用 ``useRoute().query`` 读回调参数。
const { routeQuery, routerReplace } = vi.hoisted(() => ({
  routeQuery: { value: {} as Record<string, string> },
  routerReplace: vi.fn(async () => {}),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeQuery.value }),
  useRouter: () => ({ replace: routerReplace }),
}))

// ⚠️ **免登换 token 的 API** —— 失败路径靠它抛错并带 ``reason``。
const { exchangeFeishuCodeApi } = vi.hoisted(() => ({
  exchangeFeishuCodeApi: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  exchangeFeishuCodeApi: (...args: unknown[]) => exchangeFeishuCodeApi(...args),
}))

// ⚠️ **飞书登录工具** —— ``startFeishuLogin`` 会真的跳转。
vi.mock('@/api/feishuLogin', () => ({
  consumeFeishuRedirectIntent: () => '',
  startFeishuLogin: vi.fn(),
}))

// ⚠️ **鉴权 store** —— 免登成功后要走 ``loadMe`` / ``user.permissions``。
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    token: '',
    refreshToken: '',
    user: { permissions: [], forceChangePassword: false },
    loadMe: vi.fn(async () => {}),
  }),
}))

import FeishuCallback from '../../src/views/FeishuCallback.vue'

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

/** 让免登失败，并带上后端诊断码。 */
function failWith(reason?: string) {
  const error = new Error('免登失败') as Error & { reason?: string }
  if (reason) error.reason = reason
  exchangeFeishuCodeApi.mockRejectedValue(error)
}

/** 挂载并等 onMounted 里的失败流程跑完。 */
async function mountFailed(reason?: string) {
  failWith(reason)
  const wrapper = mount(FeishuCallback)
  // ⚠️ onMounted 是 async —— 要等它把 status 改成 'error' 并 applyReason。
  await nextTick()
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
  return wrapper
}

/** 按钮文本列表。 */
function buttonLabels(wrapper: Awaited<ReturnType<typeof mountFailed>>) {
  return wrapper.findAll('button').map((b) => b.text())
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 「重新发起飞书登录」按钮的可重试语义
// ═══════════════════════════════════════════════════════════════════════════

describe('按诊断码决定是否显示「重新发起飞书登录」', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeQuery.value = { code: 'abc', state: 'st' }
    // ⚠️ jsdom 没有 sessionStorage 的持久实现 —— 每个用例重来一次。
    sessionStorage.clear()
  })

  // ⚠️ **不可重试的三种**：重试必然还是同样的结果。
  for (const reason of ['exited', 'disabled', 'ambiguous']) {
    test(`${reason} → **不显示**「重新发起飞书登录」`, async () => {
      const wrapper = await mountFailed(reason)

      expect(buttonLabels(wrapper)).not.toContain('重新发起飞书登录')
      // ⚠️ **但「改用账号密码登录」必须在** ——
      //    否则用户被卡死在死路上。
      expect(buttonLabels(wrapper)).toContain('改用账号密码登录')
    })
  }

  // ⚠️ **可重试的两种**：重试有意义。
  for (const reason of ['inactive_binding', 'not_synced']) {
    test(`${reason} → **显示**「重新发起飞书登录」`, async () => {
      const wrapper = await mountFailed(reason)

      expect(buttonLabels(wrapper)).toContain('重新发起飞书登录')
      expect(buttonLabels(wrapper)).toContain('改用账号密码登录')
    })
  }

  test('未知诊断码 → 保持默认（可重试）', async () => {
    // ⚠️ **保守默认**：后端加了新诊断码时，前端不该把用户堵死。
    const wrapper = await mountFailed('some_new_reason')

    expect(buttonLabels(wrapper)).toContain('重新发起飞书登录')
  })

  test('没有 reason → 保持默认（可重试）', async () => {
    const wrapper = await mountFailed()

    expect(buttonLabels(wrapper)).toContain('重新发起飞书登录')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 针对性提示文案
// ═══════════════════════════════════════════════════════════════════════════

describe('按诊断码给出针对性提示', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeQuery.value = { code: 'abc', state: 'st' }
    sessionStorage.clear()
  })

  test('exited → 提示「已离职」', async () => {
    const wrapper = await mountFailed('exited')

    expect(wrapper.text()).toContain('已离职')
    // ⚠️ 通用兜底文案不该同时出现 —— 那说明 applyReason 没生效。
    expect(wrapper.text()).not.toContain('多半是「飞书后台的重定向 URL')
  })

  test('disabled → 提示「已被停用」', async () => {
    const wrapper = await mountFailed('disabled')

    expect(wrapper.text()).toContain('已被停用')
  })

  test('not_synced → 提示「尚未同步到系统」', async () => {
    const wrapper = await mountFailed('not_synced')

    expect(wrapper.text()).toContain('尚未同步到系统')
  })

  test('无诊断码 → 退回通用提示', async () => {
    const wrapper = await mountFailed()

    expect(wrapper.text()).toContain('多半是「飞书后台的重定向 URL')
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 失败页的基本形态
// ═══════════════════════════════════════════════════════════════════════════

describe('失败页展示正确', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeQuery.value = { code: 'abc', state: 'st' }
    sessionStorage.clear()
  })

  test('标题是「免登未完成」', async () => {
    const wrapper = await mountFailed('exited')

    expect(wrapper.text()).toContain('免登未完成')
  })

  test('用户主动取消授权（query 带 error）→ 不调换 token 接口', async () => {
    // ⚠️ **关键短路**：用户点了「拒绝授权」⇒ 根本不该去换 token。
    //    若这个短路失效，后端会收到一次无意义的 code 交换请求。
    routeQuery.value = { error: 'access_denied', error_description: '用户拒绝授权' }
    exchangeFeishuCodeApi.mockResolvedValue({ accessToken: 'x', refreshToken: 'y' })

    const wrapper = mount(FeishuCallback)
    await nextTick()
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(exchangeFeishuCodeApi).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('免登未完成')
    expect(wrapper.text()).toContain('用户拒绝授权')
  })

  test('缺少 code/state → 报「回调参数不完整」且不调接口', async () => {
    routeQuery.value = {}
    exchangeFeishuCodeApi.mockResolvedValue({ accessToken: 'x', refreshToken: 'y' })

    const wrapper = mount(FeishuCallback)
    await nextTick()
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(exchangeFeishuCodeApi).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('回调参数不完整')
  })
})
