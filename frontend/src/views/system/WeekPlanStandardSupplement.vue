<template>
  <section class="page-panel standard-supplement-page" v-loading="loading">
    <div class="page-header">
      <div>
        <span class="eyebrow">WEEK PLAN STANDARD</span>
        <h1 class="page-title">周计划考核标准补录</h1>
        <p class="page-subtitle">列出已通过但交付物或质量为空的周计划条目，只补录考核标准，不改变审批状态。</p>
      </div>
      <el-button :icon="RefreshLeft" @click="resetFilters">重置筛选</el-button>
    </div>

    <div class="filter-bar">
      <el-input v-model="filters.keyword" clearable placeholder="搜索员工、部门或工作内容" />
      <el-select v-model="filters.deptId" clearable filterable placeholder="所属部门">
        <el-option
          v-for="dept in departmentOptions"
          :key="dept.id"
          :label="`${'　'.repeat(dept.depth)}${dept.name}`"
          :value="dept.id"
        />
      </el-select>
      <el-date-picker v-model="filters.weekStart" type="date" value-format="YYYY-MM-DD" placeholder="周开始日期" />
      <el-button type="primary" :icon="Search" @click="search">查询</el-button>
    </div>

    <div class="table-card supplement-table-card">
      <el-table :data="page.records" empty-text="暂无需要补录的已通过周计划">
        <el-table-column label="员工" width="170">
          <template #default="{ row }">
            <div class="employee-cell">
              <strong>{{ row.employeeName }}</strong>
              <small>{{ row.departmentName || '未设置部门' }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="周期" width="210">
          <template #default="{ row }">
            <div class="period-cell">
              <strong>{{ row.weekStart }} 至 {{ row.weekEnd }}</strong>
              <small>审批：{{ formatTime(row.approveAt) }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="周计划" min-width="360">
          <template #default="{ row }">
            <p class="content-preview">{{ row.content || '未填写' }}</p>
          </template>
        </el-table-column>
        <el-table-column label="交付物" min-width="220">
          <template #default="{ row }">
            <span :class="{ missing: !row.deliverable }">{{ row.deliverable || '待补录' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="质量" min-width="220">
          <template #default="{ row }">
            <span :class="{ missing: !row.quality }">{{ row.quality || '待补录' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="EditPen" @click="openSupplement(row)">补录</el-button>
            <el-button link :icon="View" @click="openDetail(row.weekPlanId)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="pagination-row">
      <span>共 {{ page.total }} 条待补录记录</span>
      <el-pagination
        v-model:current-page="filters.pageNo"
        :page-size="filters.pageSize"
        layout="prev, pager, next"
        :total="page.total"
        @current-change="load"
      />
    </div>

    <el-dialog v-model="dialogVisible" title="补录考核标准" width="640px" destroy-on-close>
      <div v-if="activeRow" class="supplement-context">
        <div><span>员工</span><strong>{{ activeRow.employeeName }}</strong></div>
        <div><span>周期</span><strong>{{ activeRow.weekStart }} 至 {{ activeRow.weekEnd }}</strong></div>
      </div>
      <p v-if="activeRow" class="supplement-content">{{ activeRow.content }}</p>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item label="交付物" prop="deliverable">
          <el-input v-model.trim="form.deliverable" type="textarea" :rows="4" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item label="质量" prop="quality">
          <el-input v-model.trim="form.quality" type="textarea" :rows="4" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSupplement">保存补录</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" size="min(760px, 100%)" destroy-on-close>
      <template #header>
        <div>
          <span class="eyebrow">WEEK PLAN DETAIL</span>
          <h2>周计划详情</h2>
        </div>
      </template>
      <div v-if="detail" class="detail-stack">
        <section class="detail-summary">
          <div><span>员工</span><strong>{{ detail.summary.employeeName }}</strong></div>
          <div><span>周期</span><strong>{{ detail.summary.weekStart }} 至 {{ detail.summary.weekEnd }}</strong></div>
          <div><span>状态</span><strong>已通过</strong></div>
        </section>
        <article v-for="item in detail.items" :key="item.id" class="detail-item">
          <h3>工作内容</h3>
          <p>{{ item.content || '未填写' }}</p>
          <dl>
            <div><dt>交付物</dt><dd>{{ item.deliverable || '未填写' }}</dd></div>
            <div><dt>质量</dt><dd>{{ item.quality || '未填写' }}</dd></div>
          </dl>
        </article>
      </div>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { EditPen, RefreshLeft, Search, View } from '@element-plus/icons-vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import {
  flattenDepartments,
  getSystemOptions,
  getWeekPlanStandardSupplementDetail,
  getWeekPlanStandardSupplements,
  supplementWeekPlanStandard,
  type PageResult,
  type WeekPlanStandardSupplementRow,
} from '@/api/system'
import type { WeekPlanDetail } from '@/api/weekPlan'
import { useAutoQuery } from '@/composables/useAutoQuery'

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const detailVisible = ref(false)
const activeRow = ref<WeekPlanStandardSupplementRow>()
const detail = ref<WeekPlanDetail>()
const formRef = ref<FormInstance>()
const departments = ref<Awaited<ReturnType<typeof getSystemOptions>>['departments']>([])
const page = reactive<PageResult<WeekPlanStandardSupplementRow>>({ records: [], total: 0, pageNo: 1, pageSize: 20 })
const filters = reactive<{ keyword: string; deptId?: string; weekStart: string; pageNo: number; pageSize: number }>({
  keyword: '',
  deptId: undefined,
  weekStart: '',
  pageNo: 1,
  pageSize: 20,
})
const form = reactive({ versionNo: 0, deliverable: '', quality: '' })
const rules: FormRules = {
  deliverable: [{ required: true, message: '请填写交付物', trigger: 'blur' }],
  quality: [{ required: true, message: '请填写质量标准', trigger: 'blur' }],
}
const departmentOptions = computed(() => flattenDepartments(departments.value))
const autoQuery = useAutoQuery(
  () => [filters.keyword, filters.deptId, filters.weekStart],
  () => {
    filters.pageNo = 1
    return load()
  },
)

function params() {
  return {
    keyword: filters.keyword || undefined,
    deptId: filters.deptId,
    weekStart: filters.weekStart || undefined,
    pageNo: filters.pageNo,
    pageSize: filters.pageSize,
  }
}

async function load() {
  loading.value = true
  try {
    Object.assign(page, await getWeekPlanStandardSupplements(params()))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '待补录周计划加载失败')
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  try {
    const options = await getSystemOptions()
    departments.value = options.departments
  } catch {
    ElMessage.warning('部门筛选加载失败，仍可查看全部待补录记录')
  }
}

function search() {
  filters.pageNo = 1
  void autoQuery.runNow()
}

async function resetFilters() {
  autoQuery.pause()
  filters.keyword = ''
  filters.deptId = undefined
  filters.weekStart = ''
  filters.pageNo = 1
  await load()
  autoQuery.resume()
}

function openSupplement(row: WeekPlanStandardSupplementRow) {
  activeRow.value = row
  form.versionNo = row.versionNo
  form.deliverable = row.deliverable || ''
  form.quality = row.quality || ''
  dialogVisible.value = true
}

async function saveSupplement() {
  if (!activeRow.value || !await formRef.value?.validate()) return
  saving.value = true
  try {
    await supplementWeekPlanStandard(activeRow.value.itemId, {
      versionNo: form.versionNo,
      deliverable: form.deliverable,
      quality: form.quality,
    })
    ElMessage.success('周计划考核标准已补录')
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '补录保存失败')
  } finally {
    saving.value = false
  }
}

async function openDetail(weekPlanId: number) {
  detailVisible.value = true
  detail.value = undefined
  try {
    detail.value = await getWeekPlanStandardSupplementDetail(weekPlanId)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '周计划详情加载失败')
  }
}

function formatTime(value?: string) {
  return value ? value.replace('T', ' ').slice(0, 16) : '未记录'
}

onMounted(async () => {
  await Promise.all([load(), loadOptions()])
  autoQuery.resume()
})
</script>

<style scoped>
.standard-supplement-page { min-width: 0; }
.supplement-table-card :deep(.el-table__cell) { vertical-align: top; }
.employee-cell,.period-cell { display: grid; gap: 3px; }
.employee-cell strong,.period-cell strong { color: var(--ink); }
.employee-cell small,.period-cell small { color: var(--muted); font-size: 12px; }
.content-preview { display: -webkit-box; margin: 0; overflow: hidden; color: #30443d; line-height: 1.55; white-space: pre-line; -webkit-box-orient: vertical; -webkit-line-clamp: 4; }
.missing { color: #b45309; font-weight: 750; }
.supplement-context,.detail-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.supplement-context > div,.detail-summary > div { display: grid; gap: 4px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: #f7faf8; }
.supplement-context span,.detail-summary span { color: var(--muted); font-size: 12px; }
.supplement-context strong,.detail-summary strong { color: var(--ink); font-size: 14px; }
.supplement-content { max-height: 180px; margin: 0 0 14px; overflow: auto; padding: 12px; border: 1px solid #dde6e1; border-radius: 8px; color: #2f443d; background: #fbfdfb; line-height: 1.6; white-space: pre-line; }
.detail-stack { display: grid; gap: 14px; }
.detail-item { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.detail-item h3 { margin: 0; color: #253b34; font-size: 15px; }
.detail-item p { margin: 0; color: #354b44; line-height: 1.65; white-space: pre-line; }
.detail-item dl { display: grid; gap: 8px; margin: 0; }
.detail-item dl > div { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 10px; padding: 9px 10px; border-radius: 7px; background: #f6f8f7; }
.detail-item dt { color: var(--muted); font-weight: 750; }
.detail-item dd { margin: 0; white-space: pre-line; }
@media (max-width: 760px) {
  .supplement-context,.detail-summary { grid-template-columns: 1fr; }
}
</style>
