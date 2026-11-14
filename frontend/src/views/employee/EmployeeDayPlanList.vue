<template>
  <section class="page-panel day-plan-list">
    <div v-if="isLeaderMode" class="page-header day-plan-list__header">
      <div>
        <span class="eyebrow">{{ isLeaderMode ? '直属领导端' : '员工工作台' }}</span>
        <h1 class="page-title">{{ isLeaderMode ? '我的日计划' : '日计划' }}</h1>
        <p class="page-subtitle">按周确认每日填报状态，直接进入需要处理的日期。</p>
      </div>
      <div class="toolbar">
        <el-tooltip content="刷新日计划" placement="bottom">
          <el-button :icon="Refresh" circle aria-label="刷新日计划" :loading="loading" @click="refreshAll" />
        </el-tooltip>
        <el-button type="primary" @click="openDate(today)">{{ todayActionLabel }}</el-button>
      </div>
    </div>

    <section class="day-week-browser mt16" aria-labelledby="day-week-title">
      <div class="day-week-browser__head">
        <div>
          <span id="day-week-title">一周工作节奏</span>
          <strong>{{ activeWeekStart }} 至 {{ activeWeekEnd }}</strong>
          <small>点击日期查看当天计划</small>
        </div>
        <div class="day-week-browser__nav">
          <el-tooltip content="上一周" placement="top">
            <el-button :icon="ArrowLeft" circle aria-label="上一周" @click="moveWeek(-7)" />
          </el-tooltip>
          <el-button :disabled="isCurrentWeek" @click="goCurrentWeek">回到本周</el-button>
          <el-tooltip content="下一周" placement="top">
            <el-button :icon="ArrowRight" circle aria-label="下一周" @click="moveWeek(7)" />
          </el-tooltip>
          <el-tooltip v-if="!isLeaderMode" content="刷新日计划" placement="bottom">
            <el-button :icon="Refresh" circle aria-label="刷新日计划" :loading="loading" @click="refreshAll" />
          </el-tooltip>
          <el-button v-if="!isLeaderMode" type="primary" @click="openDate(today)">{{ todayActionLabel }}</el-button>
        </div>
      </div>

      <div class="day-week-scroll">
        <div class="day-week-track">
          <button
            v-for="day in weekDays"
            :key="day.date"
            type="button"
            :aria-label="`${day.date} ${day.plan ? planStatusMeta(day.plan.status).label : '无计划'}`"
            :class="{
              'is-today': day.date === today,
              'is-selected': day.date === selectedDate,
              'has-plan': Boolean(day.plan),
              [`is-${day.plan?.status.toLowerCase()}`]: Boolean(day.plan),
            }"
            :aria-current="day.date === selectedDate ? 'date' : undefined"
            @click="selectDate(day.date)"
          >
            <span>{{ day.weekday }}</span>
            <strong>{{ day.date.slice(8) }}</strong>
            <small>{{ day.date === today ? '今天' : day.plan ? planStatusMeta(day.plan.status).label : day.date < today ? '无记录' : '待编制' }}</small>
            <em>{{ day.plan?.title || (day.date === today ? '安排今天工作' : '暂无计划') }}</em>
            <i aria-hidden="true"></i>
          </button>
        </div>
      </div>
    </section>

    <section class="day-record-section" aria-labelledby="day-record-title">
      <div class="day-record-section__head">
        <div>
          <h2 id="day-record-title">日计划表 / {{ selectedDateLabel }}</h2>
          <p>仅显示当前选中日期的计划内容。</p>
        </div>
        <el-button type="primary" plain @click="openDate(selectedDate)">{{ selectedActionLabel }}</el-button>
      </div>

      <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />

      <div v-loading="loading" class="day-record-table-wrap">
        <table v-if="dayPreviewRows.length" class="day-record-table" aria-label="所选日期日计划预览表">
          <thead>
            <tr>
              <th class="preview-name">姓名</th>
              <th class="preview-content">工作内容</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="preview-name-cell">{{ authStore.user?.realName || '当前用户' }}</td>
              <td><p class="preview-text">{{ dayPreviewRows[0].content }}</p></td>
            </tr>
          </tbody>
        </table>
        <div v-else-if="!loading" class="day-record-empty">
          <strong>当天暂无日计划</strong>
          <span>进入编辑页后可以填写当天工作内容。</span>
          <el-button type="primary" @click="openDate(selectedDate)">编制当天计划</el-button>
        </div>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { ArrowLeft, ArrowRight, Refresh } from '@element-plus/icons-vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listDayPlansApi, type DayPlan, type PlanStatus } from '@/api/planning'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const isLeaderMode = computed(() => route.path.startsWith('/leader/'))
