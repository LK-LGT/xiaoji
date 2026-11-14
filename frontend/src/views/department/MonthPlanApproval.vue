<template>
  <section v-loading="loading" class="page-panel">
    <div class="page-header">
      <div>
        <h1 class="page-title">月计划审批</h1>
        <p class="page-subtitle">{{ isLeader ? '审批提交给本人的下属月计划。' : '审批 Mentor、直属领导等提交给本人的月计划。' }}</p>
      </div>
      <div class="toolbar">
        <el-button :icon="HomeFilled" @click="router.push('/')">首页</el-button>
      </div>
    </div>

    <div class="status-strip approval-status-strip mt16" aria-label="计划提交统计">
      <span class="status-pill"><strong>{{ submittedCount }}/{{ totalEmployeeCount }}</strong> 提交/总人数</span>
      <span class="status-pill"><strong>{{ approvedCount }}/{{ totalEmployeeCount }}</strong> 已通过/总人数</span>
    </div>

    <div class="filter-bar approval-filter-bar" aria-label="月计划筛选">
      <span class="approval-filter-label">筛选</span>
      <el-date-picker v-model="query.year" type="year" value-format="YYYY" clearable placeholder="计划年份" />
      <el-select v-model="query.month" clearable placeholder="月份">
        <el-option v-for="item in monthOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select
        v-model="selectedOrgId"
        clearable
        :loading="orgLoading"
        :disabled="!orgOptions.length"
        placeholder="全部授权组织"
      >
        <el-option v-for="item in orgOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-select v-model="query.status" clearable placeholder="审批状态">
        <el-option label="待审批" value="待审批" />
        <el-option label="已通过" value="已通过" />
        <el-option label="已驳回" value="已驳回" />
      </el-select>
      <el-input v-model="query.keyword" clearable placeholder="员工/任务内容" />
      <el-button :icon="Search" @click="refresh()">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <div v-if="selectedRows.length" class="batch-bar approval-batch-bar">
      <div>已选择 <strong>{{ selectedRows.length }}</strong> 条月计划。</div>
      <div class="batch-actions">
        <el-button size="small" type="success" :loading="batchAction === 'approve'" :disabled="actionBusy && batchAction !== 'approve'" @click="batchApprove">批量通过</el-button>
        <el-button size="small" type="danger" :loading="batchAction === 'reject'" :disabled="actionBusy && batchAction !== 'reject'" @click="batchReject">批量驳回</el-button>
        <el-button size="small" :disabled="actionBusy" @click="clearSelection">清空选择</el-button>
      </div>
    </div>

    <el-table
      ref="tableRef"
      class="approval-list-table"
      :class="{ 'leader-compact-table': isLeader }"
      :data="tableRows"
      border
      highlight-current-row
      :row-class-name="rowClassName"
      @selection-change="onSelectionChange"
      @row-dblclick="openDetail"
      empty-text="暂无符合条件的月计划"
    >
      <el-table-column type="selection" width="48" :selectable="canSelect" />
      <el-table-column label="序号" width="62" align="center">
        <template #default="{ $index }">
          <span class="approval-row-index">{{ (currentPage - 1) * pageSize + $index + 1 }}</span>
        </template>
      </el-table-column>
      <el-table-column label="员工" min-width="156">
        <template #default="{ row }">
          <button type="button" class="plan-employee-button" @click.stop="openDetail(row)">
            <strong>{{ row.employee }}</strong>
            <span>{{ row.department || '未分配组织' }}</span>
            <small>{{ row.planNo }}</small>
          </button>
        </template>
      </el-table-column>
      <el-table-column label="计划周期" width="116">
        <template #default="{ row }">
          <div class="approval-period-cell">
            <strong>{{ row.month }}</strong>
            <span>{{ row.items.length }} 项任务</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="AI检查" width="156">
        <template #default="{ row }">
          <PlanAiReviewCell
            :review="aiReviews.reviewFor(row.id)"
            :loading="aiReviews.loading.value"
            @view="openAiReview(row)"
          />
        </template>
      </el-table-column>
      <el-table-column label="审批状态" min-width="164">
        <template #default="{ row }">
          <div class="approval-state-cell">
            <div class="approval-result__tags">
              <el-tag :type="statusTag(row.status)">{{ row.status }}</el-tag>
            </div>
            <span
              class="approval-comment-hint"
              :class="{ 'is-empty': !row.leaderComment }"
              :title="row.leaderComment || '暂未填写审批意见'"
            >
              <el-icon><ChatLineSquare /></el-icon>
              {{ row.leaderComment ? '已有审批意见' : '暂无审批意见' }}
            </span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="232" fixed="right">
        <template #default="{ row }">
          <div class="approval-actions" @click.stop>
            <el-button link type="primary" :icon="View" @click="openDetail(row)">查看</el-button>
            <template v-if="canProcess(row)">
              <el-tooltip
                :disabled="canApprove(row)"
                content="必要字段缺失，暂不能通过"
                placement="top"
              >
                <span>
                  <el-button link type="success" :loading="processingId === row.id && processingAction === 'approve'" :disabled="!canApprove(row) || actionBusy" @click="approve(row)">通过</el-button>
                </span>
              </el-tooltip>
              <el-button link type="danger" :loading="processingId === row.id && processingAction === 'reject'" :disabled="actionBusy" @click="reject(row)">驳回</el-button>
            </template>
            <el-dropdown class="approval-more" trigger="click" @command="handleMoreCommand">
              <el-button
                link
                type="primary"
                :icon="MoreFilled"
                aria-label="更多操作"
                :loading="downloadingTemplateId === row.id"
                :disabled="!!downloadingTemplateId"
              />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item :command="{ action: 'download', row }">
                    <el-icon><Download /></el-icon>
                    下载 Excel
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination-row approval-pagination">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next, jumper"
        :total="total"
        background
        @size-change="handlePageSizeChange"
        @current-change="handlePageChange"
      />
    </div>

    <el-drawer v-model="detailVisible" title="月计划详情" size="min(1040px, calc(100vw - 24px))" @closed="closeDetail">
      <div class="drawer-stack">
        <el-skeleton v-if="detailLoading" :rows="7" animated />
        <template v-else-if="activeRow">
        <section
          v-if="comparisonStatus === 'AVAILABLE' && rejectionBaseline"
          class="resubmission-review"
          aria-label="再次提交变更"
        >
          <header class="resubmission-review__heading">
            <div class="resubmission-review__title">
              <span>驳回复核</span>
              <h2>
                <b>V{{ rejectionBaseline.versionNo }}</b>
                <el-icon><Right /></el-icon>
                <b>V{{ activeRow.version }}</b>
              </h2>
              <p>{{ activeRow.employee }} · {{ activeRow.month }}</p>
            </div>
            <el-tag type="warning" effect="plain">再次提交</el-tag>
          </header>
          <dl class="rejection-context">
            <div><dt>上次驳回意见</dt><dd>{{ rejectionBaseline.rejectionComment || '未填写' }}</dd></div>
            <div><dt>驳回时间</dt><dd>{{ formatComparisonTime(rejectionBaseline.rejectedAt) }}</dd></div>
          </dl>
          <MonthPlanVersionDiff
            :diff="resubmissionDiff"
            heading="上次提交到本次提交"
            before-label="上次提交"
            after-label="本次提交"
            empty-text="本次提交与上次被驳回版本一致"
          />
        </section>
        <el-alert
          v-else-if="comparisonStatus === 'UNAVAILABLE'"
          class="comparison-unavailable"
          type="warning"
          :closable="false"
          show-icon
          title="变更对比暂不可用，仍可查看和审批当前计划"
        />
        <div
          v-if="comparisonStatus !== 'NONE'"
          class="current-plan-divider"
        >
          <span>当前提交完整内容</span>
          <p>以下为本次提交的完整计划，可继续核对并审批。</p>
        </div>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="计划编号">{{ activeRow.planNo }}</el-descriptions-item>
          <el-descriptions-item label="员工">{{ activeRow.employee }}</el-descriptions-item>
          <el-descriptions-item label="部门">{{ activeRow.department }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusTag(activeRow.status)">{{ activeRow.status }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="AI检查"><PlanAiReviewCell :review="aiReviews.reviewFor(activeRow.id)" :loading="aiReviews.loading.value" @view="openAiReview(activeRow)" /></el-descriptions-item>
          <el-descriptions-item label="审批意见"><div class="approval-text-cell compact">{{ activeRow.leaderComment || '待审批' }}</div></el-descriptions-item>
          <el-descriptions-item label="审批人">{{ activeRow.approverName }}</el-descriptions-item>
          <el-descriptions-item label="审批时间">{{ activeRow.approvedAt || '尚未处理' }}</el-descriptions-item>
        </el-descriptions>
        <div class="section-header mt16">
          <div>
            <h2>计划事项</h2>
            <p>逐项核对任务内容、考核标准、完成时间段和绩效权重。</p>
          </div>
        </div>
        <div v-if="activeRow.items.length" class="plan-item-list">
          <article v-for="item in activeRow.items" :key="item.id" class="plan-item-row">
            <header class="plan-item-row__header">
              <span class="plan-item-row__number">{{ item.sortNo }}</span>
              <div>
                <h3>{{ item.taskName || '未填写任务名称' }}</h3>
                <span>{{ item.workType || '常规任务' }}</span>
              </div>
              <div class="plan-item-row__header-actions">
                <el-tag :type="statusTag(item.status)" effect="plain">{{ item.status }}</el-tag>
                <el-button
                  v-if="canProcess(activeRow)"
                  link
                  type="primary"
                  :icon="ChatLineSquare"
                  @click="toggleTaskOpinion(item)"
                >
                  {{ hasTaskOpinion(item) ? '已填写意见' : '意见' }}
                </el-button>
              </div>
            </header>
            <MonthPlanTaskContentView class="plan-item-row__content" :content="item.taskContent" empty-text="未填写任务内容" />
            <dl class="plan-item-row__details">
              <div class="plan-item-row__wide"><dt>考核标准（交付物、质量）</dt><dd>{{ formatMonthPlanStandard(item.deliverable, item.quality, '未填写') }}</dd></div>
              <div><dt>完成时间段</dt><dd>{{ formatMonthPlanPeriod(item.estimatedStartDate, item.estimatedEndDate, '未填写') }}</dd></div>
              <div><dt>绩效权重</dt><dd>{{ item.performanceWeight }}%</dd></div>
              <div><dt>预估价值</dt><dd>¥{{ Number(item.estimatedValue || 0).toLocaleString('zh-CN') }}</dd></div>
            </dl>
            <div v-if="isTaskOpinionOpen(item)" class="task-opinion-editor">
              <el-input
                v-model="taskOpinions[opinionKey(item)]"
                type="textarea"
                :rows="3"
                maxlength="1000"
                show-word-limit
                resize="vertical"
                placeholder="请输入该任务的修改建议"
              />
              <div class="task-opinion-editor__actions">
                <el-button
                  v-if="hasTaskOpinion(item)"
                  link
                  type="danger"
                  @click="clearTaskOpinion(item)"
                >
                  清空
                </el-button>
                <el-button size="small" @click="toggleTaskOpinion(item)">收起</el-button>
              </div>
            </div>
          </article>
        </div>
        <el-empty v-else :image-size="72" description="暂无计划事项" />
        <div class="batch-bar">
          <div>当前计划 <strong>{{ activeRow.status }}</strong></div>
          <div class="batch-actions">
            <el-button
              type="primary"
              plain
              :icon="Download"
              :loading="downloadingTemplateId === activeRow.id"
              :disabled="!!downloadingTemplateId"
              @click="downloadTemplate(activeRow)"
            >
              下载 Excel
            </el-button>
            <el-tooltip
              :disabled="canApprove(activeRow)"
              content="必要字段缺失或已审批，不能直接通过"
              placement="top"
            >
              <span>
                <el-button
                  type="success"
                  :icon="Check"
                  :loading="processingId === activeRow.id && processingAction === 'approve'"
                  :disabled="!canApprove(activeRow) || actionBusy"
                  @click="approve(activeRow)"
                >
                  通过
                </el-button>
              </span>
            </el-tooltip>
            <el-button
              type="danger"
              :icon="Close"
              :loading="processingId === activeRow.id && processingAction === 'reject'"
              :disabled="!canProcess(activeRow) || actionBusy"
              @click="reject(activeRow)"
            >
              驳回
            </el-button>
          </div>
        </div>
        </template>
      </div>
    </el-drawer>
    <PlanAiReviewDrawer
      v-model="aiReviewDrawerVisible"
      plan-type="MONTH"
      :review="aiReviewRow ? aiReviews.reviewFor(aiReviewRow.id) : null"
      :loading="aiReviews.loading.value"
      :subject="aiReviewRow ? `${aiReviewRow.month} 月计划` : ''"
      :context="aiReviewRow ? [aiReviewRow.employee, aiReviewRow.department].filter(Boolean).join(' · ') : ''"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatLineSquare, Check, Close, Download, HomeFilled, MoreFilled, Right, Search, View } from '@element-plus/icons-vue'
