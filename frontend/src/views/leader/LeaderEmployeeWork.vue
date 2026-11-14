<template>
  <section v-loading="loading" class="page-panel leader-employee-work">
    <button type="button" class="back-link" @click="goBack">
      <el-icon><ArrowLeft /></el-icon><span>{{ backLabel }}</span>
    </button>

    <template v-if="pageData">
      <header class="employee-work-head">
        <div class="employee-work-identity">
          <span class="employee-work-avatar">{{ employeeInitial }}</span>
          <div>
            <div class="employee-work-name">
              <h1>{{ pageData.employee.employeeName }}</h1>
              <span>{{ pageData.employee.employeeNo || '未填写工号' }}</span>
            </div>
            <p>{{ pageData.employee.orgName }} · {{ employeeScopeLabel }}</p>
          </div>
        </div>
        <el-button :icon="Refresh" :loading="loading" @click="loadWork(true)">刷新</el-button>
      </header>

      <div class="employee-work-totals" aria-label="员工工作汇总">
        <button type="button" :class="{ active: !query.workType }" @click="chooseType('')">
          <span>全部记录</span><strong>{{ pageData.employee.totalWorkCount }}</strong>
        </button>
        <button type="button" :class="{ active: query.workType === 'MONTH_PLAN' }" @click="chooseType('MONTH_PLAN')">
          <span>月计划</span><strong>{{ pageData.employee.monthPlanCount }}</strong>
        </button>
        <button type="button" :class="{ active: query.workType === 'WEEK_PLAN' }" @click="chooseType('WEEK_PLAN')">
          <span>周计划</span><strong>{{ pageData.employee.weekPlanCount }}</strong>
        </button>
        <button type="button" :class="{ active: query.workType === 'DAY_PLAN' }" @click="chooseType('DAY_PLAN')">
          <span>日计划</span><strong>{{ pageData.employee.dayPlanCount }}</strong>
        </button>
        <button type="button" :class="{ active: query.workType === 'RESULT' }" @click="chooseType('RESULT')">
          <span>成果</span><strong>{{ pageData.employee.resultCount }}</strong>
        </button>
      </div>

      <div class="filter-bar employee-work-filter">
        <el-select v-model="query.workType" clearable placeholder="全部工作类型">
          <el-option label="月计划" value="MONTH_PLAN" />
          <el-option label="周计划" value="WEEK_PLAN" />
          <el-option label="日计划" value="DAY_PLAN" />
          <el-option label="成果" value="RESULT" />
        </el-select>
        <el-date-picker
          v-model="query.dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          unlink-panels
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
        />
        <el-select v-model="query.status" clearable placeholder="全部状态">
          <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-input
          v-model="query.keyword"
          clearable
          :prefix-icon="Search"
          placeholder="搜索计划、工作内容"
          @keyup.enter="applyFilters"
        />
        <el-button type="primary" :icon="Search" @click="applyFilters">查询</el-button>
        <el-button :disabled="!hasFilter" @click="resetFilters">重置</el-button>
      </div>

      <div class="work-list-head">
        <div>
          <strong>工作内容</strong>
          <span>共 {{ displayTotal }} 条记录</span>
        </div>
        <span v-if="!hasFilter">当前未设置筛选条件</span>
      </div>

      <div class="table-card employee-work-table-wrap">
        <el-table :data="displayItems" row-key="rowId" empty-text="该员工暂无符合条件的工作记录">
          <el-table-column label="序号" width="66" align="center">
            <template #default="{ $index }">{{ rowNumber($index) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="104">
            <template #default="{ row }">
              <div class="work-type-cell">
                <el-tag :type="workTypeMeta(row.workType).tone" effect="plain">{{ workTypeMeta(row.workType).label }}</el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="周期" width="176">
            <template #default="{ row }"><span class="period-text">{{ row.periodLabel || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="工作内容" min-width="340">
            <template #default="{ row }">
              <div class="work-content-cell">
                <strong>{{ row.title || workTypeMeta(row.workType).label }}</strong>
                <p v-if="row.workType !== 'MONTH_PLAN'">{{ row.content || '未填写具体内容' }}</p>
                <span v-if="row.relatedPlanTitle">关联：{{ row.relatedPlanTitle }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="108">
            <template #default="{ row }">
              <el-tag :type="statusMeta(row.status).tone">{{ statusMeta(row.status).label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="154">
            <template #default="{ row }">{{ formatDateTime(row.updatedAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="92" fixed="right" align="center">
            <template #default="{ row }">
              <el-button
                v-if="row.workType === 'MONTH_PLAN'"
                type="primary"
                link
                :icon="View"
                @click="openMonthPlan(row)"
              >查看</el-button>
              <span v-else class="empty-action">-</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-pagination
        v-if="displayTotal > 0"
        class="employee-work-pagination"
        background
        layout="total, sizes, prev, pager, next, jumper"
        :total="displayTotal"
        :current-page="query.pageNo"
        :page-size="query.pageSize"
        :page-sizes="[10, 20, 50, 100]"
        @current-change="changePage"
        @size-change="changePageSize"
      />

      <el-drawer
        v-model="monthPlanDrawerVisible"
        class="month-plan-detail-drawer"
        size="min(1120px, 92vw)"
        destroy-on-close
        @closed="monthPlanDetail = null"
      >
        <template #header>
          <div class="month-plan-drawer-title">
            <span>月计划详情</span>
            <strong>{{ monthPlanDetail?.title || '正在加载计划' }}</strong>
          </div>
        </template>

        <div v-loading="monthPlanDetailLoading" class="month-plan-detail-body">
          <template v-if="monthPlanDetail">
            <div class="month-plan-overview">
              <div>
                <span>计划月份</span>
                <strong>{{ monthPlanDetail.planMonth || '-' }}</strong>
              </div>
              <div>
                <span>计划状态</span>
                <el-tag :type="statusMeta(monthPlanDetail.status).tone">
                  {{ statusMeta(monthPlanDetail.status).label }}
                </el-tag>
              </div>
              <div>
                <span>更新时间</span>
                <strong>{{ formatDateTime(monthPlanDetail.updatedAt) }}</strong>
              </div>
            </div>

            <section v-if="monthPlanDetail.leaderComment" class="month-plan-comment-section">
              <h3>审批意见</h3>
              <p>{{ monthPlanDetail.leaderComment }}</p>
            </section>

            <div class="month-plan-task-head">
              <div>
                <h3>月计划任务</h3>
                <span>共 {{ monthPlanDetail.tasks.length }} 项</span>
              </div>
            </div>

            <div class="month-plan-task-table">
              <el-table :data="monthPlanDetail.tasks" row-key="id" empty-text="该月计划暂无具体任务">
                <el-table-column label="序号" width="66" align="center">
                  <template #default="{ $index }">{{ $index + 1 }}</template>
                </el-table-column>
                <el-table-column label="任务" min-width="220">
                  <template #default="{ row }">
                    <div class="task-name-cell">
                      <strong>{{ row.taskName || '未命名任务' }}</strong>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="任务内容" min-width="260">
                  <template #default="{ row }">
                    <MonthPlanTaskContentView class="task-field-text" :content="row.taskContent" empty-text="-" />
                  </template>
                </el-table-column>
                <el-table-column label="考核标准（交付物、质量）" min-width="340">
                  <template #default="{ row }"><p class="task-field-text">{{ formatMonthPlanStandard(row.deliverable, row.quality) }}</p></template>
                </el-table-column>
                <el-table-column label="完成时间段" width="126" align="center">
                  <template #default="{ row }">{{ formatMonthPlanPeriod(row.estimatedStartDate, row.estimatedEndDate) }}</template>
                </el-table-column>
                <el-table-column label="绩效权重" width="108" align="right">
                  <template #default="{ row }">{{ formatWeight(row.performanceWeight) }}</template>
                </el-table-column>
                <el-table-column label="预估价值" width="138" align="right">
                  <template #default="{ row }">{{ formatCurrency(row.estimatedValue) }}</template>
                </el-table-column>
                <el-table-column label="状态" width="104">
                  <template #default="{ row }">
                    <el-tag :type="statusMeta(row.status).tone" effect="plain">
                      {{ statusMeta(row.status).label }}
                    </el-tag>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </div>
      </el-drawer>
    </template>

    <el-empty v-else-if="!loading" :description="emptyDescription" />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh, Search, View } from '@element-plus/icons-vue'
import {
  getLeaderEmployeeMonthPlanApi,
  getLeaderEmployeeWorkApi,
  type LeaderEmployeeMonthPlanDetail,
  type LeaderEmployeeWorkItem,
  type LeaderEmployeeWorkPage,
  type LeaderWorkType,
} from '@/api/leader'
import { getDepartmentEmployeeMonthPlanApi, getDepartmentEmployeeWorkApi } from '@/api/department'
import { errorMessage } from '@/api/performance'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'

type TagTone = 'info' | 'warning' | 'success' | 'danger' | 'primary'

const route = useRoute()
const router = useRouter()
const ownerId = Number(route.params.ownerId)
const loading = ref(false)
const pageData = ref<LeaderEmployeeWorkPage | null>(null)
const monthPlanDrawerVisible = ref(false)
const monthPlanDetailLoading = ref(false)
const monthPlanDetail = ref<LeaderEmployeeMonthPlanDetail | null>(null)
const isDepartmentMode = computed(() => route.path.startsWith('/department/'))
const backLabel = computed(() => isDepartmentMode.value ? '返回部门成员' : '返回直属员工')
const employeeScopeLabel = computed(() => isDepartmentMode.value ? '当前部门范围员工' : '当前直属员工')
const emptyDescription = computed(() => isDepartmentMode.value
  ? '员工信息不存在或已不在当前部门范围'
  : '员工信息不存在或已不在当前直属范围')
const query = reactive({
  workType: '' as LeaderWorkType | '',
  dateRange: [] as string[],
  status: '',
  keyword: '',
  pageNo: 1,
  pageSize: 20,
})

const statusOptions = [
  { label: '待审批/已提交', value: 'SUBMITTED' },
  { label: '待审批', value: 'PENDING' },
  { label: '已通过', value: 'APPROVED' },
  { label: '已驳回', value: 'REJECTED' },
  { label: '已确认', value: 'CONFIRMED' },
  { label: '已撤回', value: 'WITHDRAWN' },
  { label: '已暂停', value: 'PAUSED' },
  { label: '已取消', value: 'CANCELED' },
]

const hasFilter = computed(() => Boolean(
  query.workType || query.dateRange?.length || query.status || query.keyword.trim(),
))
const employeeInitial = computed(() => pageData.value?.employee.employeeName?.trim().slice(-1) || '员')
const displayItems = computed(() => collapseMonthPlanRows(pageData.value?.items ?? []))
const displayTotal = computed(() => {
  if (!pageData.value) return 0
  const collapsedOnPage = pageData.value.items.length - displayItems.value.length
  return Math.max(displayItems.value.length, pageData.value.total - collapsedOnPage)
})

function collapseMonthPlanRows(items: LeaderEmployeeWorkItem[]) {
  const monthPlanCounts = new Map<number, number>()
  const seenMonthPlanIds = new Set<number>()
  for (const item of items) {
    if (item.workType === 'MONTH_PLAN') {
      monthPlanCounts.set(item.businessId, (monthPlanCounts.get(item.businessId) ?? 0) + 1)
    }
  }
  return items.flatMap((item) => {
    if (item.workType !== 'MONTH_PLAN') return [item]
    if (seenMonthPlanIds.has(item.businessId)) return []
    seenMonthPlanIds.add(item.businessId)
    if (item.itemId == null) return [item]
    const taskCount = monthPlanCounts.get(item.businessId) ?? 1
    return [{
      ...item,
      rowId: `MONTH_PLAN:${item.businessId}:PLAN`,
      taskType: undefined,
      itemId: undefined,
      title: `${item.periodLabel || '本月'} 月计划`,
      content: `共 ${taskCount} 项月计划任务`,
      deliverable: '',
      remark: '',
      quality: '',
      estimatedStartDate: undefined,
      estimatedEndDate: undefined,
      performanceWeight: undefined,
      estimatedValue: undefined,
    }]
  })
}

async function loadWork(showMessage = false) {
  if (!Number.isFinite(ownerId) || ownerId <= 0) return
  loading.value = true
  try {
    const requestApi = isDepartmentMode.value ? getDepartmentEmployeeWorkApi : getLeaderEmployeeWorkApi
    pageData.value = await requestApi(ownerId, {
      workType: query.workType || undefined,
      startDate: query.dateRange?.[0] || undefined,
      endDate: query.dateRange?.[1] || undefined,
      status: query.status || undefined,
      keyword: query.keyword.trim() || undefined,
      pageNo: query.pageNo,
      pageSize: query.pageSize,
    })
    if (showMessage) ElMessage.success('员工工作记录已刷新')
  } catch (error) {
    pageData.value = null
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function applyFilters() {
  query.pageNo = 1
  await loadWork(false)
}

async function resetFilters() {
  query.workType = ''
  query.dateRange = []
  query.status = ''
  query.keyword = ''
  query.pageNo = 1
  await loadWork(false)
}

async function chooseType(type: LeaderWorkType | '') {
  query.workType = type
  query.pageNo = 1
  await loadWork(false)
}

async function changePage(page: number) {
  query.pageNo = page
  await loadWork(false)
}

async function changePageSize(size: number) {
  query.pageSize = size
  query.pageNo = 1
  await loadWork(false)
}

async function openMonthPlan(row: LeaderEmployeeWorkItem) {
  monthPlanDrawerVisible.value = true
  monthPlanDetailLoading.value = true
  monthPlanDetail.value = null
  try {
    const requestApi = isDepartmentMode.value
      ? getDepartmentEmployeeMonthPlanApi
      : getLeaderEmployeeMonthPlanApi
    monthPlanDetail.value = await requestApi(ownerId, row.businessId)
  } catch (error) {
    monthPlanDrawerVisible.value = false
    ElMessage.error(errorMessage(error))
  } finally {
    monthPlanDetailLoading.value = false
  }
}

function goBack() {
  router.push({
    path: isDepartmentMode.value ? '/department/dashboard' : '/leader/workbench',
    query: {
      org: route.query.fromOrg || undefined,
      keyword: isDepartmentMode.value ? route.query.fromKeyword || undefined : undefined,
    },
  })
}

function workTypeMeta(type: LeaderWorkType): { label: string; tone: TagTone } {
  return ({
    MONTH_PLAN: { label: '月计划', tone: 'primary' },
    WEEK_PLAN: { label: '周计划', tone: 'success' },
    DAY_PLAN: { label: '日计划', tone: 'info' },
    RESULT: { label: '成果', tone: 'warning' },
  } as Record<LeaderWorkType, { label: string; tone: TagTone }>)[type]
}

function statusMeta(value?: string): { label: string; tone: TagTone } {
  const key = (value || 'UNKNOWN').toUpperCase()
  return ({
    DRAFT: { label: '草稿', tone: 'info' },
    SUBMITTED: { label: '已提交', tone: 'warning' },
    PENDING: { label: '待审批', tone: 'warning' },
    APPROVED: { label: '已通过', tone: 'success' },
    REJECTED: { label: '已驳回', tone: 'danger' },
    CONFIRMED: { label: '已确认', tone: 'success' },
    WITHDRAWN: { label: '已撤回', tone: 'info' },
    PAUSED: { label: '已暂停', tone: 'warning' },
    CANCELED: { label: '已取消', tone: 'info' },
    CANCELLED: { label: '已取消', tone: 'info' },
    UNKNOWN: { label: '未知', tone: 'info' },
  } as Record<string, { label: string; tone: TagTone }>)[key] || { label: value || '未知', tone: 'info' }
}

function formatDateTime(value?: string) {
  return value ? value.replace('T', ' ').slice(0, 16) : '-'
}

function rowNumber(index: number) {
  const pageNo = pageData.value?.pageNo ?? query.pageNo
  const pageSize = pageData.value?.pageSize ?? query.pageSize
  return (pageNo - 1) * pageSize + index + 1
}

function formatWeight(value?: number) {
  return value == null ? '-' : `${Number(value).toLocaleString('zh-CN')}%`
}

function formatCurrency(value?: number) {
  return value == null
    ? '-'
    : `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

onMounted(() => loadWork(false))
</script>

<style scoped>
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: -4px 0 18px;
  padding: 5px 0;
  border: 0;
  color: var(--blue);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.back-link:hover,
.back-link:focus-visible {
  color: var(--navy-800);
}

.back-link:focus-visible {
  outline: 2px solid rgb(36 116 166 / 28%);
  outline-offset: 4px;
  border-radius: 4px;
}

.employee-work-head,
.employee-work-identity,
.employee-work-name,
.work-list-head,
.work-list-head > div {
  display: flex;
  align-items: center;
}

.employee-work-head {
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 22px;
  border-bottom: 1px solid var(--line);
}

.employee-work-identity {
  gap: 13px;
  min-width: 0;
}

.employee-work-avatar {
  display: grid;
  width: 46px;
  height: 46px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #315e59;
  font-size: 16px;
  font-weight: 750;
}

.employee-work-name {
  flex-wrap: wrap;
  gap: 9px;
}

.employee-work-name h1 {
  margin: 0;
  color: var(--ink);
  font-size: 24px;
  letter-spacing: 0;
}

.employee-work-name span {
  padding: 3px 7px;
  border-radius: 5px;
  color: #516963;
  background: #edf2ef;
  font: 650 10px/1.4 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.employee-work-identity p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.employee-work-totals {
  display: grid;
  grid-template-columns: repeat(5, minmax(110px, 1fr));
  margin: 18px 0;
  overflow-x: auto;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.employee-work-totals button {
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 13px 16px;
  border: 0;
  border-right: 1px solid var(--line);
  color: #5d706a;
  background: transparent;
  cursor: pointer;
}

.employee-work-totals button:last-child {
  border-right: 0;
}

.employee-work-totals button:hover,
.employee-work-totals button.active {
  color: var(--ink);
  background: #f0f6f3;
}

.employee-work-totals button.active {
  box-shadow: inset 0 -2px 0 #3f7f72;
}

.employee-work-totals button:focus-visible {
  position: relative;
  z-index: 1;
  outline: 2px solid rgb(36 116 166 / 28%);
  outline-offset: -2px;
}

.employee-work-totals span {
  font-size: 12px;
}

.employee-work-totals strong {
  color: inherit;
  font: 700 20px/1 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.employee-work-filter {
  padding-left: 16px;
}

.employee-work-filter::before {
  display: none;
}

.employee-work-filter .el-select {
  width: 160px;
}

.employee-work-filter .el-date-editor {
  width: 260px;
}

.employee-work-filter .el-input {
  width: 240px;
}

.work-list-head {
  justify-content: space-between;
  gap: 16px;
  margin: 4px 0 12px;
}

.work-list-head > div {
  gap: 9px;
}

.work-list-head strong {
  color: var(--ink);
  font-size: 14px;
}

.work-list-head span {
  color: var(--muted);
  font-size: 11px;
}

.employee-work-table-wrap {
  overflow: hidden;
}

.employee-work-table-wrap :deep(.el-table) {
  width: 100%;
}

.empty-action {
  color: var(--muted);
}

.work-type-cell,
.work-content-cell {
  display: grid;
  gap: 6px;
}

.work-type-cell {
  justify-items: start;
}

.work-type-cell small,
.work-content-cell span {
  color: var(--muted);
  font-size: 10px;
  line-height: 1.5;
}

.period-text {
  color: #536a64;
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace;
  font-size: 11px;
}

.work-content-cell strong {
  color: var(--ink);
  font-size: 13px;
}

.work-content-cell p {
  margin: 0;
  color: #526761;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.employee-work-pagination {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 0;
  justify-content: flex-end;
  margin-top: 18px;
}

.month-plan-drawer-title {
  display: grid;
  gap: 3px;
}

.month-plan-drawer-title span {
  color: var(--muted);
  font-size: 11px;
}

.month-plan-drawer-title strong {
  color: var(--ink);
  font-size: 18px;
}

.month-plan-detail-body {
  min-height: 280px;
}

.month-plan-overview {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.month-plan-overview > div {
  display: grid;
  min-height: 74px;
  align-content: center;
  gap: 7px;
  padding: 12px 18px;
  border-right: 1px solid var(--line);
}

.month-plan-overview > div:last-child {
  border-right: 0;
}

.month-plan-overview span,
.month-plan-task-head span {
  color: var(--muted);
  font-size: 11px;
}

.month-plan-overview strong {
  color: var(--ink);
  font-size: 13px;
}

.month-plan-comment-section {
  padding: 22px 0;
  border-bottom: 1px solid var(--line);
}

.month-plan-comment-section h3,
.month-plan-task-head h3 {
  margin: 0;
  color: var(--ink);
  font-size: 14px;
  letter-spacing: 0;
}

.month-plan-comment-section > p {
  margin: 10px 0 0;
  padding: 10px 12px;
  border-left: 3px solid #d2a94e;
  color: #526761;
  background: #fbf8ef;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.month-plan-task-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 22px 0 12px;
}

.month-plan-task-head > div {
  display: flex;
  align-items: baseline;
  gap: 9px;
}

.month-plan-task-table {
  overflow: auto;
  border: 1px solid var(--line);
}

.month-plan-task-table :deep(.el-table) {
  min-width: 1320px;
}

.task-name-cell {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.task-name-cell strong {
  color: var(--ink);
  font-size: 12px;
  line-height: 1.6;
}

.task-field-text {
  margin: 0;
  color: #526761;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 900px) {
  .employee-work-head {
    align-items: stretch;
    flex-direction: column;
  }

  .employee-work-totals {
    grid-template-columns: repeat(5, minmax(126px, 1fr));
  }

  .month-plan-overview {
    grid-template-columns: 1fr;
  }

  .month-plan-overview > div {
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .month-plan-overview > div:last-child {
    border-bottom: 0;
  }
}
</style>
