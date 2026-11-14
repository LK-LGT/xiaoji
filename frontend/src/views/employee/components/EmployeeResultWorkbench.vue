<template>
  <section class="period-workbench">
    <div class="period-toolbar">
      <div class="period-switcher">
        <el-tooltip content="上一月" placement="bottom">
          <el-button circle :icon="ArrowLeft" aria-label="上一月" @click="changeMonth(-1)" />
        </el-tooltip>
        <el-date-picker
          v-model="selectedMonth"
          type="month"
          value-format="YYYY-MM"
          format="YYYY年MM月"
          :clearable="false"
          aria-label="选择成果月份"
          @change="selectMonth"
        />
        <el-tooltip content="下一月" placement="bottom">
          <el-button circle :icon="ArrowRight" aria-label="下一月" @click="changeMonth(1)" />
        </el-tooltip>
      </div>
      <div class="result-filter" role="tablist" aria-label="成果状态筛选">
        <button
          type="button"
          role="tab"
          :aria-selected="activeFilter === 'PENDING'"
          :class="{ 'is-active': activeFilter === 'PENDING', 'has-returned': statusCounts.returned > 0 }"
          @click="activeFilter = 'PENDING'"
        >
          <span>待处理</span><strong>{{ pendingCount }}</strong>
          <small v-if="statusCounts.returned">退回{{ statusCounts.returned }}</small>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="activeFilter === 'IN_REVIEW'"
          :class="{ 'is-active': activeFilter === 'IN_REVIEW' }"
          @click="activeFilter = 'IN_REVIEW'"
        >
          <span>审批中</span><strong>{{ statusCounts.inReview }}</strong>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="activeFilter === 'DONE'"
          :class="{ 'is-active': activeFilter === 'DONE' }"
          @click="activeFilter = 'DONE'"
        >
          <span>已完成</span><strong>{{ statusCounts.done }}</strong>
        </button>
      </div>
    </div>

    <el-alert v-if="errorMessage" class="workbench-alert" type="warning" :closable="false" show-icon>
      <template #title>{{ errorMessage }}</template>
      <el-button link type="primary" @click="loadWorkbench">重新加载</el-button>
    </el-alert>

    <el-skeleton v-if="loading && !workbench" class="workbench-loading" :rows="8" animated />

    <template v-else-if="workbench && !errorMessage">
      <section v-if="activeFilter === 'PENDING' && workbench.crossPeriodAlerts.length" class="priority-section" aria-label="跨期退回成果">
        <button
          v-for="item in workbench.crossPeriodAlerts"
          :key="item.key"
          type="button"
          class="cross-period-alert"
          @click="openPackage(item, 'PENDING')"
        >
          <span class="alert-mark">跨期退回</span>
          <span class="alert-title"><strong>{{ item.title }}</strong><small>{{ item.periodKey }}</small></span>
          <span class="alert-action">处理退回<el-icon aria-hidden="true"><ArrowRight /></el-icon></span>
        </button>
      </section>

      <section v-if="visiblePackages.length" class="package-section">
        <div class="package-list">
          <button
            v-for="item in visiblePackages"
            :key="item.key"
            type="button"
            class="result-package-row"
            :class="{ 'has-rejection': activeFilter === 'PENDING' && item.counts.rejected > 0 }"
            @click="openPackage(item, activeFilter)"
          >
            <div class="result-package-main">
              <h2>{{ packageTitle(item) }}</h2>
              <div class="result-package-meta">
                <strong :class="{ 'is-danger': activeFilter === 'PENDING' && item.counts.rejected > 0 }">
                  {{ packageStateText(item) }}
                </strong>
                <span v-if="item.type === 'MONTH' && item.endDate">截止 {{ compactDate(item.endDate) }}</span>
                <span v-else-if="item.type === 'WEEK'">{{ compactDate(item.startDate) }} - {{ compactDate(item.endDate) }}</span>
              </div>
            </div>
            <span class="row-action">
              {{ packageActionLabel(item) }}
              <el-icon><ArrowRight /></el-icon>
            </span>
          </button>
        </div>
      </section>

      <div v-else class="workbench-empty">
        <el-icon class="empty-icon"><DocumentChecked /></el-icon>
        <strong>{{ emptyTitle }}</strong>
        <span>{{ emptyHint }}</span>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { ArrowLeft, ArrowRight, DocumentChecked } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEmployeeResultSubmitOptionsApi, type EmployeeResultSubmitOptionsResp } from '@/api/employee'
import { getWeekResultSubmitOptionsApi, type WeekResultSubmitOptions } from '@/api/weekResult'
import { currentMonthKey } from '@/utils/monthGroups'
import {
  buildResultWorkbench,
  type ResultWorkbenchData,
  type ResultWorkbenchPackage,
} from '../resultWorkbenchModel'
import { normalizeResultStatus, resultStatusCounts, type ResultStatusCounts } from '../resultStatus'

type ResultFilter = 'PENDING' | 'IN_REVIEW' | 'DONE'