import {
  approveDepartmentLeaderMonthPlanApi,
  batchApproveDepartmentLeaderMonthPlansApi,
  batchRejectDepartmentLeaderMonthPlansApi,
  downloadDepartmentLeaderMonthPlanTemplateApi,
  getDepartmentLeaderMonthPlanApprovalApi,
  pageDepartmentLeaderMonthPlanApprovalsApi,
  rejectDepartmentLeaderMonthPlanApi,
  type MonthPlanApprovalItem,
  type MonthPlanRejectionBaseline,
} from '@/api/department'
import {
  approveLeaderMonthPlanApi,
  batchApproveLeaderMonthPlansApi,
  batchRejectLeaderMonthPlansApi,
  downloadLeaderMonthPlanTemplateApi,
  getLeaderMonthPlanApprovalApi,
  pageLeaderMonthPlanApprovalsApi,
  rejectLeaderMonthPlanApi,
} from '@/api/leader'
import { errorMessage } from '@/api/performance'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { useDepartmentOrgScope } from '@/composables/useDepartmentOrgScope'
import { useLeaderOrgScope } from '@/composables/useLeaderOrgScope'
import { approvalStatusCodes, mapMonthApproval } from '@/views/performanceAdapters'
import { useMessageStore } from '@/stores/messages'
import { saveBlob } from '@/utils/download'
import {
  buildMonthPlanRejectionComment,
  MONTH_PLAN_APPROVAL_COMMENT_LIMIT,
  monthPlanOpinionKey,
  type MonthPlanOpinionItem,
} from '@/utils/monthPlanApprovalOpinions'
import MonthPlanVersionDiff from '@/views/employee/components/MonthPlanVersionDiff.vue'
import { compareMonthPlanSnapshots, type MonthPlanDiff } from '@/views/employee/components/planDraftVersionComparison'
import PlanAiReviewCell from '@/components/PlanAiReviewCell.vue'
import PlanAiReviewDrawer from '@/components/PlanAiReviewDrawer.vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import { useAiReviewCollection } from '@/composables/useAiReviewCollection'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'

