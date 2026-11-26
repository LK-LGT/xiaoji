<template>
  <section class="page-panel employee-register-page" v-loading="loading">
    <div class="page-header">
      <div>
        <span class="eyebrow">EMPLOYEE ACCOUNT</span>
        <h1 class="page-title">员工注册</h1>
        <p class="page-subtitle">管理员直接创建员工账号。系统自动生成8位数字账号，默认密码为123456。</p>
      </div>
      <el-button @click="router.push('/system/employees')">进入员工管理</el-button>
    </div>

    <div class="register-layout">
      <section class="section-card register-form-panel">
        <div class="section-header"><div><h2>注册员工</h2><p>填写员工身份、组织归属和系统角色。</p></div></div>
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
          <div class="form-grid two-columns">
            <el-form-item label="姓名" prop="realName"><el-input v-model="form.realName" maxlength="80" placeholder="请输入员工姓名" /></el-form-item>
            <el-form-item label="手机号" prop="mobile"><el-input v-model="form.mobile" maxlength="11" placeholder="请输入11位手机号" /></el-form-item>
            <el-form-item label="岗位"><el-input v-model="form.positionName" maxlength="80" placeholder="请输入岗位名称（选填）" /></el-form-item>
            <el-form-item label="主部门/小组" prop="deptId">
              <el-tree-select v-model="form.deptId" :data="departmentOptions" check-strictly :render-after-expand="false" placeholder="请选择归属组织" />
            </el-form-item>
            <el-form-item label="直属负责人（选填）" prop="directLeaderId">
              <el-select v-model="form.directLeaderId" clearable filterable placeholder="请选择直属负责人">
                <el-option v-for="leader in options.leaders" :key="leader.id" :label="leader.label" :value="leader.id"><span>{{ leader.label }}</span><small class="option-secondary">{{ leader.secondary }}</small></el-option>
              </el-select>
            </el-form-item>
            <el-form-item class="span-two" label="角色" prop="roleId">
              <el-select v-model="form.roleId" placeholder="请选择角色">
                <el-option v-for="role in options.roles" :key="role.id" :label="role.name" :value="String(role.id)" :disabled="role.status !== 1" />
              </el-select>
            </el-form-item>
          </div>
          <div class="form-actions"><el-button @click="reset">清空</el-button><el-button type="primary" :loading="saving" @click="submit">完成注册</el-button></div>
        </el-form>
      </section>

      <aside class="register-result-panel">
        <template v-if="result">
          <div class="result-success-mark">✓</div>
          <span>注册成功</span>
          <h2>{{ result.realName }}</h2>
          <dl>
            <div><dt>登录账号</dt><dd>{{ result.username }} <el-button text type="primary" @click="copy(result.username)">复制</el-button></dd></div>
            <div><dt>默认密码</dt><dd>{{ result.initialPassword }} <el-button text type="primary" @click="copy(result.initialPassword)">复制</el-button></dd></div>
            <div><dt>员工编号</dt><dd>{{ result.employeeNo }}</dd></div>
            <div><dt>岗位</dt><dd>{{ result.positionName || '未填写' }}</dd></div>
            <div><dt>归属组织</dt><dd>{{ result.departmentName }}</dd></div>
            <div><dt>直属负责人</dt><dd>{{ result.directLeaderName || '未配置' }}</dd></div>
          </dl>
          <el-alert title="员工首次登录后必须修改初始密码" type="warning" :closable="false" show-icon />
        </template>
        <template v-else>
          <div class="register-empty-mark">8</div>
          <h2>数字账号自动生成</h2>
          <p>注册成功后，这里会显示账号、初始密码和员工编号。管理员自行将登录信息告知员工。</p>
        </template>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useRouter } from 'vue-router'
import { getSystemOptions, registerEmployee, type RegistrationResult, type SystemOptions } from '@/api/system'

const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const saving = ref(false)
const result = ref<RegistrationResult>()
const options = reactive<SystemOptions>({ departments: [], leaders: [], roles: [] })
const form = reactive<{ realName: string; mobile: string; positionName: string; deptId?: string; directLeaderId?: string; roleId?: string }>({ realName: '', mobile: '', positionName: '' })
const rules: FormRules = {
  realName: [{ required: true, message: '请输入员工姓名', trigger: 'blur' }],
  mobile: [{ required: true, pattern: /^1\d{10}$/, message: '请输入正确的11位手机号', trigger: 'blur' }],
  deptId: [{ required: true, message: '请选择主部门或小组', trigger: 'change' }],
  roleId: [{ required: true, message: '请选择角色', trigger: 'change' }],
}
const departmentOptions = computed(() => mapDepartments(options.departments))

function mapDepartments(nodes: SystemOptions['departments']): Array<{ value: string; label: string; disabled: boolean; children: ReturnType<typeof mapDepartments> }> {
  return nodes.map((node) => ({ value: node.id, label: node.name, disabled: node.status !== 1, children: mapDepartments(node.children || []) }))
}
async function load() {
  loading.value = true
  try {
    Object.assign(options, await getSystemOptions())
    if (!form.roleId) form.roleId = defaultRoleId()
  }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '基础选项加载失败') }
  finally { loading.value = false }
}
async function submit() {
  if (!await formRef.value?.validate()) return
  saving.value = true
  try {
    result.value = await registerEmployee({ realName: form.realName, mobile: form.mobile, positionName: form.positionName, deptId: form.deptId!, directLeaderId: form.directLeaderId, roleId: form.roleId! })
    ElMessage.success('员工账号已创建')
    reset(false)
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '员工注册失败') }
  finally { saving.value = false }
}
function reset(clearResult = true) {
  form.realName = ''; form.mobile = ''; form.positionName = ''; form.deptId = undefined; form.directLeaderId = undefined; form.roleId = defaultRoleId()
  formRef.value?.clearValidate()
  if (clearResult) result.value = undefined
}
function defaultRoleId() {
  // ⚠️ **匹配规范角色码 `employee`**（不是历史大写自造码 `EMPLOYEE`）——
  // 后端 `/system/options` 的角色候选现在 `id` 与 `code` 都是规范码
  // （见 `backend/app/services/role_vocabulary.py`）。
  const employeeRole = options.roles.find((role) => role.code === 'employee' && role.status === 1)
  // 角色主键是字符串角色码（不是自增数字），不能转数字。
  return employeeRole ? employeeRole.id : undefined
}
async function copy(value: string) {
  await navigator.clipboard.writeText(value)
  ElMessage.success('已复制')
}
onMounted(load)
</script>