const loading = ref(false)
const errorMessage = ref('')
const weekRows = ref<DayPlan[]>([])
const today = dateValue(new Date())
const activeWeekStart = ref(mondayOf(today))
const selectedDate = ref(today)

const activeWeekEnd = computed(() => addDays(activeWeekStart.value, 6))
const isCurrentWeek = computed(() => activeWeekStart.value === mondayOf(today))
const planStatuses: Record<PlanStatus, { label: string; type: 'info' | 'warning' | 'success' | 'danger' }> = {
  DRAFT: { label: '草稿', type: 'info' },
  PENDING: { label: '待审批', type: 'warning' },
  APPROVED: { label: '已通过', type: 'success' },
  REJECTED: { label: '已驳回', type: 'danger' },
  PAUSED: { label: '已暂停', type: 'warning' },
  CANCELED: { label: '已撤销', type: 'danger' },
}
const todayPlan = computed(() => weekRows.value.find((row) => row.planDate === today))
const todayActionLabel = computed(() => !todayPlan.value ? '编制今天计划' : isEditableStatus(todayPlan.value.status) ? '继续编辑今天' : '查看今天计划')
const weekDays = computed(() => Array.from({ length: 7 }, (_, index) => {
  const date = addDays(activeWeekStart.value, index)
  return {
    date,
    weekday: weekdayLabel(date).replace('星期', '周'),
    plan: weekRows.value.find((row) => row.planDate === date),
  }
}))
const selectedDay = computed(() => weekDays.value.find((day) => day.date === selectedDate.value) ?? weekDays.value[0])
const selectedDayPlan = computed(() => selectedDay.value?.plan)
const selectedDateLabel = computed(() => selectedDate.value ? `${selectedDate.value.slice(5).replace('-', '/')} ${weekdayLabel(selectedDate.value).replace('星期', '周')}` : '--')
const selectedActionLabel = computed(() => !selectedDayPlan.value ? '编制当天计划' : isEditableStatus(selectedDayPlan.value.status) ? '继续编辑当天' : '查看当天计划')
const dayPreviewRows = computed(() => {
  const plan = selectedDayPlan.value
  if (!plan) return []
  return [{
    content: plan.content?.trim() || '暂无计划内容',
  }]
})