type MonthApprovalRow = ReturnType<typeof mapMonthApproval>

const router = useRouter()
const route = useRoute()
const messageStore = useMessageStore()
const isLeader = computed(() => route.path.startsWith('/leader'))
const tableRef = ref()
const departmentScope = useDepartmentOrgScope(false)
const leaderScope = useLeaderOrgScope()
const orgOptions = computed(() => isLeader.value ? leaderScope.orgOptions.value : departmentScope.orgOptions.value)
const orgLoading = computed(() => isLeader.value ? leaderScope.orgLoading.value : departmentScope.orgLoading.value)
const selectedOrgId = computed<number | undefined>({
  get: () => isLeader.value ? leaderScope.scopeOrgId.value : departmentScope.selectedOrgId.value,
  set: (value) => {
    if (isLeader.value) leaderScope.scopeOrgId.value = value
    else departmentScope.selectedOrgId.value = value
  },
})
const loadOrgScope = () => isLeader.value ? leaderScope.loadOrgScope() : departmentScope.loadOrgScope()
const resetOrgScope = () => isLeader.value ? leaderScope.resetOrgScope() : departmentScope.resetOrgScope()
const defaultPlanYear = String(new Date().getFullYear())
const defaultPlanMonth = String(new Date().getMonth() + 1).padStart(2, '0')
const query = reactive({
  year: defaultPlanYear,
  month: defaultPlanMonth,
  status: '',
  keyword: '',
})
const tableRows = ref<MonthApprovalRow[]>([])
const selectedRows = ref<MonthApprovalRow[]>([])
const activeRow = ref<MonthApprovalRow | null>(null)
const aiReviews = useAiReviewCollection('MONTH_PLAN')
const aiReviewDrawerVisible = ref(false)
const aiReviewRow = ref<MonthApprovalRow | null>(null)
const comparisonStatus = ref<'NONE' | 'AVAILABLE' | 'UNAVAILABLE'>('NONE')
const rejectionBaseline = ref<MonthPlanRejectionBaseline>()
const taskOpinions = reactive<Record<string, string>>({})
const openTaskOpinionIds = ref<string[]>([])
const detailVisible = ref(false)
const detailLoading = ref(false)
const loading = ref(false)
const processingId = ref('')
const processingAction = ref('')
const batchAction = ref('')
const downloadingTemplateId = ref('')
let refreshRequestId = 0
let detailRequestId = 0
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const submittedCount = ref(0)
const approvedCount = ref(0)
const totalEmployeeCount = ref(0)
const actionBusy = computed(() => Boolean(processingId.value || batchAction.value))
const emptyMonthDiff: MonthPlanDiff = {
  items: [],
  counts: { added: 0, modified: 0, removed: 0 },
  unchanged: true,
}
const resubmissionDiff = computed(() => {
  if (!activeRow.value || !rejectionBaseline.value) return emptyMonthDiff
  const currentSnapshot = {
    planMonth: activeRow.value.month,
    items: activeRow.value.items.map((item) => ({
      id: item.id,
      workType: item.workType,
      taskName: item.taskName,
      taskContent: item.taskContent,
      deliverable: item.deliverable,
      quality: item.quality,
      estimatedStartDate: item.estimatedStartDate,
      estimatedEndDate: item.estimatedEndDate,
      performanceWeight: item.performanceWeight,
      estimatedValue: item.estimatedValue,
    })),
  }
  return compareMonthPlanSnapshots(rejectionBaseline.value.snapshot, currentSnapshot)
})
const autoQuery = useAutoQuery(
  () => [selectedOrgId.value, query.year, query.month, query.status, query.keyword],
  () => refresh(false),
)
const monthOptions = Array.from({ length: 12 }, (_, index) => {
  const value = String(index + 1).padStart(2, '0')
  return { label: `${index + 1} 月`, value }
})

