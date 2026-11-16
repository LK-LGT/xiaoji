<template>
  <main class="password-change-page">
    <section class="password-change-panel">
      <div class="password-change-head"><span class="brand-mark">成</span><div><span class="eyebrow">ACCOUNT SECURITY</span><h1>首次登录，请修改密码</h1><p>当前账号使用统一初始密码。完成修改后才能进入业务工作台。</p></div></div>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item label="初始密码" prop="oldPassword"><el-input v-model="form.oldPassword" type="password" show-password autocomplete="current-password" /></el-form-item>
        <el-form-item label="新密码" prop="newPassword"><el-input v-model="form.newPassword" type="password" show-password autocomplete="new-password" placeholder="至少8位，建议包含字母和数字" /></el-form-item>
        <el-form-item label="确认新密码" prop="confirmPassword"><el-input v-model="form.confirmPassword" type="password" show-password autocomplete="new-password" @keyup.enter="submit" /></el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
        <el-button class="password-change-button" type="primary" size="large" :loading="loading" @click="submit">修改密码并重新登录</el-button>
      </el-form>
      <div class="password-rules"><strong>密码要求</strong><span>8至32位，不能与初始密码相同；请勿与他人共享。</span></div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter(), authStore = useAuthStore(), formRef = ref<FormInstance>(), loading = ref(false), error = ref('')
// ⚠️ BUG-R6-39：这里原本硬编码 `oldPassword: '123456'`。
// 后果：改密页一打开就**预填了统一弱口令**，
// ① 用户很容易直接点提交 → 旧密码填错 → 401；
// ② 更隐蔽的是——用户手动选中"初始密码"框输入时，
//    文本会**追加**在 '123456' 后面（'123456LiKai@2026'），
//    于是「旧密码不正确」，用户完全不知道是哪里错了。
//
// R4 之后弱口令早已被哈希覆盖，'123456' 对绝大多数账号**根本不是**当前口令，
// 这个默认值只会误导。现在留空，强制用户自己输入当前口令。
const form = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })
const rules: FormRules = {
  oldPassword: [{ required: true, message: '请输入初始密码' }],
  newPassword: [{ required: true, min: 8, max: 32, message: '新密码长度为8至32位' }, { validator: (_rule, value, callback) => value === form.oldPassword ? callback(new Error('新密码不能与初始密码相同')) : callback() }],
  confirmPassword: [{ required: true, message: '请再次输入新密码' }, { validator: (_rule, value, callback) => value !== form.newPassword ? callback(new Error('两次输入的密码不一致')) : callback() }],
}
async function submit() {
  if (!await formRef.value?.validate()) return
  loading.value = true; error.value = ''
  try { await authStore.changePassword(form.oldPassword, form.newPassword); ElMessage.success('密码修改成功，请重新登录'); await router.replace('/login') }
  catch (err) { error.value = err instanceof Error ? err.message : '密码修改失败' }
  finally { loading.value = false }
}
</script>
