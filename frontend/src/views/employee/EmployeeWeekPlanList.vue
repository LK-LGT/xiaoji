<template>
  <section class="page-panel week-plan-list">
    <div v-if="isLeaderWorkspace" class="page-header">
      <div>
        <h1 class="page-title">{{ isLeaderWorkspace ? '我的周计划' : '周计划' }}</h1>
        <p class="page-subtitle">按自然周查看完整计划表，选中周后再进入编辑或查看。</p>
      </div>
      <div class="toolbar">
        <el-button :loading="loading" @click="loadRows">刷新</el-button>
        <el-button v-if="selectedPlan?.status === 'PENDING'" type="warning" plain :loading="withdrawingPlanId === selectedPlan.id" @click="withdrawSelectedPlan">撤回计划</el-button>
        <el-button type="primary" :disabled="!selectedPlan && !canCreateActiveWeek" @click="openSelectedWeek">
          {{ selectedPlan ? primaryLabel(selectedPlan) : '创建所选周计划' }}
        </el-button>
      </div>
    </div>

    <section class="week-browser mt16">
      <div class="week-browser__head">
        <div>
          <h2>周导航</h2>
          <p>选择自然周后，下方会直接展示该周的计划表。</p>
        </div>
        <div class="toolbar">
          <el-button @click="moveWeek(-7)">上一周</el-button>
          <el-button @click="goCurrentWeek">本周</el-button>
          <el-button @click="moveWeek(7)">下一周</el-button>
          <template v-if="!isLeaderWorkspace">
            <el-button :loading="loading" @click="loadRows">刷新</el-button>
            <el-button v-if="selectedPlan?.status === 'PENDING'" type="warning" plain :loading="withdrawingPlanId === selectedPlan.id" @click="withdrawSelectedPlan">撤回计划</el-button>
            <el-button type="primary" :disabled="!selectedPlan && !canCreateActiveWeek" @click="openSelectedWeek">
              {{ selectedPlan ? primaryLabel(selectedPlan) : '创建所选周计划' }}
            </el-button>
          </template>
        </div>
      </div>
      <div class="week-browser__list" aria-label="周导航">
        <button
          v-for="week in weekCards"
          :key="week.weekStart"
          type="button"
          class="week-browser-card"
          :class="{ 'is-active': week.weekStart === activeWeekStart, 'is-current': week.isCurrent, 'is-empty': !week.plan }"
          @click="selectWeek(week.weekStart)"
        >
          <span>{{ week.weekStart === activeWeekStart ? '所选周' : week.isCurrent ? '本周' : '自然周' }}</span>
          <strong>{{ week.weekStart.slice(5) }}<small>至 {{ week.weekEnd.slice(5) }}</small></strong>
          <em>{{ week.plan?.title || '该周暂无计划' }}</em>
          <div class="week-browser-card__status"><el-tag size="small" :type="week.statusType">{{ week.statusLabel }}</el-tag></div>
        </button>
      </div>
    </section>

    <el-alert v-if="errorMessage" class="mt16" type="warning" :closable="false" show-icon :title="errorMessage" />

    <section v-loading="detailLoading" class="week-preview-stage mt16">
      <div class="week-preview-stage__head">
        <div>
          <h2>周计划表</h2>
          <p>此处仅预览，修改内容请进入编辑页。</p>
        </div>
      </div>

      <div v-if="selectedDetail?.items.length" class="week-preview-table-wrap">
        <table class="week-preview-table" aria-label="所选周计划预览表">
          <thead>
            <tr>
              <th class="preview-name">姓名</th>
              <th class="preview-content">工作内容</th>
              <th class="preview-period">统计周期</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="preview-name-cell">{{ selectedDetail.summary.employeeName }}</td>
              <td><p class="preview-text">{{ selectedWeekFields.content }}</p></td>
              <td class="preview-period-cell">{{ selectedDetail.summary.weekStart }} 至 {{ selectedDetail.summary.weekEnd }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-else-if="!detailLoading" class="week-preview-empty">
        <strong>{{ selectedPlan ? '该周计划还没有工作内容' : '该周尚未创建计划' }}</strong>
        <span>{{ canCreateActiveWeek ? '进入编辑页后可以创建第一条周任务。' : '可切换到已创建的周查看。' }}</span>
        <el-button v-if="canCreateActiveWeek" type="primary" @click="openSelectedWeek">{{ selectedPlan ? '编辑计划' : '创建周计划' }}</el-button>
      </div>

    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { getEmployeeWeekPlanApi, listEmployeeWeekPlansApi, weekPlanStatusMeta, withdrawEmployeeWeekPlanApi, type WeekPlanDetail, type WeekPlanStatus, type WeekPlanSummary } from '@/api/weekPlan'
import { mergeWeekPlanFields } from '@/utils/weekPlanDisplay'

const router = useRouter()
const route = useRoute()
const isLeaderWorkspace = route.path.startsWith('/leader/')
const weekPlanBasePath = isLeaderWorkspace ? '/leader/my-week-plans' : '/employee/week-plans'
const loading = ref(false)
const detailLoading = ref(false)
// BUG-R6-14：周计划主键为字符串（UUID/十六进制）。
const withdrawingPlanId = ref<string | null>(null)
const errorMessage = ref('')
const rows = ref<WeekPlanSummary[]>([])
const selectedDetail = ref<WeekPlanDetail | null>(null)
const currentWeekStart = currentMonday()
const activeWeekStart = ref(currentWeekStart)
const selectedPlan = computed(() => rows.value.find((row) => row.weekStart === activeWeekStart.value))
const selectedWeekFields = computed(() => mergeWeekPlanFields(selectedDetail.value?.items || []))
const canCreateActiveWeek = computed(() => activeWeekStart.value >= currentWeekStart)
const weekCards = computed(() => Array.from({ length: 7 }, (_, index) => {
  const weekStart = shiftWeek(activeWeekStart.value, index - 3)
  const plan = rows.value.find((row) => row.weekStart === weekStart)
  const status = plan ? statusMeta(plan.status) : { label: weekStart >= currentWeekStart ? '未创建' : '无记录', type: 'info' as const }
  return { weekStart, weekEnd: addDays(weekStart, 6), plan, isCurrent: weekStart === currentWeekStart, statusLabel: status.label, statusType: status.type }
}))

function statusMeta(status: WeekPlanStatus) { return weekPlanStatusMeta[status] }
function dateValue(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` }
function currentMonday() { const date = new Date(); const day = date.getDay() || 7; date.setDate(date.getDate() - day + 1); return dateValue(date) }
function addDays(value: string, days: number) { const date = new Date(`${value}T00:00:00`); date.setDate(date.getDate() + days); return dateValue(date) }
function shiftWeek(value: string, offset: number) { return addDays(value, offset * 7) }
function primaryLabel(row: WeekPlanSummary) { return row.status === 'DRAFT' || row.status === 'REJECTED' ? '编辑计划' : '查看计划' }

async function loadSelectedDetail() {
  const plan = selectedPlan.value
  selectedDetail.value = null
  if (!plan) return
  detailLoading.value = true
  try { selectedDetail.value = await getEmployeeWeekPlanApi(plan.id) }
  catch (error) { errorMessage.value = error instanceof Error ? error.message : '周计划详情加载失败' }
  finally { detailLoading.value = false }
}

function selectWeek(weekStart: string) {
  activeWeekStart.value = weekStart
  void loadSelectedDetail()
}
function moveWeek(days: number) { selectWeek(addDays(activeWeekStart.value, days)) }
function goCurrentWeek() { selectWeek(currentWeekStart) }
function openSelectedWeek() {
  const plan = selectedPlan.value
  if (plan) return router.push(plan.status === 'DRAFT' || plan.status === 'REJECTED' ? `${weekPlanBasePath}/${plan.id}/edit` : `${weekPlanBasePath}/${plan.id}`)
  if (!canCreateActiveWeek.value) return ElMessage.info('历史周未创建计划，不能补建')
  return router.push({ path: `${weekPlanBasePath}/new/edit`, query: { weekStart: activeWeekStart.value } })
}
async function withdrawSelectedPlan() {
  const plan = selectedPlan.value
  if (!plan || plan.status !== 'PENDING' || withdrawingPlanId.value !== null) return
  try {
    await ElMessageBox.confirm('撤回后周计划恢复为草稿，可修改后重新提交。确认撤回？', '撤回周计划', {
      type: 'warning',
      confirmButtonText: '确认撤回',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  withdrawingPlanId.value = plan.id
  errorMessage.value = ''
  try {
    await withdrawEmployeeWeekPlanApi(plan.id, plan.versionNo)
    ElMessage.success('周计划已撤回为草稿')
    await loadRows()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '撤回周计划失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  } finally {
    withdrawingPlanId.value = null
  }
}
async function loadRows() {
  loading.value = true
  errorMessage.value = ''
  try {
    rows.value = await listEmployeeWeekPlansApi()
    await loadSelectedDetail()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '周计划加载失败'
  } finally { loading.value = false }
}

onMounted(loadRows)
</script>

<style scoped>
.week-plan-list { min-width:0; }
.week-browser { padding:16px; border:1px solid var(--line); border-radius:10px; background:#fff; }
.week-browser__head { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:12px; }
.week-browser__head > div:first-child { display:grid; gap:4px; }
.week-browser__head h2,.week-preview-stage__head h2 { margin:0; font-size:18px; }
.week-browser__head p,.week-preview-stage__head p { margin:0; color:var(--muted); font-size:12px; }
.week-browser__list { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:8px; }
.week-browser-card { display:grid; min-width:0; min-height:126px; align-content:start; gap:7px; padding:11px; border:1px solid var(--line); border-radius:8px; color:var(--ink); background:#fff; text-align:left; cursor:pointer; }
.week-browser-card:hover { border-color:#9fbfb0; }
.week-browser-card.is-active { border-color:var(--blue); background:#eef7f2; box-shadow:inset 0 0 0 1px var(--blue); }
.week-browser-card.is-current:not(.is-active) { border-color:#bfd4c9; }
.week-browser-card.is-empty { color:var(--muted); }
.week-browser-card > span { color:var(--blue); font-size:10px; font-weight:800; }
.week-browser-card strong { display:grid; gap:3px; font:800 18px/1.1 "IBM Plex Mono","Cascadia Mono",monospace; }
.week-browser-card small { color:var(--muted); font:600 11px/1.2 var(--font-sans,sans-serif); }
.week-browser-card em { display:-webkit-box; overflow:hidden; color:var(--muted); font-size:11px; font-style:normal; line-height:1.35; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
.week-browser-card__status { display:flex; flex-wrap:wrap; gap:5px; }
.week-preview-stage { min-height:300px; border:1px solid var(--line); background:#fff; }
.week-preview-stage__head { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 16px; border-bottom:1px solid var(--line); background:#f7f9f6; }
.week-preview-stage__head > div { display:grid; gap:4px; }
.week-preview-table-wrap { width:100%; overflow-x:auto; }
.week-preview-table { width:100%; min-width:720px; border-collapse:collapse; table-layout:fixed; color:#263a34; font-size:13px; }
.week-preview-table th,.week-preview-table td { padding:12px 10px; border-right:1px solid #cfd9d3; border-bottom:1px solid #d9e1dc; vertical-align:middle; line-height:1.65; overflow-wrap:anywhere; }
.week-preview-table th:last-child,.week-preview-table td:last-child { border-right:0; }
.week-preview-table th { color:#304a42; background:#e6f0f6; font-weight:750; text-align:center; }
.preview-name{width:110px}.preview-content{width:auto}.preview-period{width:210px}
.preview-name-cell,.preview-period-cell { text-align:center; }
.preview-text { margin:0; line-height:1.65; white-space:pre-wrap; }
.week-preview-empty { display:grid; min-height:240px; place-items:center; align-content:center; gap:8px; color:var(--muted); text-align:center; }
.week-preview-empty strong { color:var(--ink); }
@media (max-width:900px) { .week-browser__list { grid-template-columns:repeat(4,minmax(0,1fr)); } }
@media (max-width:600px) { .week-browser__head,.week-preview-stage__head { align-items:flex-start; flex-direction:column; }.week-browser__list { grid-auto-flow:column; grid-auto-columns:72%; grid-template-columns:none; overflow-x:auto; scroll-snap-type:x mandatory; }.week-browser-card { scroll-snap-align:start; } }
</style>