function statusTag(status: string) {
  if (status === '已通过') return 'success'
  if (status === '已驳回' || status === '待补正') return 'danger'
  return 'warning'
}

async function refresh(showMessage = true, resetPage = true) {
  if (resetPage) currentPage.value = 1
  const requestId = ++refreshRequestId
  loading.value = true
  try {
    const queryParams = {
      planYear: query.year ? Number(query.year) : undefined,
      planMonth: query.month ? Number(query.month) : undefined,
      orgId: selectedOrgId.value,
      status: approvalStatusCodes[query.status] || undefined,
      keyword: query.keyword || undefined,
      pageNo: currentPage.value,
      pageSize: pageSize.value,
    }
    const data = isLeader.value
      ? await pageLeaderMonthPlanApprovalsApi(queryParams)
      : await pageDepartmentLeaderMonthPlanApprovalsApi(queryParams)
    if (requestId !== refreshRequestId) return
    tableRows.value = data.items.map(mapMonthApproval)
    await aiReviews.load(tableRows.value.map((row) => row.id))
    total.value = data.total
    submittedCount.value = data.submittedCount
    approvedCount.value = data.approvedCount
    totalEmployeeCount.value = data.totalEmployeeCount
    clearSelection()
    if (showMessage) ElMessage.success('月计划审批列表已刷新')
  } catch (error) {
    if (requestId !== refreshRequestId) return
    ElMessage.error(errorMessage(error))
  } finally {
    if (requestId === refreshRequestId) loading.value = false
  }
}

