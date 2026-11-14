<template>
  <section class="page-panel">
    <div class="page-header">
      <div>
        <h1 class="page-title">周计划台账</h1>
        <p class="page-subtitle">只读查看直属领导已经处理完成的周计划和审批结果。</p>
      </div>
      <el-button :loading="loading" @click="loadRows">刷新</el-button>
    </div>

    <el-alert class="mt16" type="info" :closable="false" show-icon title="本页面不参与二次审批；待审批周计划不会展示。" />

    <div class="filter-bar mt16">
      <el-select v-model="status" clearable placeholder="全部审批结果">
        <el-option label="已通过" value="APPROVED" />
        <el-option label="已驳回" value="REJECTED" />
      </el-select>
      <el-date-picker v-model="weekStart" type="date" value-format="YYYY-MM-DD" clearable placeholder="按周一筛选" />
      <el-button @click="search">查询</el-button>
    </div>

    <el-table v-loading="loading" class="mt16" :data="rows" border empty-text="暂无已处理周计划">
      <el-table-column prop="employeeName" label="员工" width="110" />
      <el-table-column prop="departmentName" label="组织" min-width="150" />
      <el-table-column prop="weekStart" label="周开始" width="120" />
      <el-table-column prop="weekEnd" label="周结束" width="120" />
      <el-table-column label="周计划" width="80"><template #default>1 份</template></el-table-column>
      <el-table-column label="审批结果" width="110">
        <template #default="{ row }"><el-tag :type="statusMeta(row.status).type">{{ statusMeta(row.status).label }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="approvalComment" label="直属领导意见" min-width="220" show-overflow-tooltip />
      <el-table-column prop="approveAt" label="审批时间" width="170" />
      <el-table-column label="操作" width="90">
        <template #default="{ row }"><el-button link type="primary" @click="open(row)">查看</el-button></template>
      </el-table-column>
    </el-table>

    <div class="pagination-row">
      <el-pagination
        v-model:current-page="pageNo"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        layout="sizes, prev, pager, next, total"
        :total="total"
        @current-change="changePage"
        @size-change="changePageSize"
      />
    </div>

    <el-drawer v-model="drawer" title="周计划审批结果" size="min(1080px, 96vw)">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="员工">{{ detail.summary.employeeName }}</el-descriptions-item>
          <el-descriptions-item label="组织">{{ detail.summary.departmentName }}</el-descriptions-item>
          <el-descriptions-item label="自然周">{{ detail.summary.weekStart }} 至 {{ detail.summary.weekEnd }}</el-descriptions-item>
          <el-descriptions-item label="审批结果"><el-tag :type="weekPlanStatusMeta[detail.summary.status].type">{{ weekPlanStatusMeta[detail.summary.status].label }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="直属领导意见" :span="2">{{ detail.summary.approvalComment || '无' }}</el-descriptions-item>
        </el-descriptions>
        <el-table class="mt16" :data="detailRows" border>
          <el-table-column label="姓名" width="100"><template #default>{{ detail.summary.employeeName }}</template></el-table-column>
          <el-table-column label="工作内容" min-width="320"><template #default="{ row }"><div class="ledger-work-content"><span>{{ row.content || '未填写' }}</span></div></template></el-table-column>
          <el-table-column label="考核标准（交付物、质量）" min-width="300"><template #default="{ row }"><div class="ledger-standard-cell"><p><strong>交付物：</strong>{{ row.deliverable || '未填写' }}</p><p><strong>质量：</strong>{{ row.quality || '未填写' }}</p></div></template></el-table-column>
          <el-table-column label="统计周期" width="190"><template #default>{{ detail.summary.weekStart }} 至 {{ detail.summary.weekEnd }}</template></el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getDepartmentWeekPlanApi,
  listDepartmentWeekPlansApi,
  weekPlanStatusMeta,
  type WeekPlanDetail,
  type WeekPlanStatus,
  type WeekPlanSummary,
} from '@/api/weekPlan'
import { mergeWeekPlanFields } from '@/utils/weekPlanDisplay'

const loading = ref(false)
const rows = ref<WeekPlanSummary[]>([])
const status = ref('')
const weekStart = ref('')
const pageNo = ref(1)
const pageSize = ref(10)
const total = ref(0)
const drawer = ref(false)
const detail = ref<WeekPlanDetail | null>(null)
const detailRows = computed(() => detail.value ? [mergeWeekPlanFields(detail.value.items)] : [])

function statusMeta(value: WeekPlanStatus) {
  return weekPlanStatusMeta[value]
}

async function loadRows() {
  loading.value = true
  try {
    const data = await listDepartmentWeekPlansApi({
      status: status.value as 'APPROVED' | 'REJECTED' | '',
      weekStart: weekStart.value || undefined,
      pageNo: pageNo.value,
      pageSize: pageSize.value,
    })
    if (!data.items.length && data.total > 0 && pageNo.value > 1) {
      pageNo.value = Math.ceil(data.total / pageSize.value)
      await loadRows()
      return
    }
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '周计划台账加载失败')
  } finally {
    loading.value = false
  }
}

async function search() {
  pageNo.value = 1
  await loadRows()
}

async function changePage(page: number) {
  pageNo.value = page
  await loadRows()
}

async function changePageSize(size: number) {
  pageSize.value = size
  pageNo.value = 1
  await loadRows()
}

async function open(row: WeekPlanSummary) {
  try {
    detail.value = await getDepartmentWeekPlanApi(row.id)
    drawer.value = true
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '详情加载失败')
  }
}

watch([status, weekStart], () => {
  pageNo.value = 1
})

onMounted(loadRows)
</script>

<style scoped>
.ledger-work-content { display:grid; gap:6px; white-space:pre-wrap; }
.ledger-work-content small { color:var(--muted); }
.ledger-standard-cell { display:grid; gap:6px; line-height:1.65; white-space:pre-wrap; overflow-wrap:anywhere; }
.ledger-standard-cell p { margin:0; }
.ledger-standard-cell strong { color:#304a42; }
</style>