function dateValue(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function addDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00`)
  date.setDate(date.getDate() + days)
  return dateValue(date)
}

function mondayOf(value: string) {
  const date = new Date(`${value}T00:00:00`)
  const day = date.getDay() || 7
  date.setDate(date.getDate() - day + 1)
  return dateValue(date)
}

function weekdayLabel(value: string) {
  return ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][new Date(`${value}T00:00:00`).getDay()]
}

function planStatusMeta(status: PlanStatus) {
  return planStatuses[status] ?? { label: status, type: 'info' as const }
}

function isEditableStatus(status: PlanStatus) {
  return status === 'DRAFT' || status === 'REJECTED'
}

function selectDate(date: string) {
  selectedDate.value = date
}

function openDate(date: string) {
  void router.push({ path: isLeaderMode.value ? '/leader/my-day-plans/edit' : '/employee/daily-plan', query: { date } })
}

function moveWeek(days: number) {
  activeWeekStart.value = addDays(activeWeekStart.value, days)
  selectedDate.value = activeWeekStart.value
  void loadWeek()
}

function goCurrentWeek() {
  activeWeekStart.value = mondayOf(today)
  selectedDate.value = today
  void loadWeek()
}

async function loadWeek() {
  loading.value = true
  errorMessage.value = ''
  try {
    weekRows.value = await listDayPlansApi({ startDate: activeWeekStart.value, endDate: activeWeekEnd.value, mine: true })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '本周日计划状态加载失败'
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await loadWeek()
}

onMounted(refreshAll)
</script>

<style scoped>
.day-plan-list__header {
  align-items: flex-end;
}

.day-week-browser {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
}

.day-week-browser__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 14px;
}

.day-week-browser__head > div:first-child {
  display: grid;
  gap: 4px;
}

.day-week-browser__head span {
  color: var(--blue);
  font-size: 11px;
  font-weight: 800;
}

.day-week-browser__head strong {
  font-size: 16px;
}

.day-week-browser__head small {
  color: var(--muted);
  font-size: 11px;
}

.day-week-browser__nav {
  display: flex;
  align-items: center;
  gap: 8px;
}

.day-week-scroll {
  overflow-x: auto;
  padding-bottom: 2px;
  scrollbar-width: thin;
}

.day-week-track {
  display: grid;
  grid-template-columns: repeat(7, minmax(104px, 1fr));
  gap: 8px;
  min-width: 770px;
}

.day-week-track button {
  position: relative;
  display: grid;
  grid-template-rows: auto auto auto minmax(32px, auto);
  gap: 5px;
  min-height: 124px;
  padding: 12px 10px 11px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 7px;
  color: var(--muted);
  background: #fbfcfb;
  text-align: left;
  cursor: pointer;
  transition: border-color .16s ease, background-color .16s ease, transform .16s ease;
}

.day-week-track button:hover,
.day-week-track button:focus-visible {
  border-color: #8fb5a5;
  background: #f7faf8;
  outline: none;
  transform: translateY(-1px);
}

.day-week-track button.is-today {
  border-color: var(--blue);
  background: #eef7f2;
  box-shadow: inset 0 0 0 1px rgb(45 119 108 / 10%);
}

.day-week-track button.is-selected {
  border-color: var(--blue);
  background: #eef7f2;
  box-shadow: inset 0 0 0 1px var(--blue);
}

.day-week-track button.has-plan {
  background: #fff;
}

.day-week-track button.has-plan.is-selected {
  background: #eef7f2;
}

.day-week-track span,
.day-week-track small {
  font-size: 10px;
}

.day-week-track strong {
  color: var(--ink);
  font: 800 25px/1 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.day-week-track small {
  color: var(--blue);
  font-weight: 700;
}

.day-week-track em {
  display: -webkit-box;
  overflow: hidden;
  color: var(--ink);
  font-size: 11px;
  font-style: normal;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.day-week-track i {
  position: absolute;
  right: 9px;
  top: 10px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cdd5d1;
}

.day-week-track .is-pending i { background: #c28a2e; }
.day-week-track .is-approved i { background: var(--green); }
.day-week-track .is-rejected i { background: #b9573f; }
.day-week-track .is-paused i { background: #9a7b3a; }
.day-week-track .is-canceled i { background: #8d9692; }

.day-record-section {
  margin-top: 22px;
}

.day-record-section__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.day-record-section__head h2 {
  margin: 0;
  font-size: 18px;
}

.day-record-section__head p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.day-record-table-wrap {
  min-height: 180px;
  margin-top: 12px;
}

.day-record-table {
  width: 100%;
  min-width: 720px;
  border-collapse: collapse;
  table-layout: fixed;
  color: #263a34;
  font-size: 13px;
}

.day-record-table th,
.day-record-table td {
  padding: 12px 10px;
  border-right: 1px solid #cfd9d3;
  border-bottom: 1px solid #d9e1dc;
  vertical-align: middle;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.day-record-table th:last-child,
.day-record-table td:last-child {
  border-right: 0;
}

.day-record-table th {
  color: #304a42;
  background: #e6f0f6;
  font-weight: 750;
  text-align: center;
}

.preview-name {
  width: 110px;
}

.preview-content {
  width: auto;
}

.preview-name-cell {
  text-align: center;
}

.preview-text {
  margin: 0;
  line-height: 1.65;
  white-space: pre-wrap;
}

.day-record-empty {
  display: grid;
  min-height: 240px;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: var(--muted);
  text-align: center;
  border: 1px solid var(--line);
  background: #fff;
}

.day-record-empty strong {
  color: var(--ink);
}

@media (max-width: 760px) {
  .day-plan-list__header,
  .day-week-browser__head {
    align-items: flex-start;
    flex-direction: column;
  }

  .day-plan-list__header .toolbar {
    width: 100%;
  }

  .day-plan-list__header .toolbar .el-button:last-child {
    flex: 1;
  }

  .day-week-browser {
    padding: 14px;
  }

  .day-week-browser__nav {
    width: 100%;
    justify-content: space-between;
  }

  .day-week-track {
    grid-template-columns: repeat(7, 112px);
    min-width: max-content;
  }
}

@media (prefers-reduced-motion: reduce) {
  .day-week-track button {
    transition: none;
  }
}
</style>