async function handlePageChange() {
  await refresh(false, false)
}

async function handlePageSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
  await refresh(false, false)
}

type MonthPlanApprovalCommand = {
  action: 'download'
  row: MonthApprovalRow
}

function handleMoreCommand(command: MonthPlanApprovalCommand) {
  if (command.action === 'download') void downloadTemplate(command.row)
}

async function reset() {
  autoQuery.pause()
  Object.assign(query, {
    year: defaultPlanYear,
    month: defaultPlanMonth,
    status: '',
    keyword: '',
  })
  resetOrgScope()
  await refresh(false)
  autoQuery.resume()
  ElMessage.success('筛选条件已重置')
}

async function downloadTemplate(row: MonthApprovalRow) {
  if (downloadingTemplateId.value) return
  downloadingTemplateId.value = row.id
  try {
    const blob = isLeader.value
      ? await downloadLeaderMonthPlanTemplateApi(row.id)
      : await downloadDepartmentLeaderMonthPlanTemplateApi(row.id)
    saveBlob(blob, templateFileName(row))
    ElMessage.success('月计划 Excel 已下载')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    downloadingTemplateId.value = ''
  }
}

function templateFileName(row: MonthApprovalRow) {
  return `month-plan-${row.month}-${row.id}.xlsx`
}

async function openDetail(row: MonthApprovalRow) {
  await openDetailById(row.id)
}

async function openAiReview(row: MonthApprovalRow) {
  aiReviewRow.value = row
  aiReviewDrawerVisible.value = true
  await aiReviews.loadOne(row.id)
}

async function openDetailById(id: string) {
  const requestId = ++detailRequestId
  detailVisible.value = true
  detailLoading.value = true
  activeRow.value = null
  comparisonStatus.value = 'NONE'
  rejectionBaseline.value = undefined
  resetTaskOpinions()
  try {
    const [response] = await Promise.all([
      isLeader.value ? getLeaderMonthPlanApprovalApi(id) : getDepartmentLeaderMonthPlanApprovalApi(id),
      aiReviews.loadOne(id),
    ])
    const plan: MonthPlanApprovalItem = response.plan
    const nextComparisonStatus = response.comparisonStatus
    const nextRejectionBaseline = response.rejectionBaseline
    if (requestId !== detailRequestId) return
    activeRow.value = mapMonthApproval(plan)
    comparisonStatus.value = nextComparisonStatus
    rejectionBaseline.value = nextRejectionBaseline
  } catch (error) {
    if (requestId !== detailRequestId) return
    detailVisible.value = false
    ElMessage.error(errorMessage(error))
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
  }
}

function closeDetail() {
  detailRequestId += 1
  activeRow.value = null
  comparisonStatus.value = 'NONE'
  rejectionBaseline.value = undefined
  detailLoading.value = false
  resetTaskOpinions()
}

function opinionKey(item: MonthPlanOpinionItem) {
  return monthPlanOpinionKey(item)
}

function isTaskOpinionOpen(item: MonthPlanOpinionItem) {
  return openTaskOpinionIds.value.includes(opinionKey(item))
}

function hasTaskOpinion(item: MonthPlanOpinionItem) {
  return Boolean(taskOpinions[opinionKey(item)]?.trim())
}

function toggleTaskOpinion(item: MonthPlanOpinionItem) {
  const key = opinionKey(item)
  openTaskOpinionIds.value = isTaskOpinionOpen(item)
    ? openTaskOpinionIds.value.filter((id) => id !== key)
    : [...openTaskOpinionIds.value, key]
}

function clearTaskOpinion(item: MonthPlanOpinionItem) {
  taskOpinions[opinionKey(item)] = ''
}

function resetTaskOpinions() {
  Object.keys(taskOpinions).forEach((key) => delete taskOpinions[key])
  openTaskOpinionIds.value = []
}

function formatComparisonTime(value: string) {
  return value ? value.slice(0, 16).replace('T', ' ') : '-'
}

function canApprove(row: MonthApprovalRow) {
  return row.aiCheck === '正常' && canProcess(row)
}

