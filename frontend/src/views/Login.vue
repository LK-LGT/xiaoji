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
        <el-checkbox v-model="rememberPassword" class="login-remember">记住密码</el-checkbox>
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
const REMEMBER_PASSWORD_KEY = 'planning_login_password_memory'
const REMEMBER_PASSWORD_TTL = 7 * 24 * 60 * 60 * 1000
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const loading = ref(false)
const error = ref('')
const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const rememberPassword = ref(false)
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

onMounted(loadRememberedPassword)

async function submit() {
  if (loading.value) return
  error.value = ''
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await authStore.login(form.username, form.password)
    persistRememberedPassword()
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

function loadRememberedPassword() {
  try {
    const saved = JSON.parse(localStorage.getItem(REMEMBER_PASSWORD_KEY) || 'null')
    if (!saved || typeof saved !== 'object' || typeof saved.expiresAt !== 'number' || saved.expiresAt <= Date.now()) {
      clearRememberedPassword()
      return
    }
    if (typeof saved.username !== 'string' || typeof saved.password !== 'string') {
      clearRememberedPassword()
      return
    }
    form.username = saved.username
    form.password = saved.password
    rememberPassword.value = true
  } catch {
    clearRememberedPassword()
  }
}

function persistRememberedPassword() {
  if (!rememberPassword.value) {
    clearRememberedPassword()
    return
  }
  localStorage.setItem(REMEMBER_PASSWORD_KEY, JSON.stringify({
    username: form.username,
    password: form.password,
    expiresAt: Date.now() + REMEMBER_PASSWORD_TTL,
  }))
}

function clearRememberedPassword() {
  localStorage.removeItem(REMEMBER_PASSWORD_KEY)
}

function authorizedRedirect(value: unknown, permissions: readonly string[]) {
  if (typeof value !== 'string' || !value.startsWith('/')) return undefined
  const target = router.resolve(value)
  if (!target.matched.length || ['/', '/login', '/change-password', '/no-access'].includes(target.path)) return undefined
  const requiredPermission = typeof target.meta.permission === 'string' ? target.meta.permission : undefined
  return hasPermission(permissions, requiredPermission) ? target.fullPath : undefined
}
</script>
