<template>
  <el-dialog
    v-model="visible"
    class="profile-password-dialog"
    title="修改密码"
    width="480px"
    :close-on-click-modal="!submitting"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    @closed="reset"
  >
    <p class="profile-password-dialog__hint">修改后当前登录会失效，请使用新密码重新登录。</p>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
      <el-form-item label="当前密码" prop="currentPassword">
        <el-input
          v-model="form.currentPassword"
          type="password"
          show-password
          autocomplete="current-password"
        />
      </el-form-item>
      <el-form-item label="新密码" prop="newPassword">
        <el-input
          v-model="form.newPassword"
          type="password"
          show-password
          autocomplete="new-password"
          placeholder="8至32位，不能与当前密码相同"
        />
      </el-form-item>
      <el-form-item label="确认新密码" prop="confirmPassword">
        <el-input
          v-model="form.confirmPassword"
          type="password"
          show-password
          autocomplete="new-password"
          @keyup.enter="submit"
        />
      </el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    </el-form>
    <template #footer>
      <el-button :disabled="submitting" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">修改密码</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'

defineProps<{
  submitting: boolean
  error: string
}>()

const emit = defineEmits<{
  submit: [currentPassword: string, newPassword: string]
  closed: []
}>()

const visible = defineModel<boolean>({ required: true })
const formRef = ref<FormInstance>()
const form = reactive({ currentPassword: '', newPassword: '', confirmPassword: '' })
const rules: FormRules = {
  currentPassword: [{ required: true, message: '请输入当前密码' }],
  newPassword: [
    { required: true, min: 8, max: 32, message: '新密码长度为8至32位' },
    {
      validator: (_rule, value, callback) => value === form.currentPassword
        ? callback(new Error('新密码不能与当前密码相同'))
        : callback(),
    },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入新密码' },
    {
      validator: (_rule, value, callback) => value !== form.newPassword
        ? callback(new Error('两次输入的密码不一致'))
        : callback(),
    },
  ],
}

async function submit() {
  if (!await formRef.value?.validate()) return
  emit('submit', form.currentPassword, form.newPassword)
}

function reset() {
  form.currentPassword = ''
  form.newPassword = ''
  form.confirmPassword = ''
  formRef.value?.clearValidate()
  emit('closed')
}
</script>
