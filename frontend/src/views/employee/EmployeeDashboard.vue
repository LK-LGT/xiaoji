<template>
  <section class="page-panel employee-dashboard employee-workbench">
    <el-alert
      v-if="errorMessage"
      class="dashboard-alert"
      type="warning"
      :closable="false"
      show-icon
      :title="errorMessage"
    />

    <section class="employee-workbench-layout">
      <section class="workbench-action-board" aria-label="当前待处理事项">
        <div class="workbench-action-board__head">
          <div>
            <span>当前工作</span>
            <h2>{{ selectedDateText }}</h2>
            <p>{{ currentGroup || '未配置归属组织' }} · {{ currentMonth }} 月度周期</p>
          </div>
          <el-button :icon="RefreshRight" :loading="loading" @click="loadDashboard">刷新</el-button>
        </div>

        <div class="workbench-action-list">
          <button
            v-for="action in actionItems"
            :key="action.key"
            type="button"
            class="workbench-action-card"
            :class="{ 'is-urgent': action.urgent }"
            @click="action.action"
          >
            <span class="workbench-action-card__icon"><el-icon><component :is="action.icon" /></el-icon></span>
            <span class="workbench-action-card__copy">
              <strong>{{ action.title }}</strong>
              <em>{{ action.desc }}</em>
            </span>
            <b>{{ action.label }}</b>
            <el-icon class="workbench-row-arrow"><Right /></el-icon>
          </button>
        </div>

        <div class="workbench-light-tools" aria-label="辅助入口">
          <button type="button" @click="router.push('/employee/performance-evidence')">
            <el-icon><DataAnalysis /></el-icon>
            <span>绩效依据</span>
          </button>
          <button type="button" @click="router.push('/employee/appeals')">
            <el-icon><ChatLineSquare /></el-icon>
            <span>申诉记录</span>
            <strong v-if="summary.openAppealCount">{{ summary.openAppealCount }}</strong>
          </button>
        </div>
      </section>
      <aside class="employee-workbench-tools" aria-label="工作台概览与快捷操作">
        <section class="workbench-month-display" aria-label="当前月份">
          <span>当前月份</span>
          <div class="workbench-month-display__value">
            <strong>{{ monthDisplay.month }}</strong>
            <div>
              <b>{{ monthDisplay.year }}</b>
              <small>{{ currentGroup || '未配置归属组织' }}</small>
            </div>
          </div>
        </section>

        <section class="workbench-tool-section workbench-todo-section">
          <div class="workbench-tool-section__head">
            <span>待处理</span>
            <strong>{{ todoCount }}</strong>
          </div>
          <div v-if="todoCount" class="workbench-todo-stack">
            <button
              v-for="todo in todoItems"
              :key="todo.key"
              type="button"
              class="workbench-todo-card"
              :class="{ 'is-empty': todo.count === 0 }"
              @click="todo.action"
            >
              <span class="workbench-todo-card__icon"><el-icon><component :is="todo.icon" /></el-icon></span>
              <span class="workbench-todo-card__copy">
                <strong>{{ todo.title }}</strong>
                <em>{{ todo.desc }}</em>
              </span>
              <b>{{ todo.count }}</b>
              <el-icon class="workbench-row-arrow"><Right /></el-icon>
            </button>
          </div>
          <div v-else class="workbench-empty-todos">
            <el-icon><CircleCheck /></el-icon>
            <span>当前无待处理事项</span>
          </div>
        </section>

        <section class="workbench-tool-section workbench-entry-section">
          <div class="workbench-tool-section__head">
            <span>快捷入口</span>
          </div>
          <div class="workbench-entry-grid">
            <button class="workbench-entry-card" type="button" @click="goDayPlanEdit(formatDate(selectedDate))">
              <el-icon><EditPen /></el-icon>
              <strong>编制日计划</strong>
            </button>
            <el-tooltip :disabled="Boolean(resultSubmitPlan)" content="当前没有可提交成果" placement="top">
              <span class="workbench-entry-tooltip">
                <button class="workbench-entry-card" type="button" :disabled="!resultSubmitPlan" @click="resultSubmitPlan && goResultSubmit(resultSubmitPlan.id)">
                  <el-icon><UploadFilled /></el-icon>
                  <strong>提交成果</strong>
                </button>
              </span>
            </el-tooltip>
            <button class="workbench-entry-card" type="button" @click="router.push('/employee/performance-evidence')">
              <el-icon><DataAnalysis /></el-icon>
              <strong>绩效依据</strong>
            </button>
            <button class="workbench-entry-card" type="button" @click="router.push('/employee/appeals')">
              <el-icon><ChatLineSquare /></el-icon>
              <strong>申诉记录</strong>
            </button>
          </div>
        </section>
      </aside>

      <div class="employee-workbench-main">
        <section class="dashboard-section workbench-calendar-stage">
          <div class="workbench-calendar-scroll">
            <el-calendar v-model="selectedDate">
              <template #header>
                <div class="workbench-calendar-toolbar">
                  <div class="workbench-calendar-toolbar__intro">
                    <div>
                      <h2>日计划日历</h2>
                      <p>选择日期查看并处理当日计划</p>
                    </div>
                    <div class="workbench-calendar-stage__summary">
                      <span>月计划 {{ summary.monthPlanCount }}</span>
                      <span>已提交成果 {{ summary.submittedResultCount }}</span>
                      <span>平均完成率 {{ summary.averageCompletionRate }}%</span>
                    </div>
                  </div>
                  <div class="workbench-month-controls">
                    <el-tooltip content="上个月" placement="top">
                      <el-button circle aria-label="上个月" :disabled="loading" @click="changeMonth(-1)">
                        <el-icon><ArrowLeft /></el-icon>
                      </el-button>
                    </el-tooltip>
                    <el-date-picker
                      v-model="currentMonth"
                      type="month"
                      value-format="YYYY-MM"
                      aria-label="选择月份"
                      :disabled="loading"
                    />
                    <el-tooltip content="下个月" placement="top">
                      <el-button circle aria-label="下个月" :disabled="loading" @click="changeMonth(1)">
                        <el-icon><ArrowRight /></el-icon>
                      </el-button>
                    </el-tooltip>
                    <el-tooltip content="刷新工作台" placement="top">
                      <el-button circle aria-label="刷新工作台" :loading="loading" @click="loadDashboard">
                        <el-icon><RefreshRight /></el-icon>
                      </el-button>
                    </el-tooltip>
                  </div>
                </div>
              </template>
              <template #date-cell="{ data }">
                <button
                  class="calendar-cell"
                  :class="{
                    'has-plan': Boolean(dayPlanMap[data.day]),
                    'is-non-required': workdayMap[data.day] && !workdayMap[data.day].forceReport,
                    'is-missing-required': workdayMap[data.day]?.missingRequired,
                  }"
                  type="button"
                  @click="selectCalendarDate(data.day)"
                  @dblclick.stop="openDayPlanDialog(data.day)"
                >
                  <span>{{ data.day.split('-').pop() }}</span>
                  <small
                    v-if="dayPlanMap[data.day]"
                    :class="['calendar-dot', `status-${dayPlanMap[data.day]}`]"
                    :title="getPlanStatus(dayPlanMap[data.day]).label"
                  >
                    {{ getPlanStatus(dayPlanMap[data.day]).label }}
                  </small>
                  <small
                    v-else-if="calendarCellLabel(data.day)"
                    :class="['calendar-dot', calendarCellClass(data.day)]"
                    :title="calendarCellLabel(data.day)"
                  >
                    {{ calendarCellLabel(data.day) }}
                  </small>
                </button>
              </template>
            </el-calendar>
          </div>
        </section>
      </div>
    </section>

    <el-dialog
      v-model="dayPlanDialogVisible"
      :title="`${dayPlanDialogDate} 日计划`"
      width="min(1120px, 94vw)"
      destroy-on-close
      append-to-body
      @closed="handleDayPlanDialogClosed"
    >
      <EmployeeDayPlanEditor
        v-if="dayPlanDialogDate"
        :date="dayPlanDialogDate"
        compact
        @changed="handleDayPlanChanged"
      />
      <template #footer>
        <el-button @click="openFullDayPlanPage">打开完整页面</el-button>
        <el-button type="primary" @click="dayPlanDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowLeft,
  ArrowRight,
  Calendar,
  ChatLineSquare,
  CircleCheck,
  DataAnalysis,
  Document,
  EditPen,
  RefreshRight,
  Right,
  UploadFilled,
  Warning,
} from '@element-plus/icons-vue'
import EmployeeDayPlanEditor from './components/EmployeeDayPlanEditor.vue'
import {
  getEmployeeDashboardApi,
  type EmployeeDashboardResp,
  type EmployeeMonthPlan,
  type EmployeePlanStatus,
} from '@/api/employee'
import { currentMonth as getCurrentMonth } from '@/api/performance'
import { periodMonthKey } from '@/utils/period'

