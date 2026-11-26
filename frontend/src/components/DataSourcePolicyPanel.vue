/**
 * P1-5 数据源白名单配置面板（仅 HR）。
 *
 * ⚠️ 这个面板配的是**安全边界**，不是普通业务配置。
 * 所以呈现上必须把「默认不信任」的语义讲清楚：
 * - 未在列表里的数据源**不可读、也不进算分集**；
 * - 「允许进入算分集」与「哪些角色能读」是两件独立的事；
 * - 脱敏字段是读出时打码，不是删字段。
 */
<template>
  <section class="datasource-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">安全配置</span>
        <h3 class="panel-title">数据源白名单</h3>
        <p class="panel-subtitle">
          默认不信任 —— 未在此列出的数据源既不可读，也不会进入算分集
        </p>
      </div>
    </header>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="漏配的后果是「数据没进来」，不是「数据泄露」"
      description="未配置 = 不可读。想让某个源进入系统，必须在这里显式放行。"
      class="safety-note"
    />

    <el-table :data="policies" stripe style="width: 100%">
      <el-table-column prop="sourceCode" label="数据源" width="140" />
      <el-table-column prop="displayName" label="显示名" min-width="120" />
      <el-table-column label="可读角色" min-width="200">
        <template #default="{ row }">
          <el-tag
            v-for="role in row.allowedRoles"
            :key="role"
            size="small"
            effect="plain"
            class="role-tag"
          >
            {{ roleLabel(role) }}
          </el-tag>
          <span v-if="!row.allowedRoles.length" class="muted">（仅 HR）</span>
        </template>
      </el-table-column>
      <el-table-column label="脱敏字段" min-width="140">
        <template #default="{ row }">
          <el-tag
            v-for="field in row.redactedFields"
            :key="field"
            size="small"
            type="info"
            effect="plain"
            class="role-tag"
          >
            {{ field }}
          </el-tag>
          <span v-if="!row.redactedFields.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.configured ? 'success' : 'info'" effect="plain">
            {{ row.configured ? '已配置' : '默认值' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="edit(row)">
            配置
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 配置对话框 -->
    <el-dialog v-model="showEdit" title="配置数据源策略" width="min(520px, calc(100vw - 32px))">
      <el-form label-width="110px">
        <el-form-item label="数据源标识">
          <el-input v-model="form.source_code" :disabled="Boolean(editingSource)" />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.display_name" placeholder="考勤系统" />
        </el-form-item>
        <el-form-item label="进入算分集">
          <el-switch v-model="form.allowed" />
          <span class="field-hint">
            关闭后该源只能作参考，不会参与算分
          </span>
        </el-form-item>
        <el-form-item label="可读角色">
          <el-select
            v-model="form.allowed_roles"
            multiple
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            placeholder="选择角色"
          >
            <el-option v-for="role in ROLES" :key="role" :label="roleLabel(role)" :value="role" />
          </el-select>
          <span class="field-hint">HR 始终可读任何源，无需在此配置</span>
        </el-form-item>
        <el-form-item label="脱敏字段">
          <el-select
            v-model="form.redacted_fields"
            multiple
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            placeholder="mobile, email"
          />
          <span class="field-hint">读出时替换为 ***，不是删除字段</span>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.note" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { dataSourceApi, type DataSourcePolicy } from '@/api/p2'
import { roleLabel } from '@/views/profile/profilePresentation'

/**
 * 数据源策略允许的角色清单。
 *
 * ⚠️ **注意**：这里**不含** `admin` ——
 * 「系统管理员」管的是系统配置与同步，**不自动获得业务数据访问权**
 * （见 `hr_admin_roles.py`）。把 admin 加进来等于给配置权限的人
 * 开了业务数据后门。
 */
const ROLES = ['hr', 'manager', 'department_leader', 'employee']

const policies = ref<DataSourcePolicy[]>([])
const loading = ref(false)
const saving = ref(false)
const showEdit = ref(false)
const editingSource = ref('')

const form = reactive({
  source_code: '',
  display_name: '',
  allowed: false,
  allowed_roles: ['hr'] as string[],
  redacted_fields: [] as string[],
  note: '',
})

/**
 * 角色码 → 中文标签。
 *
 * ⚠️ 复用 `profilePresentation.ts` 的**单一权威映射**，
 * 不在本文件另存一份 —— 两份 map 必然漂移（历史上就漂移过：
 * 一份用 `SUPER_ADMIN` 自造码，一份用后端真实码）。
 */

async function load() {
  loading.value = true
  try {
    policies.value = await dataSourceApi.list()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
    policies.value = []
  } finally {
    loading.value = false
  }
}

function edit(row: DataSourcePolicy) {
  editingSource.value = row.sourceCode
  form.source_code = row.sourceCode
  form.display_name = row.displayName
  form.allowed = row.allowed
  form.allowed_roles = [...row.allowedRoles]
  form.redacted_fields = [...row.redactedFields]
  form.note = row.note
  showEdit.value = true
}

async function save() {
  if (!form.source_code) {
    ElMessage.warning('数据源标识不能为空')
    return
  }
  saving.value = true
  try {
    await dataSourceApi.upsert({
      source_code: form.source_code,
      display_name: form.display_name,
      allowed: form.allowed,
      allowed_roles: form.allowed_roles.join(','),
      redacted_fields: form.redacted_fields.join(','),
      note: form.note,
    })
    ElMessage.success('已保存')
    showEdit.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.datasource-panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
}

.panel-head {
  margin-bottom: 12px;
}

.eyebrow {
  display: block;
  font-size: 11px;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 4px 0;
  color: #303133;
}

.panel-subtitle {
  font-size: 13px;
  color: #606266;
  margin: 0;
}

.safety-note {
  margin-bottom: 14px;
}

.role-tag {
  margin-right: 4px;
}

.muted {
  color: #c0c4cc;
  font-size: 12px;
}

.field-hint {
  display: block;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
  margin-top: 2px;
}
</style>