function canProcess(row: MonthApprovalRow) {
  return row.status === '待审批'
}

function canSelect(row: MonthApprovalRow) {
  return canProcess(row) && !actionBusy.value
}

function rowClassName({ row }: { row: MonthApprovalRow }) {
  if (row.aiCheck !== '正常') return 'danger-row clickable-row'
  if (row.status === '待审批') return 'warning-row clickable-row'
  return 'clickable-row'
}

function onSelectionChange(rows: MonthApprovalRow[]) {
  selectedRows.value = rows
}

function clearSelection() {
  tableRef.value?.clearSelection?.()
  selectedRows.value = []
}

function syncRowStatus(id: string, status: string) {
  const tableRow = tableRows.value.find((row) => row.id === id)
  if (tableRow) tableRow.status = status
  if (activeRow.value?.id === id) activeRow.value.status = status
}

async function batchApprove() {
  if (actionBusy.value || !selectedRows.value.length) return
  const blocked = selectedRows.value.filter((row) => !canApprove(row)).length
  if (blocked) {
    ElMessage.warning(`${blocked} 条计划存在必要信息异常或已审批，需先处理异常`)
    return
  }
  try {
    await ElMessageBox.confirm(`确认批量通过 ${selectedRows.value.length} 条月计划？`, '批量审批', {
      confirmButtonText: '批量通过',
      cancelButtonText: '取消',
      type: 'success',
    })
    batchAction.value = 'approve'
    const rows = [...selectedRows.value]
    const payload = { ids: rows.map((row) => row.id), comment: '批量审批通过。' }
    const results = isLeader.value
      ? await batchApproveLeaderMonthPlansApi(payload)
      : await batchApproveDepartmentLeaderMonthPlansApi(payload)
    rows.forEach((row) => syncRowStatus(row.id, '已通过'))
    clearSelection()
    await Promise.all([refresh(false, false), messageStore.refresh(true).catch(() => undefined)])
    ElMessage.success(`${results.length} 条月计划已批量审批通过`)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    batchAction.value = ''
  }
}

async function batchReject() {
  if (actionBusy.value || !selectedRows.value.length) return
  try {
    const { value } = await ElMessageBox.prompt('请输入批量驳回原因', '批量驳回月计划', {
      confirmButtonText: '批量驳回',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValidator: (text) => {
        if (!text?.trim()) return '请输入驳回原因'
        return text.trim().length <= 500 || '驳回原因不能超过 500 个字符'
      },
    })
    batchAction.value = 'reject'
    const rows = [...selectedRows.value]
    const payload = {
      ids: rows.map((row) => row.id),
      comment: value,
      notifyEmployee: true,
    }
    const results = isLeader.value
      ? await batchRejectLeaderMonthPlansApi(payload)
      : await batchRejectDepartmentLeaderMonthPlansApi(payload)
    rows.forEach((row) => syncRowStatus(row.id, '已驳回'))
    clearSelection()
    await Promise.all([refresh(false, false), messageStore.refresh(true).catch(() => undefined)])
    ElMessage.success(`${results.length} 条月计划已批量驳回`)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    batchAction.value = ''
  }
}

async function approve(row: MonthApprovalRow) {
  if (!canApprove(row) || actionBusy.value) return
  try {
    await ElMessageBox.confirm(`确认通过 ${row.employee} 的月计划？`, '月计划审批', {
      confirmButtonText: '通过',
      cancelButtonText: '取消',
      type: 'success',
    })
    processingId.value = row.id
    processingAction.value = 'approve'
    const payload = { decision: 'APPROVED', comment: '审批通过。' }
    const result = isLeader.value
      ? await approveLeaderMonthPlanApi(row.id, payload)
      : await approveDepartmentLeaderMonthPlanApi(row.id, payload)
    syncRowStatus(row.id, '已通过')
    await Promise.all([refresh(false, false), messageStore.refresh(true).catch(() => undefined)])
    ElMessage.success(result.message)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    processingId.value = ''
    processingAction.value = ''
  }
}

async function reject(row: MonthApprovalRow) {
  if (!canProcess(row) || actionBusy.value) return
  try {
    const taskOpinionComment = activeRow.value?.id === row.id
      ? buildMonthPlanRejectionComment(activeRow.value.items, taskOpinions)
      : ''
    const { value } = await ElMessageBox.prompt(`请输入驳回 ${row.employee} 月计划的原因`, '驳回月计划', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValue: taskOpinionComment,
      inputPlaceholder: taskOpinionComment ? '可继续补充总体意见' : '请输入驳回原因',
      customClass: 'month-plan-reject-prompt',
      inputValidator: (text) => {
        if (!text?.trim()) return '请输入驳回原因'
        return text.trim().length <= MONTH_PLAN_APPROVAL_COMMENT_LIMIT
          || `驳回原因不能超过 ${MONTH_PLAN_APPROVAL_COMMENT_LIMIT} 个字符`
      },
    })
    processingId.value = row.id
    processingAction.value = 'reject'
    const payload = { decision: 'REJECTED', comment: value, notifyEmployee: true }
    const result = isLeader.value
      ? await rejectLeaderMonthPlanApi(row.id, payload)
      : await rejectDepartmentLeaderMonthPlanApi(row.id, payload)
    syncRowStatus(row.id, '已驳回')
    await Promise.all([refresh(false, false), messageStore.refresh(true).catch(() => undefined)])
    ElMessage.success(result.message)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    processingId.value = ''
    processingAction.value = ''
  }
}