const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')
const currentMonth = ref(getCurrentMonth())
const currentGroup = ref('')
const selectedDate = ref(new Date())
const dayPlanDialogVisible = ref(false)
const dayPlanDialogDate = ref('')
const monthPlans = ref<EmployeeMonthPlan[]>([])
const dayPlanCalendar = ref<EmployeeDashboardResp['dayPlanCalendar']>([])
const workdayCalendar = ref<EmployeeDashboardResp['workdayCalendar']>([])
const summary = reactive<EmployeeDashboardResp['summary']>({
  monthPlanCount: 0,
  submittedResultCount: 0,
  averageCompletionRate: 0,
  openAppealCount: 0,
  missingRequiredDayPlanCount: 0,
})

const planStatusMap = {
  draft: { label: '草稿', type: 'info' },
  submitted: { label: '已提交', type: 'warning' },
  approved: { label: '已通过', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' },
  paused: { label: '已暂停', type: 'warning' },
  canceled: { label: '已撤销', type: 'danger' },
  confirmed: { label: '已确认', type: 'success' },
  archived: { label: '已归档', type: 'info' },
} as const

const pendingPlanCount = computed(() => monthPlans.value.filter((item) => item.planStatus === 'draft' || item.planStatus === 'rejected').length)
const dayPlanMap = computed<Record<string, EmployeePlanStatus>>(() => Object.fromEntries(dayPlanCalendar.value.map((item) => [item.date, item.status])))
const workdayMap = computed<Record<string, EmployeeDashboardResp['workdayCalendar'][number]>>(() => Object.fromEntries(workdayCalendar.value.map((item) => [item.date, item])))
const selectedDateText = computed(() => formatDate(selectedDate.value))
const todayText = computed(() => formatDate(new Date()))
const todayPlanStatus = computed(() => dayPlanMap.value[todayText.value])
const currentMonthPlan = computed(() => monthPlans.value.find((item) => item.planMonth === currentMonth.value))
const pendingResultPlans = computed(() => monthPlans.value.filter((item) =>
  item.planStatus === 'approved'
  && (item.resultStatus === 'not_submitted' || item.resultStatus === 'rejected')))
const resultSubmitPlan = computed(() => pendingResultPlans.value[0])
const todoCount = computed(() => pendingPlanCount.value
  + pendingResultPlans.value.length
  + summary.openAppealCount
  + summary.missingRequiredDayPlanCount)
const todoItems = computed(() => [
  {
    key: 'day-plan',
    title: '日计划',
    icon: Calendar,
    count: summary.missingRequiredDayPlanCount,
    desc: summary.missingRequiredDayPlanCount ? '本月应填未填' : '本月无缺报',
    action: goFirstMissingDay,
  },
  {
    key: 'result',
    title: '成果',
    icon: Document,
    count: pendingResultPlans.value.length,
    desc: '待提交或待补证据',
    action: () => pendingResultPlans.value[0] && goResultSubmit(pendingResultPlans.value[0].id),
  },
  {
    key: 'appeal',
    title: '申诉',
    icon: Warning,
    count: summary.openAppealCount,
    desc: '确认后 3 个自然日内处理',
    action: () => router.push('/employee/appeals'),
  },
])
const monthDisplay = computed(() => {
  const [year, month] = currentMonth.value.split('-')
  return {
    year,
    month: `${Number(month)}月`,
  }
})
const actionItems = computed(() => [
  {
    key: 'today',
    title: '今天日计划',
    icon: Calendar,
    label: todayPlanStatus.value ? getPlanStatus(todayPlanStatus.value).label : '去填写',
    desc: todayPlanStatus.value ? '查看或继续处理今天的安排' : '今天还没有日计划记录',
    urgent: !todayPlanStatus.value,
    action: () => goDayPlanEdit(todayText.value),
  },
  {
    key: 'month',
    title: '本月月计划',
    icon: Document,
    label: currentMonthPlan.value ? getPlanStatus(currentMonthPlan.value.planStatus).label : '去新建',
    desc: currentMonthPlan.value ? (currentMonthPlan.value.title || '查看本月计划进度') : '本月还没有计划',
    urgent: !currentMonthPlan.value || currentMonthPlan.value.planStatus === 'draft' || currentMonthPlan.value.planStatus === 'rejected',
    action: openCurrentMonthPlan,
  },
  {
    key: 'result',
    title: '待提交成果',
    icon: UploadFilled,
    label: `${pendingResultPlans.value.length} 项`,
    desc: pendingResultPlans.value.length ? '有计划事项可以提交或补充成果' : '当前没有待提交成果',
    urgent: pendingResultPlans.value.length > 0,
    action: () => resultSubmitPlan.value ? goResultSubmit(resultSubmitPlan.value.id) : router.push('/employee/results'),
  },
])

function getPlanStatus(status: EmployeePlanStatus) {
  return planStatusMap[status]
}

async function loadDashboard() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await getEmployeeDashboardApi(currentMonth.value)
    monthPlans.value = data.monthPlans ?? []
    dayPlanCalendar.value = data.dayPlanCalendar ?? []
    workdayCalendar.value = data.workdayCalendar ?? []
    currentGroup.value = data.orgName ?? ''
    Object.assign(summary, data.summary ?? {
      monthPlanCount: monthPlans.value.length,
      submittedResultCount: monthPlans.value.filter((item) => item.resultStatus === 'submitted' || item.resultStatus === 'confirmed').length,
      averageCompletionRate: 0,
      openAppealCount: 0,
      missingRequiredDayPlanCount: 0,
    })
    if (data.currentMonth) {
      currentMonth.value = data.currentMonth
    }
  } catch (error) {
    monthPlans.value = []
    dayPlanCalendar.value = []
    workdayCalendar.value = []
    currentGroup.value = ''
    Object.assign(summary, {
      monthPlanCount: 0,
      submittedResultCount: 0,
      averageCompletionRate: 0,
      openAppealCount: 0,
      missingRequiredDayPlanCount: 0,
    })
    errorMessage.value = error instanceof Error ? error.message : '员工工作台加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function goDayPlanEdit(date?: string) {
  router.push({ path: '/employee/day-plans/edit', query: date ? { date } : undefined })
}