const route = useRoute()
const router = useRouter()
const resultBasePath = computed(() => route.path.startsWith('/leader/') ? '/leader/results' : '/employee/results')
const selectedMonth = ref(/^\d{4}-\d{2}$/.test(String(route.query.month || '')) ? String(route.query.month) : currentMonthKey())
const activeFilter = ref<ResultFilter>('PENDING')
const loading = ref(false)
const errorMessage = ref('')
const workbench = ref<ResultWorkbenchData | null>(null)
const monthOptions = reactive<EmployeeResultSubmitOptionsResp>({ monthPlanOptions: [], monthPlanItemOptions: [], resultVersions: [], acceptedFileTypes: [], maxFileSizeMb: 20 })
const weekOptions = reactive<WeekResultSubmitOptions>({ plans: [], acceptedFileTypes: [], maxFileSizeMb: 20 })

const statusCounts = computed<ResultStatusCounts>(() => resultStatusCounts(workbench.value?.packages.flatMap((item) => item.tasks.map((task) => task.status)) || []))
const pendingCount = computed(() => statusCounts.value.todo + statusCounts.value.returned)
const visiblePackages = computed(() => {
  const packages = (workbench.value?.packages || []).filter((item) => item.tasks.some((task) => taskMatchesFilter(task.status, activeFilter.value)))
  if (activeFilter.value !== 'PENDING') return packages
  return [...packages].sort((left, right) => Number(right.counts.rejected > 0) - Number(left.counts.rejected > 0))
})
const emptyTitle = computed(() => ({
  PENDING: '当前月份没有待处理成果',
  IN_REVIEW: '当前月份没有审批中的成果',
  DONE: '当前月份没有已完成成果',
})[activeFilter.value])
const emptyHint = computed(() => {
  if (!workbench.value?.totals.total) return '计划审批通过后，将自动生成对应成果任务。'
  return '可切换上方状态查看其他成果。'
})

function taskMatchesFilter(status: string, filter: ResultFilter) {
  const normalized = normalizeResultStatus(status)
  if (filter === 'PENDING') return normalized === 'TODO' || normalized === 'RETURNED'
  if (filter === 'IN_REVIEW') return normalized === 'IN_REVIEW'
  return normalized === 'DONE'
}

function compactDate(value?: string) {
  if (!value) return '--'
  return value.length >= 10 ? value.slice(5, 10).replace('-', '.') : value
}

function weekTitle(item: ResultWorkbenchPackage) {
  const start = item.startDate ? new Date(`${item.startDate}T00:00:00`) : null
  if (!start || Number.isNaN(start.getTime())) return item.title
  return `${start.getMonth() + 1}月第${Math.ceil(start.getDate() / 7)}周`
}

function packageTitle(item: ResultWorkbenchPackage) {
  return item.type === 'MONTH' ? item.title : `${weekTitle(item)}执行成果`
}

function packageStateText(item: ResultWorkbenchPackage) {
  if (activeFilter.value === 'IN_REVIEW') return `${item.counts.submitted}项审批中`
  if (activeFilter.value === 'DONE') {
    const completed = item.tasks.filter((task) => taskMatchesFilter(task.status, 'DONE')).length
    return `${completed}项已完成`
  }
  const pending = item.counts.pending - item.counts.rejected
  return [
    item.counts.rejected ? `${item.counts.rejected}项被退回` : '',
    pending ? `${pending}项待填写` : '',
  ].filter(Boolean).join('，')
}

function packageActionLabel(item: ResultWorkbenchPackage) {
  if (activeFilter.value === 'IN_REVIEW') return '查看进度'
  if (activeFilter.value === 'DONE') return '查看成果'
  if (item.counts.rejected) return '处理退回'
  if (item.tasks.some((task) => task.status === 'DRAFT')) return '继续填写'
  return '去填写'
}

function changeMonth(offset: number) {
  const [year, month] = selectedMonth.value.split('-').map(Number)
  const target = new Date(year, month - 1 + offset, 1)
  selectedMonth.value = `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, '0')}`
  selectMonth()
}

function selectMonth() {
  void router.replace({ query: { ...route.query, month: selectedMonth.value } })
  void loadWorkbench()
}

function openPackage(item: ResultWorkbenchPackage, filter: ResultFilter) {
  const preferred = filter === 'PENDING'
    ? item.tasks.find((task) => task.status === 'REJECTED')
      || item.tasks.find((task) => task.status === 'DRAFT')
      || item.tasks.find((task) => taskMatchesFilter(task.status, filter))
    : item.tasks.find((task) => taskMatchesFilter(task.status, filter))
  const target = preferred || item.tasks[0]
  void router.push({
    path: `${resultBasePath.value}/workspace/${item.type}/${item.planId}`,
    query: { month: selectedMonth.value, ...(target ? { taskId: String(target.taskId) } : {}) },
  })
}

