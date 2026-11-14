<template>
  <main class="page-panel profile-page">
    <section class="profile-hero" aria-labelledby="profile-title">
      <span class="profile-avatar" aria-hidden="true">{{ initial }}</span>
      <div class="profile-identity">
        <span class="eyebrow">PERSONAL ACCOUNT</span>
        <h1 id="profile-title">{{ user?.realName || '当前用户' }}</h1>
        <p>{{ user?.username || '未提供账号' }}</p>
        <div class="profile-role-list" aria-label="当前角色">
          <el-tag v-for="role in displayedRoles" :key="role" effect="plain">{{ role }}</el-tag>
        </div>
      </div>
      <span :class="['profile-account-state', accountStatus.className]"><i></i>{{ accountStatus.label }}</span>
    </section>

    <div class="profile-content">
      <section class="profile-section" aria-labelledby="basic-info-title">
        <header class="profile-section__header">
          <div>
            <span>PROFILE</span>
            <h2 id="basic-info-title">基本信息</h2>
          </div>
        </header>
        <dl class="profile-details">
          <div>
            <dt>姓名</dt>
            <dd>{{ user?.realName || '未填写' }}</dd>
          </div>
          <div>
            <dt>登录账号</dt>
            <dd class="profile-data-value">{{ user?.username || '未填写' }}</dd>
          </div>
          <div>
            <dt>手机号</dt>
            <dd class="profile-data-value">{{ user?.mobile || '未填写' }}</dd>
          </div>
          <div>
            <dt>角色</dt>
            <dd>{{ displayedRoles.join('、') }}</dd>
          </div>
          <div>
            <dt>所属组织</dt>
            <dd>{{ user?.departmentName || '未分配组织' }}</dd>
          </div>
          <div>
            <dt>最后登录</dt>
            <dd class="profile-data-value">{{ formatDateTime(user?.lastLoginAt) }}</dd>
          </div>
          <div>
            <dt>账号状态来源</dt>
            <dd>{{ accountStatusSource }}</dd>
          </div>
          <div>
            <dt>可用工作空间</dt>
            <dd>{{ displayedWorkspaces.join('、') }}</dd>
          </div>
        </dl>
      </section>

      <section class="profile-section profile-security" aria-labelledby="account-security-title">
        <header class="profile-section__header">
          <div>
            <span>SECURITY</span>
            <h2 id="account-security-title">账号安全</h2>
          </div>
        </header>
        <div class="profile-security__body">
          <div class="profile-security__copy">
            <span class="profile-security__icon"><el-icon><Lock /></el-icon></span>
            <div>
              <strong>登录密码</strong>
              <p>修改后需要使用新密码重新登录。</p>
            </div>
          </div>
          <el-button :icon="Lock" type="primary" plain @click="openPasswordDialog">修改密码</el-button>
        </div>
      </section>
    </div>

    <ChangePasswordDialog
      v-model="passwordDialogVisible"
      :submitting="passwordSubmitting"
      :error="passwordError"
      @submit="changePassword"
      @closed="passwordError = ''"
    />
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import ChangePasswordDialog from './ChangePasswordDialog.vue'
import { roleLabels, workspaceLabels } from './profilePresentation'

const router = useRouter()
const authStore = useAuthStore()
const passwordDialogVisible = ref(false)
const passwordSubmitting = ref(false)
const passwordError = ref('')

const user = computed(() => authStore.user)
const initial = computed(() => (user.value?.realName || '用').slice(0, 1))
const displayedRoles = computed(() => roleLabels(user.value?.roles || []))
const displayedWorkspaces = computed(() => workspaceLabels(user.value?.permissions || []))
const accountStatus = computed(() => user.value?.status === 0
  ? { label: '账号停用', className: 'is-disabled' }
  : { label: '账号正常', className: '' })
const accountStatusSource = computed(() => {
  const source = user.value?.statusSource || '系统员工管理'
  return `${source} · ${accountStatus.value.label}`
})

function formatDateTime(value?: string) {
  return value ? value.replace('T', ' ').slice(0, 19) : '尚未登录'
}

function openPasswordDialog() {
  passwordError.value = ''
  passwordDialogVisible.value = true
}

async function changePassword(currentPassword: string, newPassword: string) {
  passwordSubmitting.value = true
  passwordError.value = ''
  try {
    await authStore.changePassword(currentPassword, newPassword)
    passwordDialogVisible.value = false
    ElMessage.success('密码修改成功，请重新登录')
    await router.replace('/login')
  } catch (error) {
    passwordError.value = error instanceof Error ? error.message : '密码修改失败，请重试'
  } finally {
    passwordSubmitting.value = false
  }
}
</script>