onMounted(async () => {
  try {
    await loadOrgScope()
    await refresh(false)
    autoQuery.resume()
    if (typeof route.query.planId === 'string') {
      await openDetailById(route.query.planId)
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
})
</script>

<style scoped>
.resubmission-review { display:grid; margin-bottom:24px; padding-top:2px; }
.resubmission-review__heading { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:3px 1px 15px; }
.resubmission-review__title { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:4px 11px; min-width:0; }
.resubmission-review__title > span { grid-row:1 / span 2; align-self:center; display:grid; padding:5px 8px; place-items:center; border:1px solid #d9b77d; border-radius:5px; color:#8b5a16; background:#fff8ed; font-size:12px; font-weight:700; }
.resubmission-review__title h2 { display:flex; align-items:center; gap:8px; margin:0; color:#263632; font:700 20px/1.35 "Cascadia Mono",monospace; }
.resubmission-review__title h2 b { font:inherit; }
.resubmission-review__title h2 b:last-child { color:#237a57; }
.resubmission-review__title h2 .el-icon { color:#86948f; font-size:16px; }
.resubmission-review__title p { margin:0; overflow-wrap:anywhere; color:#687873; font-size:12px; line-height:1.5; }
.rejection-context { display:grid; grid-template-columns:minmax(0,2fr) minmax(180px,1fr); margin:0; border:1px solid #eadbc4; border-bottom:0; border-radius:6px 6px 0 0; background:#fff8ed; }
.rejection-context div { min-width:0; padding:11px 14px; border-left:1px solid #eadbc4; }
.rejection-context div:first-child { border-left:0; }
.rejection-context dt { color:#8a6a3b; font-size:12px; }
.rejection-context dd { margin:5px 0 0; overflow-wrap:anywhere; color:#453e33; font-size:14px; line-height:1.55; white-space:pre-wrap; }
.resubmission-review :deep(.diff-overview) { border-radius:0; }
.current-plan-divider { display:flex; align-items:baseline; gap:12px; margin:4px 0 14px; padding-top:17px; border-top:1px solid #dfe6e3; }
.current-plan-divider span { color:#263632; font-size:15px; font-weight:700; }
.current-plan-divider p { margin:0; color:#687873; font-size:12px; line-height:1.5; }
.comparison-unavailable { margin-bottom:18px; }
.approval-status-strip {
  align-items: center;
  gap: 0;
  margin-top: 0;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}
.approval-status-strip .status-pill {
  min-height: 26px;
  padding: 0 18px 0 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.approval-status-strip .status-pill + .status-pill {
  padding-left: 18px;
  border-left: 1px solid var(--line);
}
.approval-filter-bar {
  align-items: center;
  gap: 8px;
  margin: 0 0 16px;
  padding: 11px 12px;
  border-radius: 9px;
  background: #f5f8f5;
}
.approval-filter-bar::before { display: none; }
.approval-filter-label {
  flex: 0 0 auto;
  margin-right: 3px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
.approval-filter-bar .el-input,
.approval-filter-bar .el-select,
.approval-filter-bar .el-date-editor { width: 158px; }
.approval-filter-bar .el-input { width: 210px; }
.approval-filter-bar .el-button { min-width: 72px; }
.approval-batch-bar { margin: 0 0 12px; }
.approval-list-table { width: 100%; }
.approval-list-table :deep(.el-table__header th) {
  color: #596b65;
  background: #f6f8f4;
  font-size: 11px;
  font-weight: 750;
}
.approval-list-table :deep(.el-table__row) { vertical-align: top; }
.approval-list-table :deep(.el-table__cell) { padding: 12px 0; }
.approval-list-table :deep(.el-table__body tr) { cursor: pointer; }
.approval-list-table :deep(.clickable-row:hover .plan-employee-button strong) { color: var(--blue); }
.approval-row-index {
  color: #80908a;
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.plan-employee-button {
  display: grid;
  gap: 3px;
  width: 100%;
  padding: 0;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.plan-employee-button:focus-visible { outline: 2px solid var(--blue); outline-offset: 4px; }
.plan-employee-button strong { color: var(--ink); font-size: 13px; line-height: 1.45; transition: color .18s ease; }
.plan-employee-button span { overflow: hidden; color: var(--muted); font-size: 11px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
.plan-employee-button small { color: #7c8985; font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 10px; line-height: 1.4; }
.approval-period-cell { display: grid; gap: 5px; }
.approval-period-cell strong { color: var(--ink); font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 12px; font-variant-numeric: tabular-nums; }
.approval-period-cell span { color: var(--muted); font-size: 11px; }
.approval-state-cell { display: grid; gap: 7px; }
.approval-result__tags { display: flex; flex-wrap: wrap; gap: 6px; }
.approval-comment-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: max-content;
  max-width: 100%;
  overflow: hidden;
  color: var(--muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.approval-comment-hint .el-icon { font-size: 13px; }
.approval-comment-hint.is-empty { color: #9aa7a2; }
.approval-actions { display: flex; flex-wrap: nowrap; align-items: center; gap: 2px; white-space: nowrap; }
.approval-actions :deep(.el-button), .approval-actions :deep(.el-button + .el-button) { margin-left: 0; }
.approval-actions :deep(.el-button) { padding-inline: 5px; }
.approval-more { display: inline-flex; }
.approval-more :deep(.el-button) { min-width: 26px; padding-inline: 4px; }
.approval-pagination { justify-content: flex-end; margin-top: 16px; }
.approval-pagination :deep(.el-pagination) { flex-wrap: wrap; justify-content: flex-end; gap: 4px; }
.plan-item-list { display: grid; gap: 12px; }
.plan-item-row { padding: 17px 18px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.plan-item-row__header { display: grid; grid-template-columns: 30px minmax(0,1fr) auto; gap: 11px; align-items: start; }
.plan-item-row__header-actions { display:flex; align-items:center; justify-content:flex-end; gap:10px; }
.plan-item-row__header-actions :deep(.el-button) { margin:0; }
.plan-item-row__number { display: grid; width: 28px; height: 28px; place-items: center; border-radius: 6px; color: var(--blue); background: var(--soft-blue); font: 700 12px/1 "IBM Plex Mono", "Cascadia Mono", monospace; }
.plan-item-row__header h3 { margin: 1px 0 3px; color: var(--ink); font-size: 15px; line-height: 1.45; }
.plan-item-row__header div > span { color: var(--muted); font-size: 11px; }
.plan-item-row__content { margin: 13px 0 0 41px; color: var(--ink); font-size: 13px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.plan-item-row__details { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 0; margin: 15px 0 0 41px; border-top: 1px solid var(--line); }
.plan-item-row__details div { min-width: 0; padding: 12px 14px 0 0; }
.plan-item-row__details .plan-item-row__wide { grid-column: span 3; }
.plan-item-row__details dt { color: var(--muted); font-size: 11px; }
.plan-item-row__details dd { margin: 4px 0 0; color: var(--ink); font-size: 13px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.task-opinion-editor { margin:16px 0 0 41px; padding:14px; border:1px solid #bfd9cf; border-radius:6px; background:#f6fbf9; }
.task-opinion-editor :deep(.el-textarea__inner) { color:var(--ink); line-height:1.65; }
.task-opinion-editor__actions { display:flex; align-items:center; justify-content:flex-end; gap:8px; margin-top:10px; }
:global(.month-plan-reject-prompt) { width:min(680px, calc(100vw - 32px)); }
:global(.month-plan-reject-prompt .el-textarea__inner) { min-height:220px !important; line-height:1.65; }
.approval-text-cell {
  color: var(--ink);
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width:760px) {
  .approval-status-strip .status-pill { padding-right: 12px; }
  .approval-status-strip .status-pill + .status-pill { padding-left: 12px; }
  .approval-filter-bar { align-items: stretch; }
  .approval-filter-label { width: 100%; margin: 0 0 2px; }
  .approval-filter-bar .el-input,
  .approval-filter-bar .el-select,
  .approval-filter-bar .el-date-editor { width: calc(50% - 4px); }
  .approval-filter-bar .el-input { width: 100%; }
  .approval-filter-bar .el-button { flex: 1 1 0; }
  .approval-pagination { justify-content: center; }
  .approval-pagination :deep(.el-pagination) { justify-content: center; }
  .approval-pagination :deep(.el-pagination__sizes),
  .approval-pagination :deep(.el-pagination__jump) { display: none; }
  .resubmission-review__heading { align-items:flex-start; flex-direction:column; }
  .resubmission-review__title { grid-template-columns:auto minmax(0,1fr); }
  .rejection-context { grid-template-columns:1fr; }
  .rejection-context div { border-top:1px solid #eadbc4; border-left:0; }
  .rejection-context div:first-child { border-top:0; }
  .current-plan-divider { align-items:flex-start; flex-direction:column; gap:3px; }
  .plan-item-row { padding: 15px; }
  .plan-item-row__content,
  .plan-item-row__details,
  .task-opinion-editor { margin-left: 0; }
  .plan-item-row__header-actions { align-items:flex-end; flex-direction:column; gap:5px; }
  .plan-item-row__details { grid-template-columns: 1fr; }
  .plan-item-row__details .plan-item-row__wide { grid-column: auto; }
}
</style>