async function loadWorkbench() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [months, weeks] = await Promise.all([getEmployeeResultSubmitOptionsApi(), getWeekResultSubmitOptionsApi()])
    Object.assign(monthOptions, months)
    Object.assign(weekOptions, weeks)
    workbench.value = buildResultWorkbench(monthOptions, weekOptions, selectedMonth.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '成果工作台加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(loadWorkbench)
</script>

<style scoped>
.period-workbench { padding-top:14px; color:#263a34; }
.period-toolbar { display:flex; align-items:center; justify-content:space-between; gap:20px; min-height:56px; padding:8px 12px; border-bottom:1px solid #d8e1dc; background:#f7f9f7; }
.period-switcher { display:flex; align-items:center; gap:10px; }
.period-switcher :deep(.el-date-editor) { width:160px; }
.result-filter { display:flex; align-items:center; height:36px; border:1px solid #d8e1dc; background:#fff; }
.result-filter button { display:flex; align-items:center; justify-content:center; gap:7px; height:100%; min-width:100px; padding:0 14px; border:0; border-right:1px solid #d8e1dc; color:#61716a; background:transparent; font:inherit; font-size:13px; cursor:pointer; }
.result-filter button:last-child { border-right:0; }
.result-filter button:hover { background:#f3f7f5; }
.result-filter button.is-active { color:#1f6f64; background:#eaf4f0; font-weight:700; }
.result-filter strong { color:inherit; font-size:14px; }
.result-filter small { color:#b64d45; font-size:11px; font-weight:700; }
.result-filter .has-returned:not(.is-active) strong { color:#b64d45; }
.workbench-alert,.workbench-loading { margin-top:16px; }
.priority-section { margin-top:14px; border:1px solid #ead8d5; }
.cross-period-alert { display:grid; grid-template-columns:88px minmax(180px,1fr) auto; align-items:center; width:100%; min-height:58px; padding:8px 14px; border:0; border-bottom:1px solid #ead8d5; color:#263a34; background:#fff8f7; text-align:left; cursor:pointer; }
.cross-period-alert:last-child { border-bottom:0; }
.cross-period-alert:hover { background:#fff3f1; }
.alert-mark,.is-danger { color:#b64d45!important; font-weight:700; }
.alert-title { display:flex; align-items:baseline; gap:10px; }
.alert-title small { color:#8a7772; }
.alert-action { display:flex; align-items:center; gap:4px; color:#a0443d; font-weight:700; }
.package-section { margin-top:14px; }
.package-list { border-top:1px solid #d8e1dc; border-bottom:1px solid #d8e1dc; background:#fff; }
.result-package-row { display:grid; grid-template-columns:minmax(240px,1fr) auto; align-items:center; gap:24px; width:100%; min-height:86px; padding:14px 16px; border:0; border-bottom:1px solid #dfe6e2; color:inherit; background:#fff; font:inherit; text-align:left; cursor:pointer; }
.result-package-row:last-child { border-bottom:0; }
.result-package-row:hover { background:#f7faf8; }
.result-package-row:focus-visible { position:relative; z-index:1; outline:2px solid #2d776c; outline-offset:-2px; }
.result-package-row.has-rejection { box-shadow:inset 3px 0 #b64d45; background:#fffafa; }
.result-package-row.has-rejection:hover { background:#fff5f4; }
.result-package-main { min-width:0; }
.result-package-main h2 { margin:0 0 8px; overflow:hidden; color:#263a34; font-size:17px; font-weight:700; letter-spacing:0; text-overflow:ellipsis; white-space:nowrap; }
.result-package-meta { display:flex; align-items:center; flex-wrap:wrap; gap:8px 18px; color:#728079; font-size:12px; }
.result-package-meta strong { color:#4f625a; font-size:13px; }
.row-action { display:flex; align-items:center; justify-content:flex-end; gap:4px; min-width:112px; color:#1f7468; font-size:13px; font-weight:700; }
.workbench-empty { display:grid; min-height:300px; place-items:center; align-content:center; gap:10px; color:#75847d; text-align:center; }
.workbench-empty .empty-icon { color:#2d776c; font-size:30px; }
.workbench-empty strong { color:#30463f; }
@media (max-width:760px) {
  .period-toolbar { align-items:stretch; flex-direction:column; gap:10px; padding:10px 0; background:transparent; }
  .period-switcher { justify-content:center; }
  .result-filter { width:100%; }
  .result-filter button { flex:1; min-width:0; padding:0 8px; }
  .result-filter small { display:none; }
  .cross-period-alert { grid-template-columns:78px minmax(0,1fr) auto; padding-inline:10px; }
  .alert-title { align-items:flex-start; flex-direction:column; gap:2px; }
  .result-package-row { grid-template-columns:minmax(0,1fr) auto; gap:12px; padding-inline:10px; }
  .row-action { min-width:96px; }
}
@media (max-width:480px) {
  .result-filter button { gap:4px; }
  .cross-period-alert { grid-template-columns:72px minmax(0,1fr); }
  .alert-action { display:none; }
  .result-package-row { align-items:stretch; grid-template-columns:1fr; }
  .result-package-main h2 { white-space:normal; }
  .row-action { justify-content:flex-start; width:100%; }
}
</style>
