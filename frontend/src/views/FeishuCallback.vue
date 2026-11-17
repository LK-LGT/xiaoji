<template>
  <main class="feishu-callback">
    <section class="feishu-callback__card">
      <!-- 处理中 -->
      <template v-if="status === 'pending'">
        <span class="feishu-callback__code">FEISHU SSO</span>
        <h1>正在完成免登</h1>
        <p>正在用飞书身份换取本系统凭证，请稍候……</p>
        <el-icon class="feishu-callback__spinner" :size="26"><Loading /></el-icon>
      </template>

      <!-- 成功（通常一闪而过，随即跳走） -->
      <template v-else-if="status === 'success'">
        <span class="feishu-callback__code">FEISHU SSO</span>
        <h1>登录成功</h1>
        <p>正在进入你的工作台……</p>
        <el-icon class="feishu-callback__spinner" :size="26"><Loading /></el-icon>
      </template>

      <!-- 失败 -->
      <template v-else>
        <span class="feishu-callback__code feishu-callback__code--error">FAILED</span>
        <h1>免登未完成</h1>
        <el-alert :title="error" type="error" :closable="false" show-icon />
        <!--
          ⚠️ **按诊断码给针对性提示**（2026-11-17 改）。

          原来是一句通用文案：「若反复失败，多半是重定向 URL 不一致，请联系管理员」。
          但「identity_not_bound」背后有四种要处理方式完全不同的情况 ——
          把它们混成一句，会让用户和排查者都走错方向：

            exited        人已离职   → 提「重新发起登录」是**无用**的
            disabled      账号被停用 → 提「重定向 URL」更是**误导**
            not_synced    未同步     → 该找管理员跑同步
            ambiguous     数据冲突   → 该人工解绑
        -->
        <p v-if="hint" class="feishu-callback__hint">{{ hint }}</p>
        <p v-else class="feishu-callback__hint">
          飞书免登失败不影响账号密码登录。<br />
          若反复失败，多半是「飞书后台的重定向 URL 与本系统配置不一致」，
          请联系管理员核对。
        </p>
        <div class="feishu-callback__actions">
          <!-- ⚠️ 已离职 / 已停用时，「重新发起飞书登录」必然还是同样的结果 ——
               展示一个注定失败的按钮只会让人反复点。 -->
          <el-button v-if="retryable" type="primary" @click="retry">重新发起飞书登录</el-button>
          <el-button type="primary" @click="goPasswordLogin">改用账号密码登录</el-button>
        </div>
      </template>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { exchangeFeishuCodeApi } from '@/api/auth'
import { consumeFeishuRedirectIntent, startFeishuLogin } from '@/api/feishuLogin'
import { defaultWorkspacePath, hasPermission } from '@/navigation/workspaces'
import { useAuthStore } from '@/stores/auth'

/**
 * ═══════════════════════════════════════════════════════════════
 * 飞书免登 OAuth 回调页（FS-2）
 * ═══════════════════════════════════════════════════════════════
 *
 * 飞书授权成功后 302 跳回本页，URL 上带 `code` + `state`。
 * 本页负责：拿 code 换本系统 token → 落到工作台。
 *
 * ⚠️ **为什么回调落在前端而不是后端**：
 * 飞书的 code 是**一次性**的，且**只能换一次** → 必须立刻转发给后端，
 * 不能在页面里刷新重试。后端 `/exchange` 拿到 code 后换 token、解析员工身份、
 * 再签发**本系统**的 HMAC token（飞书 token 用完即弃，绝不下发前端）。
 *
 * ⚠️ **三条不能破的约束**（Runbook `17` §3.2）：
 * 1. **不在前端存飞书 token** —— 前端只拿本系统 token；
 * 2. **回调页要幂等** —— OAuth 回调可能重复触发（用户刷新 / 双击），
 *    而 code 用一次就废 → 重复提交会拿到「code 已被使用」的错误。
 *    本页用 `sessionStorage` 记「本次回调已处理」，避免把用户误导到错误页。
 * 3. **密码登录必须保留** —— 飞书故障时全员无法登录是灾难。
 */

type CallbackStatus = 'pending' | 'success' | 'error'

/** 本次回调是否已处理过（幂等护栏，Runbook §3.2 约束 2）。 */
const HANDLED_KEY = 'planning_feishu_callback_handled'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const status = ref<CallbackStatus>('pending')
const error = ref('')

/**
 * 针对具体失败原因的提示（空字符串 = 用默认通用文案）。
 *
 * ⚠️ **为什么要按诊断码分流**（2026-11-17 改）：
 * 「identity_not_bound」背后有四种要处理方式**完全不同**的情况。
 * 混成一句通用文案会让人走错方向 ——
 * 比如「已离职」的人看到「请检查重定向 URL」纯属误导。
 */
const hint = ref('')

/**
 * 是否展示「重新发起飞书登录」按钮。
 *
 * ⚠️ 已离职 / 已停用 / 数据冲突时，**重试必然还是同样的结果** ——
 * 展示一个注定失败的按钮只会让人反复点，还以为是网络问题。
 */
const retryable = ref(true)

/** 后端诊断码 → (提示文案, 是否可重试)。 */
const REASON_HINTS: Record<string, { hint: string; retryable: boolean }> = {
  exited: {
    hint: '该员工账号已离职，无法使用飞书免登登录。'
      + '如需恢复，请联系管理员在系统中重新启用账号。',
    retryable: false,
  },
  disabled: {
    hint: '该员工账号已被停用，无法使用飞书免登登录。'
      + '如需恢复，请联系管理员解除停用。',
    retryable: false,
  },
  inactive_binding: {
    hint: '该飞书账号在系统中的绑定记录已失效，'
      + '请联系管理员执行一次组织架构同步后重试。',
    retryable: true,
  },
  ambiguous: {
    hint: '该飞书账号在系统中存在多条绑定记录，'
      + '请联系管理员处理重复绑定后再试。',
    retryable: false,
  },
  not_synced: {
    hint: '该飞书账号尚未同步到系统，'
      + '请联系管理员执行一次组织架构同步后重试。',
    retryable: true,
  },
}