function openCurrentMonthPlan() {
  const plan = currentMonthPlan.value
  if (!plan) {
    router.push({ path: '/employee/month-plans/new/edit', query: { planMonth: currentMonth.value } })
    return
  }
  if (plan.planStatus === 'draft' || plan.planStatus === 'rejected') {
    router.push(`/employee/month-plans/${plan.id}/edit`)
    return
  }
  router.push({ path: '/employee/month-plans', query: { month: plan.planMonth } })
}

function goFirstMissingDay() {
  const missing = workdayCalendar.value.find((item) => item.missingRequired)
  goDayPlanEdit(missing?.date || selectedDateText.value)
}

function calendarCellLabel(day: string) {
  const rule = workdayMap.value[day]
  if (!rule) return ''
  if (rule.missingRequired) return '待填报'
  if (!rule.forceReport) return rule.ruleType === 'HOLIDAY' ? '节假日' : rule.ruleType === 'LEAVE' ? '请假' : '非强制'
  if (rule.explicit) return rule.ruleType === 'SPECIAL_SHIFT' ? '特殊排班' : rule.ruleType === 'BUSINESS_TRIP' ? '出差' : '需填报'
  return ''
}

function calendarCellClass(day: string) {
  const rule = workdayMap.value[day]
  return rule?.missingRequired ? 'status-missing' : rule?.forceReport ? 'status-required' : 'status-non-required'
}

