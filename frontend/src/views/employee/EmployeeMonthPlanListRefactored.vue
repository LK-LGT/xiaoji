<template>
  <section class="page-panel employee-month-page">
    <div v-if="isLeaderContext" class="page-header">
      <div>
        <h1 class="page-title">{{ isLeaderContext ? '我的月计划' : '月计划' }}</h1>
        <p class="page-subtitle">按月编制计划、跟进审批并推进成果提交。</p>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="loading" aria-label="刷新月计划" @click="loadAll">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="goCreateSelectedMonth">
          {{ selectedPlan ? '打开月计划' : '新建月计划' }}
        </el-button>
      </div>
    </div>

    <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />

    <section class="employee-month-browser mt16">
      <div class="employee-month-browser__head">
        <div>
          <h2>选择月份</h2>
          <p>最近三个月、本月和未来三个月的计划状态。</p>
        </div>
        <div class="toolbar">
          <el-date-picker v-model="currentMonth" type="month" value-format="YYYY-MM" placeholder="选择月份" />
          <template v-if="!isLeaderContext">
            <el-button :icon="Refresh" :loading="loading" aria-label="刷新月计划" @click="loadAll">刷新</el-button>
            <el-button type="primary" :icon="Plus" @click="goCreateSelectedMonth">
              {{ selectedPlan ? '打开月计划' : '新建月计划' }}
            </el-button>
          </template>
        </div>
      </div>
      <div class="employee-month-browser__list" aria-label="月份导航">
        <button
          v-for="month in monthCards"
          :key="month.month"
          type="button"
          class="employee-month-card"
          :class="{
            'is-active': month.month === currentMonth,
            'is-current': month.isCurrent,
            'is-empty': !month.hasPlan,
          }"
          :aria-pressed="month.month === currentMonth"
          @click="selectMonth(month.month)"
        >
          <span class="employee-month-card__label">
            {{ month.month === currentMonth ? '已选择' : month.isCurrent ? '本月' : '月份' }}
          </span>
          <strong>{{ month.month.slice(5) }}<small>月</small></strong>
          <span class="employee-month-card__year">{{ month.month.slice(0, 4) }}</span>
          <span class="employee-month-card__meta">{{ month.title }}</span>
          <el-tag size="small" :type="month.statusType">{{ month.statusLabel }}</el-tag>
        </button>
      </div>
    </section>

    <section v-loading="loading" class="employee-month-workspace mt16">
      <template v-if="selectedPlanDetail">
        <header class="employee-month-workspace__head">
          <div class="employee-month-workspace__identity">
            <span class="employee-month-workspace__kicker">本月行动摘要</span>
            <h2>{{ selectedPlan?.title || `${selectedPlanDetail.planMonth} 月计划` }}</h2>
            <div class="employee-month-workspace__summary-meta">
              <div class="employee-month-workspace__status">
                <el-tag :type="getPlanStatus(selectedPlanDetail.status).type">计划：{{ getPlanStatus(selectedPlanDetail.status).label }}</el-tag>
                <el-tag :type="getResultStatus(selectedPlanDetail.resultStatus).type">成果：{{ getResultStatus(selectedPlanDetail.resultStatus).label }}</el-tag>
                <PlanAiReviewCell :review="aiReviews.reviewFor(selectedPlanDetail.id)" :loading="aiReviews.loading.value" @view="openAiReview" />
              </div>
              <span class="employee-month-workspace__hint">{{ primaryActionHint }}</span>
            </div>
          </div>
          <div class="employee-month-workspace__actions">
            <div class="employee-month-workspace__action-buttons">
              <el-button v-if="primaryActionLabel" type="primary" @click="runPrimaryAction">{{ primaryActionLabel }}</el-button>
              <el-button v-if="selectedPlanDetail.status === 'submitted'" type="warning" plain @click="withdrawSelectedPlan">撤回计划</el-button>
              <EmployeeMonthPlanSupportActions :detail="selectedPlanDetail" />
            </div>
          </div>
        </header>

        <div class="employee-month-workspace__metrics">
          <div><span>计划任务</span><strong>{{ taskRows.length }}</strong><small>项</small></div>
          <div><span>绩效权重</span><strong>{{ totalWeight }}</strong><small>%</small></div>
          <div v-if="!isLeaderContext"><span>已提交成果</span><strong>{{ submittedTaskCount }}</strong><small>项</small></div>
          <div v-else><span>审批状态</span><strong class="status-value">{{ getPlanStatus(selectedPlanDetail.status).label }}</strong></div>
          <div><span>整体完成</span><strong>{{ selectedPlanDetail.resultSummary.overallCompletionRate }}</strong><small>%</small></div>
        </div>

        <section class="employee-month-tasks">
          <div class="employee-month-tasks__head">
            <div>
              <h2>本月任务</h2>
              <p>{{ isLeaderContext ? '查看本月全部任务与审批状态' : '当前页面直接展示全部任务、期限和成果状态' }}</p>
            </div>
          </div>

          <template v-if="taskRows.length">
            <div class="employee-month-readonly-table-wrap">
              <el-table
                :data="taskRows"
                row-key="id"
                border
                table-layout="auto"
                class="employee-month-readonly-table"
                :class="{ 'has-actions': showTaskActionColumn }"
                aria-label="月计划任务预览表"
              >
                <el-table-column label="序号" width="56" align="center">
                  <template #default="{ $index }"><span class="readonly-task-index">{{ String($index + 1).padStart(2, '0') }}</span></template>
                </el-table-column>
                <el-table-column label="工作计划内容" min-width="300">
                  <template #default="{ row }">
                    <MonthPlanTaskContentView class="readonly-task-text readonly-task-text--primary" :content="row.taskContent" />
                  </template>
                </el-table-column>
                <el-table-column label="权重（%）" width="86" align="right">
                  <template #default="{ row }"><span class="readonly-task-number">{{ formatTableNumber(row.performanceWeight) }}</span></template>
                </el-table-column>
                <el-table-column label="考核标准（交付物、质量）" min-width="320">
                  <template #default="{ row }"><p class="readonly-task-text readonly-task-standard">{{ formatMonthPlanStandard(row.deliverable, row.quality) }}</p></template>
                </el-table-column>
                <el-table-column label="完成时间段" width="126" align="center">
                  <template #default="{ row }">
                    <span class="readonly-task-period">{{ formatMonthPlanPeriod(row.estimatedStartDate, row.estimatedEndDate) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="预估价值" width="112" align="right">
                  <template #default="{ row }"><span class="readonly-task-number">{{ formatTableNumber(row.estimatedValue) }}</span></template>
                </el-table-column>
                <el-table-column v-if="showTaskActionColumn" label="成果" width="128" align="center">
                  <template #default="{ row }">
                    <div class="readonly-task-result">
                      <el-tag size="small" :type="getTaskResultMeta(row).type">{{ getTaskResultMeta(row).label }}</el-tag>
                      <el-button v-if="canSubmitTaskResult(row)" link type="primary" @click="goResultSubmit(row.id)">
                        {{ row.latestResult?.status === 'rejected' ? '重新提交' : '提交成果' }}
                      </el-button>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
            </div>

          </template>
          <div v-else class="employee-month-workspace__empty compact">
            <strong>当前计划还没有任务</strong>
            <p>使用上方主操作进入编辑页，补充本月任务内容。</p>
          </div>
        </section>
      </template>

      <div v-else class="employee-month-workspace__empty">
        <span>{{ currentMonth }}</span>
        <strong>本月尚未创建计划</strong>
        <p>{{ canCreateSelectedMonth ? '创建月计划后，任务和成果提交情况将在这里显示。' : '历史月份没有可查看的月计划记录。' }}</p>
        <el-button v-if="canCreateSelectedMonth" type="primary" @click="goCreateSelectedMonth">新建月计划</el-button>
      </div>
    </section>

    <PlanAiReviewDrawer
      v-model="aiReviewDrawerVisible"
      plan-type="MONTH"
      :review="selectedPlanDetail ? aiReviews.reviewFor(selectedPlanDetail.id) : null"
      :loading="aiReviews.loading.value"
      :subject="selectedPlanDetail ? `${selectedPlanDetail.planMonth} 月计划` : ''"
      :context="selectedPlanDetail ? [selectedPlanDetail.employeeName, selectedPlanDetail.departmentName].filter(Boolean).join(' · ') : ''"
    />
  </section>
</template>

<script setup lang="ts">
import { Plus, Refresh } from '@element-plus/icons-vue'
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { listMonthPlansApi, type MonthPlan } from '@/api/planning'
import {
  getEmployeeMonthPlanDetailApi,
  getEmployeeResultSubmitOptionsApi,
  withdrawEmployeeMonthPlanApi,
  type EmployeeMonthPlanDetailResp,
  type EmployeeMonthPlanItem,
  type EmployeePlanStatus,
  type EmployeeResultStatus,
  type EmployeeResultSubmitOptionsResp,
} from '@/api/employee'
import { currentMonth as getCurrentMonth } from '@/api/performance'
import { usePersonalMonthPlanRoutes } from '@/composables/usePersonalMonthPlanRoutes'
import EmployeeMonthPlanSupportActions from './components/EmployeeMonthPlanSupportActions.vue'
import PlanAiReviewCell from '@/components/PlanAiReviewCell.vue'
import PlanAiReviewDrawer from '@/components/PlanAiReviewDrawer.vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import { useAiReviewCollection } from '@/composables/useAiReviewCollection'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'

const router = useRouter()
const route = useRoute()
const { isLeaderContext, editPath } = usePersonalMonthPlanRoutes()
const loading = ref(false)
const errorMessage = ref('')
const requestedMonth = Array.isArray(route.query.month) ? route.query.month[0] : route.query.month
const currentMonth = ref(typeof requestedMonth === 'string' && /^\d{4}-\d{2}$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
const allPlans = ref<MonthPlan[]>([])
const selectedPlanDetail = ref<EmployeeMonthPlanDetailResp | null>(null)
const resultVersions = ref<EmployeeResultSubmitOptionsResp['resultVersions']>([])
const aiReviews = useAiReviewCollection('MONTH_PLAN')
const aiReviewDrawerVisible = ref(false)
async function openAiReview() {
  if (!selectedPlanDetail.value) return
  aiReviewDrawerVisible.value = true
  await aiReviews.loadOne(selectedPlanDetail.value.id)
}

const planStatusMap = {
  draft: { label: '草稿', type: 'info' }, submitted: { label: '已提交', type: 'warning' }, approved: { label: '已通过', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' }, paused: { label: '已暂停', type: 'warning' }, canceled: { label: '已撤销', type: 'danger' },
  confirmed: { label: '已确认', type: 'success' }, archived: { label: '已归档', type: 'info' },
} as const
const resultStatusMap = {
  not_submitted: { label: '未提交', type: 'info' }, draft: { label: '草稿', type: 'info' }, submitted: { label: '待确认', type: 'warning' },
  confirmed: { label: '已确认', type: 'success' }, rejected: { label: '已驳回', type: 'danger' },
} as const

type MonthCard = {
  /** BUG-R6-14：主键为字符串（UUID/十六进制）。 */
  id?: string
  month: string
  title: string
  isCurrent: boolean
  hasPlan: boolean
  statusLabel: string
  statusType: 'info' | 'warning' | 'success' | 'danger'
}

function shiftMonth(month: string, offset: number) {
  const [year, monthNumber] = month.split('-').map(Number)
  const date = new Date(year, monthNumber - 1 + offset, 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

const monthCards = computed<MonthCard[]>(() => {
  const calendarMonth = getCurrentMonth()
  const planByMonth = new Map<string, MonthPlan>()
  allPlans.value.forEach((plan) => {
    if (plan.status === 'CANCELED') return
    if (!planByMonth.has(plan.planMonth)) planByMonth.set(plan.planMonth, plan)
  })

  return Array.from({ length: 7 }, (_, index) => {
    const month = shiftMonth(currentMonth.value, index - 3)
    const plan = planByMonth.get(month)
    const status = plan ? planStatusMap[toEmployeeStatus(plan.status)] : planStatusMap.draft
    return {
      id: plan?.id,
      month,
      title: plan?.title || '该月暂无计划',
      isCurrent: month === calendarMonth,
      hasPlan: Boolean(plan),
      statusLabel: plan ? status.label : '未创建',
      statusType: plan ? status.type : 'info',
    }
  })
})

const selectedPlan = computed(() => allPlans.value.find((plan) => (
  plan.planMonth === currentMonth.value && plan.status !== 'CANCELED'
)))
const canCreateSelectedMonth = computed(() => currentMonth.value >= getCurrentMonth())
type TaskResultVersion = EmployeeResultSubmitOptionsResp['resultVersions'][number]
type TaskRow = EmployeeMonthPlanItem & { latestResult?: TaskResultVersion }
const taskRows = computed<TaskRow[]>(() => (selectedPlanDetail.value?.items ?? [])
  .map((item) => ({
    ...item,
    latestResult: resultVersions.value.find((result) => result.monthPlanItemId === item.id),
  })))
const totalWeight = computed(() => Number(taskRows.value.reduce((sum, item) => sum + Number(item.performanceWeight || 0), 0).toFixed(2)))
const submittedTaskCount = computed(() => taskRows.value.filter((item) => Boolean(item.latestResult)).length)
const showTaskActionColumn = computed(() => !isLeaderContext.value
  && taskRows.value.some((item) => Boolean(item.latestResult) || canSubmitTaskResult(item)))
const primaryActionLabel = computed(() => {
  const status = selectedPlanDetail.value?.status
  if (status === 'draft' || status === 'rejected') return '编辑'
  return ''
})
const primaryActionHint = computed(() => {
  const status = selectedPlanDetail.value?.status
  if (status === 'draft') return '补齐任务内容后提交审批'
  if (status === 'rejected') return '根据审批意见修改并重新提交'
  if (status === 'submitted') return `当前计划正在等待${isLeaderContext.value ? '部门负责人' : '直属领导'}审批`
  if (status === 'approved') return '计划已通过，可直接在下方查看任务或提交成果'
  return '当前计划已完成流程处理'
})

function toEmployeeStatus(status: string): EmployeePlanStatus {
  return ({ DRAFT: 'draft', PENDING: 'submitted', APPROVED: 'approved', REJECTED: 'rejected', PAUSED: 'paused', CANCELED: 'canceled' } as Record<string, EmployeePlanStatus>)[status] || 'draft'
}
function getPlanStatus(status: EmployeePlanStatus) { return planStatusMap[status] }
function getResultStatus(status: EmployeeResultStatus) { return resultStatusMap[status] }
function canEdit(status: EmployeePlanStatus) { return status === 'draft' || status === 'rejected' }
function selectMonth(month: string) { currentMonth.value = month }
function goEdit(id?: string) { router.push(editPath(id)) }
function goCreateSelectedMonth() {
  if (selectedPlan.value) return goEdit(selectedPlan.value.id)
  router.push({ path: editPath(), query: { planMonth: currentMonth.value } })
}
function runPrimaryAction() {
  if (!selectedPlanDetail.value) return
  if (canEdit(selectedPlanDetail.value.status)) return goEdit(selectedPlanDetail.value.id)
}
function getTaskResultMeta(item: TaskRow) {
  return item.latestResult ? getResultStatus(item.latestResult.status) : resultStatusMap.not_submitted
}
function formatTableNumber(value: number) {
  return Number(value || 0).toFixed(2)
}
function canSubmitTaskResult(item: TaskRow) {
  return selectedPlanDetail.value?.status === 'approved'
    && item.status === 'approved'
    && (!item.latestResult || item.latestResult.status === 'rejected')
}
function goResultSubmit(itemId: string) {
  if (!selectedPlanDetail.value) return
  router.push({
    path: `/employee/results/workspace/MONTH/${selectedPlanDetail.value.id}`,
    query: { month: selectedPlanDetail.value.planMonth, taskId: String(itemId) },
  })
}

async function withdrawSelectedPlan() {
  if (!selectedPlanDetail.value) return
  try {
    await ElMessageBox.confirm('撤回后月计划恢复为草稿，可修改后重新提交。确认撤回？', '撤回月计划', { type: 'warning' })
    await withdrawEmployeeMonthPlanApi(selectedPlanDetail.value.id)
    ElMessage.success('月计划已撤回为草稿')
    await loadAll()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '撤回失败')
      await loadAll()
    }
  }
}

async function loadMonth() {
  loading.value = true
  errorMessage.value = ''
  selectedPlanDetail.value = null
  resultVersions.value = []
  try {
    if (!selectedPlan.value) return
    const [detail, options] = await Promise.all([
      getEmployeeMonthPlanDetailApi(selectedPlan.value.id),
      isLeaderContext.value ? Promise.resolve({ resultVersions: [] }) : getEmployeeResultSubmitOptionsApi(),
      aiReviews.loadOne(selectedPlan.value.id),
    ])
    selectedPlanDetail.value = detail
    resultVersions.value = options.resultVersions.filter((result) => result.monthPlanId === detail.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '月计划加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function loadAll() {
  loading.value = true
  errorMessage.value = ''
  selectedPlanDetail.value = null
  resultVersions.value = []
  try {
    const allRequest = listMonthPlansApi({ mine: true }).then((plans) => plans ?? [])
    const currentRequest = allRequest.then(async (plans) => {
      const plan = plans.find((item) => item.planMonth === currentMonth.value)
      if (!plan) return null
      const [detail, options] = await Promise.all([
        getEmployeeMonthPlanDetailApi(plan.id),
        isLeaderContext.value ? Promise.resolve({ resultVersions: [] }) : getEmployeeResultSubmitOptionsApi(),
      ])
      return { detail, options }
    })
    const [all, current] = await Promise.all([allRequest, currentRequest])
    allPlans.value = all
    await aiReviews.load(all.map((plan) => plan.id))
    selectedPlanDetail.value = current?.detail ?? null
    resultVersions.value = current
      ? current.options.resultVersions.filter((result) => result.monthPlanId === current.detail.id)
      : []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '月计划加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}
watch(currentMonth, loadMonth)
onMounted(loadAll)
</script>