/** 授权失败时飞书可能带上 `error` / `error_description`（用户点了「拒绝授权」）。 */
function readQuery(key: string): string {
  const value = route.query[key]
  if (typeof value === 'string') return value
  if (Array.isArray(value) && typeof value[0] === 'string') return value[0]
  return ''
}

/** 按后端诊断码填入针对性提示。 */
function applyReason(reason: string | undefined): void {
  if (!reason) return
  const matched = REASON_HINTS[reason]
  if (!matched) return
  hint.value = matched.hint
  retryable.value = matched.retryable
}

onMounted(async () => {
  const oauthError = readQuery('error')
  if (oauthError) {
    // 用户在飞书页面上点了「拒绝授权」/ 主动取消
    status.value = 'error'
    error.value = readQuery('error_description') || '你取消了飞书授权，请重试或改用账号密码登录。'
    return
  }

  const code = readQuery('code')
  const state = readQuery('state')
  if (!code || !state) {
    status.value = 'error'
    error.value = '回调参数不完整（缺少 code 或 state），请重新发起飞书登录。'
    return
  }

  // ⚠️ 幂等护栏：同一个 code 只能用一次。
  //   若本页已被处理过（用户刷新了回调 URL），直接跳走 —— 不要再试一次，
  //   否则只会拿到「code 已被使用」，把本已登录的用户误导到错误页。
  if (sessionStorage.getItem(HANDLED_KEY) === code) {
    await enterWorkspace()
    return
  }

  try {
    const data = await exchangeFeishuCodeApi(code, state)
    // ⚠️ 先记「已处理」再跳转 —— 万一跳转失败，刷新页面也不会重复提交 code。
    sessionStorage.setItem(HANDLED_KEY, code)
    authStore.token = data.accessToken
    authStore.refreshToken = data.refreshToken
    localStorage.setItem('planning_access_token', data.accessToken)
    localStorage.setItem('planning_refresh_token', data.refreshToken)
    await authStore.loadMe()
    status.value = 'success'
    await enterWorkspace()
  }
  catch (err) {
    status.value = 'error'
    error.value = err instanceof Error ? err.message : '免登失败，请重试或改用账号密码登录。'
    // ⚠️ 后端会带上细粒度诊断码（exited / disabled / not_synced / ambiguous …）——
    //   据此给针对性提示，并决定「重新发起」按钮是否值得展示。
    applyReason((err as { reason?: string } | null)?.reason)
  }
})

/** 进入工作台（优先跳回原目标页）。 */
async function enterWorkspace() {
  const permissions = authStore.user?.permissions || []
  // ⚠️ 跳转意图来自 sessionStorage —— 不能靠 URL 传（见 `feishuLogin.ts` 的说明：
  //   `redirect_uri` 必须与飞书后台逐字一致，加 query 会被飞书拒绝授权）。
  const target = authorizedRedirect(consumeFeishuRedirectIntent(), permissions)
  const home = defaultWorkspacePath(permissions) || '/no-access'
  await router.replace(authStore.user?.forceChangePassword ? '/change-password' : (target || home))
}

/**
 * 重新发起飞书登录。
 *
 * ⚠️ 必须走**完整流程**（重新取授权 URL → 跳飞书），不能复用旧 code ——
 *   旧 code 已被消费，且 state 已被后端作废。
 */
async function retry() {
  sessionStorage.removeItem(HANDLED_KEY)
  error.value = ''
  status.value = 'pending'
  await startFeishuLogin()
}

function goPasswordLogin() {
  sessionStorage.removeItem(HANDLED_KEY)
  ElMessage.closeAll()
  router.replace('/login')
}

/**
 * 判断回跳目标是否可访问。
 *
 * ⚠️ 与 `Login.vue` 的同名函数保持同一套规则 —— 不能跳到未授权的页面，
 *   也不能跳回 `/login`（会造成「登录 → 又跳登录」的循环）。
 */
function authorizedRedirect(value: string, permissions: readonly string[]) {
  if (!value.startsWith('/')) return undefined
  const target = router.resolve(value)
  if (!target.matched.length) return undefined
  if (['/', '/login', '/change-password', '/no-access', '/feishu/callback'].includes(target.path)) {
    return undefined
  }
  const requiredPermission = typeof target.meta.permission === 'string' ? target.meta.permission : undefined
  return hasPermission(permissions, requiredPermission) ? target.fullPath : undefined
}
</script>

<style scoped>
.feishu-callback {
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 40px 20px;
  background: #eef3f5;
}

.feishu-callback__card {
  width: min(520px, 100%);
  padding: 43px;
  border: 1px solid var(--line);
  border-radius: 17px;
  background: #fff;
  box-shadow: 0 25px 70px rgb(19 45 65 / 11%);
}

.feishu-callback__code {
  color: var(--amber);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: .14em;
}

.feishu-callback__code--error {
  color: #d9534f;
}

.feishu-callback h1 {
  margin: 10px 0 12px;
  color: var(--navy-900);
  font-size: 30px;
  letter-spacing: 0;
}

.feishu-callback p {
  margin: 0 0 24px;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.8;
}

.feishu-callback__spinner {
  color: var(--blue);
}

.feishu-callback__hint {
  margin-top: 18px;
}

.feishu-callback__actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}
</style>