function selectCalendarDate(day: string) {
  // ⚠️ **BUG-R6-26**：统一走 `periodMonthKey`（不再手写 `slice(0, 7)`）。
  const selectedMonth = periodMonthKey(day)
  if (selectedMonth !== currentMonth.value) currentMonth.value = selectedMonth
  selectedDate.value = new Date(`${day}T00:00:00`)
}

function openDayPlanDialog(day: string) {
  selectCalendarDate(day)
  dayPlanDialogDate.value = day
  dayPlanDialogVisible.value = true
}

async function handleDayPlanChanged() {
  await loadDashboard()
}

async function handleDayPlanDialogClosed() {
  await loadDashboard()
}

function openFullDayPlanPage() {
  const date = dayPlanDialogDate.value
  dayPlanDialogVisible.value = false
  goDayPlanEdit(date)
}

function changeMonth(offset: number) {
  const [year, month] = currentMonth.value.split('-').map(Number)
  const next = new Date(year, month - 1 + offset, 1)
  currentMonth.value = `${next.getFullYear()}-${`${next.getMonth() + 1}`.padStart(2, '0')}`
}

function goResultSubmit(monthPlanId: number) {
  router.push({ path: `/employee/results/workspace/MONTH/${monthPlanId}`, query: { month: currentMonth.value } })
}

function formatDate(date: Date) {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

watch(currentMonth, loadDashboard)
watch(currentMonth, (month) => {
  const requestedDay = selectedDate.value.getDate()
  const [year, monthNumber] = month.split('-').map(Number)
  const lastDay = new Date(year, monthNumber, 0).getDate()
  selectedDate.value = new Date(year, monthNumber - 1, Math.min(requestedDay, lastDay))
})
onMounted(loadDashboard)
</script>
