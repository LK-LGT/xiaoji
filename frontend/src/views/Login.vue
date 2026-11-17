<template>
  <main class="login-page">
    <div class="login-aside">
      <div class="login-brand"><span class="brand-mark">成</span><strong>成果计划</strong></div>
      <div class="login-statement">
        <span class="eyebrow">PLANNING PLATFORM</span>
        <h1>让每一项计划，<br /><em>都有结果可追踪。</em></h1>
        <p>从目标拆解到成果确认，建立清晰、可验证、可复盘的工作闭环。</p>
      </div>
      <div class="login-aside-footer"><span>计划</span><i></i><span>成果</span><i></i><span>审批</span><i></i><span>台账</span></div>
    </div>
    <section class="login-panel">
      <div class="login-panel__head">
        <span class="eyebrow">SIGN IN</span>
        <h2>欢迎回来</h2>
        <p>使用企业账号或手机号进入你的工作空间</p>
      </div>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item label="账号或手机号" prop="username">
          <el-input
            v-model.trim="form.username"
            size="large"
            autocomplete="username"
            maxlength="50"
            clearable
            placeholder="请输入账号或手机号"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            size="large"
            type="password"
            autocomplete="current-password"
            maxlength="64"
            show-password
            placeholder="请输入密码"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-checkbox v-model="rememberUsername" class="login-remember">记住账号</el-checkbox>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
        <el-button class="login-button" type="primary" size="large" :loading="loading" :disabled="loading" @click="submit">进入工作空间</el-button>
      </el-form>
      <div class="login-security"><span class="status-dot"></span>企业账号安全认证 · 操作全程留痕</div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { defaultWorkspacePath, hasPermission } from '@/navigation/workspaces'
import { useAuthStore } from '@/stores/auth'
const REMEMBER_USERNAME_KEY = 'planning_login_username_memory'
/**
 * ⚠️ BUG-R6-32（安全）：旧实现往 localStorage 写的是
 *     ``{ username, password, expiresAt }`` —— **明文密码存 7 天**。
 *
 * 危害：
 * - localStorage 对同源 JS **完全可读** → 任何 XSS / 第三方脚本 / 浏览器插件
 *   都能直接拿到用户密码（不是哈希，是可直接登录的明文）；
 * - 密码还会出现在浏览器磁盘快照、devtools、以及用户主动「导出站点数据」里；
 * - 用户在公用电脑上退出登录后，密码**仍然留在磁盘上**。
 *
 * 现在：**只记用户名**，密码永远不落盘。
 * （密码由后端 PBKDF2 哈希存储，前端不做任何持久化。）
 *
 * ⚠️ 迁移：老版本已经在用户浏览器里写过明文密码，
 * 必须在升级后**主动清掉**，否则风险窗口一直开着。
 */
const REMEMBER_USERNAME_TTL = 30 * 24 * 60 * 60 * 1000
/** 旧版存明文密码的 key —— 读取后立即删除，绝不再沿用。 */
const LEGACY_PASSWORD_MEMORY_KEY = 'planning_login_password_memory'
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const loading = ref(false)
const error = ref('')
const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const rememberUsername = ref(false)
const rules: FormRules<typeof form> = {
  username: [
    { required: true, message: '请输入账号或手机号', trigger: 'blur' },
    { min: 2, max: 50, message: '账号或手机号长度为 2-50 个字符', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (/\s/.test(value)) {
          callback(new Error('账号或手机号不能包含空格'))
          return
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { max: 64, message: '密码长度不能超过 64 个字符', trigger: 'blur' },
  ],
}

onMounted(loadRememberedUsername)

async function submit() {
  if (loading.value) return
  error.value = ''
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await authStore.login(form.username, form.password)
    persistRememberedUsername()
    ElMessage.closeAll()
    ElMessage.success('登录成功')
    const permissions = authStore.user?.permissions || []
    const defaultPath = defaultWorkspacePath(permissions) || '/no-access'
    const redirect = authorizedRedirect(route.query.redirect, permissions)
    await router.replace(authStore.user?.forceChangePassword ? '/change-password' : (redirect || defaultPath))
  }
  catch (err) { error.value = err instanceof Error ? err.message : '登录失败，请检查账号或密码' }
  finally { loading.value = false }
}

/**
 * 清除旧版遗留的**明文密码**记录（BUG-R6-32 迁移）。
 *
 * ⚠️ 必须无条件执行 —— 只要发现老 key，就说明这台机器的浏览器里
 * 还躺着用户的明文密码，不清掉等于风险一直在。
 */
function purgeLegacyPasswordMemory() {
  try {
    localStorage.removeItem(LEGACY_PASSWORD_MEMORY_KEY)
  } catch {
    // localStorage 不可用（隐私模式 / 禁用）时忽略 —— 不影响登录。
  }
}

function loadRememberedUsername() {
  purgeLegacyPasswordMemory()
  try {
    const saved = JSON.parse(localStorage.getItem(REMEMBER_USERNAME_KEY) || 'null')
    if (!saved || typeof saved !== 'object' || typeof saved.expiresAt !== 'number' || saved.expiresAt <= Date.now()) {
      clearRememberedUsername()
      return
    }
    if (typeof saved.username !== 'string' || !saved.username) {
      clearRememberedUsername()
      return
    }
    // ⚠️ 只回填用户名 —— 密码框保持为空，用户必须自己输入。
    form.username = saved.username
    form.password = ''
    rememberUsername.value = true
  } catch {
    clearRememberedUsername()
  }
}

function persistRememberedUsername() {
  if (!rememberUsername.value) {
    clearRememberedUsername()
    return
  }
  try {
    localStorage.setItem(REMEMBER_USERNAME_KEY, JSON.stringify({
      username: form.username,
      expiresAt: Date.now() + REMEMBER_USERNAME_TTL,
    }))
  } catch {
    // 存储不可用时静默失败 —— 记住用户名是锦上添花，不该拦住登录。
  }
}

function clearRememberedUsername() {
  try {
    localStorage.removeItem(REMEMBER_USERNAME_KEY)
  } catch {
    // 同上 —— 清不掉的极端情况不影响主流程。
  }
}

function authorizedRedirect(value: unknown, permissions: readonly string[]) {
  if (typeof value !== 'string' || !value.startsWith('/')) return undefined
  const target = router.resolve(value)
  if (!target.matched.length || ['/', '/login', '/change-password', '/no-access'].includes(target.path)) return undefined
  const requiredPermission = typeof target.meta.permission === 'string' ? target.meta.permission : undefined
  return hasPermission(permissions, requiredPermission) ? target.fullPath : undefined
}
</script>
