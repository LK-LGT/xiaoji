<template>
  <section class="page-panel" v-loading="loading">
    <div class="page-header">
      <div>
        <span class="eyebrow">MODULE ENTITLEMENT</span>
        <h1 class="page-title">部门功能板块</h1>
        <p class="page-subtitle">
          控制「哪个部门有哪些业务板块」。板块与角色权限是**两个独立维度**：
          权限回答「这个人能不能干」，板块回答「这个部门有没有这个业务」。
        </p>
      </div>
      <el-button type="primary" :loading="saving" @click="openGrant">开通板块</el-button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="默认关闭：没有配置行的部门看不到该板块，接口也会返回 403。"
      description="板块开通向下继承 —— 上级部门开通后，其所有子部门自动获得该板块。"
      style="margin-bottom: 16px"
    />

    <div class="section-card">
      <div class="section-header">
        <div><h2>已开通板块</h2><p>共 {{ grants.length }} 条配置。</p></div>
      </div>
      <el-table :data="grants" stripe empty-text="还没有任何部门开通板块">
        <el-table-column prop="departmentName" label="部门" min-width="180" />
        <el-table-column label="板块" min-width="160">
          <template #default="{ row }">{{ moduleLabel(row.moduleCode) }}</template>
        </el-table-column>
        <el-table-column prop="moduleCode" label="板块编码" min-width="140" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.active ? 'success' : 'info'" effect="plain">
              {{ row.active ? '已开通' : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="200" show-overflow-tooltip />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="toggle(row)">
              {{ row.active ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" title="开通部门功能板块" width="480px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="部门" required>
          <el-select v-model="form.department_id" filterable placeholder="选择部门" style="width: 100%">
            <el-option
              v-for="dept in deptOptions"
              :key="dept.id"
              :label="dept.label"
              :value="dept.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="板块" required>
          <el-select v-model="form.module_code" placeholder="选择板块" style="width: 100%">
            <el-option
              v-for="mod in knownModules"
              :key="mod.code"
              :label="mod.label"
              :value="mod.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.active" active-text="开通" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" maxlength="255" placeholder="例如：短视频部开通剪辑工作量" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- ★ 岗位口径（2026-11-23）—— 板块开了 ≠ 全部门都要收推送 -->
    <el-divider content-position="left">
      <span class="divider-text">部门 × 岗位 考核口径</span>
    </el-divider>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="板块开通 ≠ 全部门都要收推送。"
      description="同一部门里不同岗位的考核方式不一样 —— 这里配「谁要填报、谁只审、谁不参与」，以及各自填哪些指标。没配的岗位默认不参与。"
      style="margin-bottom: 16px"
    />

    <div class="section-card">
      <div class="section-header">
        <div>
          <h2>岗位口径</h2>
          <p>先选部门，再配置该部门每个岗位的角色。</p>
        </div>
        <el-select
          v-model="policyDeptId"
          filterable
          placeholder="选择部门"
          style="width: 260px"
          @change="loadPositions"
        >
          <el-option
            v-for="dept in deptOptions"
            :key="dept.id"
            :label="dept.label"
            :value="dept.id"
          />
        </el-select>
      </div>

      <el-table
        v-if="policyDeptId"
        :data="positions"
        stripe
        empty-text="该部门还没有在职员工"
      >
        <el-table-column prop="position" label="岗位" min-width="140" />
        <el-table-column label="在职人数" width="100" align="center">
          <template #default="{ row }">{{ row.headcount }}</template>
        </el-table-column>
        <el-table-column label="角色" min-width="150">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.role)" effect="plain">
              {{ WORKLOAD_ROLE_LABELS[row.role as WorkloadRole] }}
            </el-tag>
            <el-tag v-if="!row.configured" type="info" effect="plain" style="margin-left: 6px">
              未配置
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="填报指标" min-width="200">
          <template #default="{ row }">
            <span v-if="row.role !== 'none'">
              {{ formatMetricNames(effectiveMetricCodes(row)) }}
            </span>
            <span v-else style="color: var(--el-text-color-secondary)">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openPolicy(row)">配置</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-else description="请先选择部门" />
    </div>

    <el-dialog v-model="policyDialogVisible" title="配置岗位考核口径" width="520px">
      <el-form :model="policyForm" label-width="110px">
        <el-form-item label="部门">
          <el-input :model-value="policyDeptName" disabled />
        </el-form-item>
        <el-form-item label="岗位">
          <el-input :model-value="policyForm.position" disabled />
        </el-form-item>
        <el-form-item label="角色" required>
          <el-radio-group v-model="policyForm.role">
            <el-radio value="reporter">要填报（收到表格 + 表单）</el-radio>
            <el-radio value="reviewer">审阅（收到汇总卡 + 效果指标表单）</el-radio>
            <el-radio value="none">不参与（不收任何推送）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="policyForm.role !== 'none'" label="填报指标">
          <el-input
            v-model="policyForm.metric_codes"
            maxlength="255"
            :placeholder="
              policyForm.role === 'reporter'
                ? '留空 = 用角色默认（普通视频、OOTD、智能体 —— 条数）'
                : '留空 = 用角色默认（完播率、视频留存率 —— 比率）'
            "
          />
          <div class="field-hint">
            多个指标用英文逗号分隔。
            <template v-if="policyForm.role === 'reporter'">
              数量类：<code>video_normal_count,video_ootd_count,video_agent_count</code>
            </template>
            <template v-else>
              比率类：<code>video_completion_rate,video_retention_rate</code>
            </template>
          </div>
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="policyForm.active" active-text="生效" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="policyForm.note" maxlength="255" placeholder="配置依据" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="policyDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePolicy">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  departmentModulesApi,
  WORKLOAD_DEFAULT_METRICS,
  WORKLOAD_METRIC_LABELS,
  WORKLOAD_ROLE_LABELS,
  type DepartmentModuleGrant,
  type DepartmentPosition,
  type KnownModule,
  type WorkloadRole,
} from '@/api/departmentModules'
import { flattenDepartments, getDepartments, type DeptNode } from '@/api/system'

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const grants = ref<DepartmentModuleGrant[]>([])
const knownModules = ref<KnownModule[]>([])
const departments = ref<DeptNode[]>([])

// ── 岗位口径（2026-11-23）──────────────────────────────────────────
const policyDeptId = ref('')
const positions = ref<DepartmentPosition[]>([])
const policyDialogVisible = ref(false)
const policyForm = reactive({
  position: '',
  role: 'none' as WorkloadRole,
  metric_codes: '',
  active: true,
  note: '',
})

const form = reactive({
  department_id: '',
  module_code: '',
  active: true,
  note: '',
})

/** 部门下拉（带缩进，父级在前）。 */
const deptOptions = computed(() =>
  flattenDepartments(departments.value).map((dept) => ({
    id: dept.id,
    label: `${'　'.repeat(dept.depth)}${dept.name}`,
  })),
)

const policyDeptName = computed(
  () => deptOptions.value.find((d) => d.id === policyDeptId.value)?.label?.trim() || '',
)

function moduleLabel(code: string) {
  return knownModules.value.find((mod) => mod.code === code)?.label || code
}

function roleTagType(role: WorkloadRole): 'success' | 'warning' | 'info' {
  if (role === 'reporter') return 'success'
  if (role === 'reviewer') return 'warning'
  return 'info'
}

function formatMetrics(metrics: DepartmentPosition['metricCodes']): string {
  if (!metrics) return '（板块默认）'
  if (Array.isArray(metrics)) {
    return metrics.length ? metrics.join('、') : '（板块默认）'
  }
  return metrics.trim() ? metrics : '（板块默认）'
}

/** 指标码数组 → 中文名文本。 */
function formatMetricNames(codes: string[]): string {
  if (!codes.length) return '（无）'
  return codes.map((code) => WORKLOAD_METRIC_LABELS[code] ?? code).join('、')
}

/**
 * ★ 该岗位**实际**要填的指标（2026-11-23 角色倒转）。
 *
 * ⚠️ **为什么要算「实际」而不是直接显示配置值**
 * ----------------------------------------------
 * 口径表里 `metricCodes` 留空 = **按角色用默认**：
 * - reporter（剪辑）→ 普通视频 / OOTD / 智能体（**条数**）
 * - reviewer（主管）→ 完播率 / 视频留存率（**比率**）
 *
 * ⇒ 直接显示空值 HR 不知道该岗位到底填什么 ⇒ 这里算出来。
 */
function effectiveMetricCodes(row: DepartmentPosition): string[] {
  const configured =
    typeof row.metricCodes === 'string'
      ? row.metricCodes.split(',').map((s) => s.trim()).filter(Boolean)
      : (row.metricCodes ?? [])
  if (configured.length) return configured
  if (row.role === 'reporter') return WORKLOAD_DEFAULT_METRICS.reporter
  if (row.role === 'reviewer') return WORKLOAD_DEFAULT_METRICS.reviewer
  return []
}

function openGrant() {
  Object.assign(form, { department_id: '', module_code: '', active: true, note: '' })
  dialogVisible.value = true
}

async function load() {
  loading.value = true
  try {
    const [known, list, tree] = await Promise.all([
      departmentModulesApi.getKnown(),
      departmentModulesApi.getGrants(),
      getDepartments(),
    ])
    knownModules.value = known.items
    grants.value = list.items
    departments.value = tree
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '板块配置加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.department_id) {
    ElMessage.warning('请选择部门')
    return
  }
  if (!form.module_code) {
    ElMessage.warning('请选择板块')
    return
  }
  saving.value = true
  try {
    await departmentModulesApi.grant({ ...form })
    ElMessage.success('板块配置已保存')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggle(row: DepartmentModuleGrant) {
  saving.value = true
  try {
    await departmentModulesApi.grant({
      department_id: row.departmentId,
      module_code: row.moduleCode,
      active: !row.active,
      note: row.note,
    })
    ElMessage.success(row.active ? '板块已停用' : '板块已启用')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作失败')
  } finally {
    saving.value = false
  }
}

// ── 岗位口径 ──────────────────────────────────────────────────────

async function loadPositions() {
  if (!policyDeptId.value) {
    positions.value = []
    return
  }
  try {
    const res = await departmentModulesApi.getPositions(policyDeptId.value)
    positions.value = res.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '岗位口径加载失败')
  }
}

function openPolicy(row: DepartmentPosition) {
  Object.assign(policyForm, {
    position: row.position,
    role: row.role,
    metric_codes:
      typeof row.metricCodes === 'string' ? row.metricCodes : (row.metricCodes || []).join(','),
    active: true,
    note: '',
  })
  policyDialogVisible.value = true
}

async function savePolicy() {
  saving.value = true
  try {
    await departmentModulesApi.savePolicy({
      department_id: policyDeptId.value,
      position: policyForm.position,
      role: policyForm.role,
      metric_codes: policyForm.metric_codes,
      active: policyForm.active,
      note: policyForm.note,
    })
    ElMessage.success('岗位口径已保存')
    policyDialogVisible.value = false
    await loadPositions()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
