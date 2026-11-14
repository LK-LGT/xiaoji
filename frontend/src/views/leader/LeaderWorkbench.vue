<template>
  <div v-loading="loading" class="leader-people-page">
    <section class="page-panel leader-people-panel">
    <div class="page-header leader-people-page__header">
      <div>
        <h1 class="page-title">直属员工</h1>
        <p class="page-subtitle">查看当前账号直接管理的员工及其月、周、日计划和成果记录。</p>
      </div>
      <div class="leader-people-page__actions">
        <el-button type="primary" :icon="EditPen" @click="writeMonthPlan">撰写月计划</el-button>
        <el-button :icon="Calendar" @click="writeWeekPlan">撰写周计划</el-button>
        <el-button :icon="Calendar" @click="writeDayPlan">撰写日计划</el-button>
        <el-button :icon="Refresh" :loading="loading || dayPackageLoading || packageLoading || resultPackageLoading" @click="refreshWorkbench">刷新</el-button>
      </div>
    </div>

    <div class="filter-bar leader-people-filter">
      <el-select
        v-model="scopeOrgId"
        clearable
        :loading="orgLoading"
        placeholder="全部直属范围"
      >
        <el-option v-for="item in orgOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-input
        v-model="keyword"
        clearable
        :prefix-icon="Search"
        placeholder="搜索员工姓名或工号"
        @keyup.enter="loadEmployees(true)"
        @clear="loadEmployees(false)"
      />
      <el-button type="primary" :icon="Search" @click="applyWorkbenchFilter">查询</el-button>
      <el-button :disabled="!hasFilter" @click="resetFilter">重置</el-button>
    </div>

    <div class="leader-people-summary" aria-live="polite">
      <div>
        <strong>{{ employees.length }}</strong>
        <span>名直属员工</span>
      </div>
    </div>

    <div class="table-card leader-people-table-wrap">
      <el-table
        class="leader-compact-table"
        :data="employees"
        row-key="ownerId"
        empty-text="当前没有直属员工，请先在员工管理中配置直属领导"
      >
        <el-table-column label="员工" width="160" fixed="left">
          <template #default="{ row }">
            <button type="button" class="employee-name-link" @click="openEmployee(row)">
              <span class="employee-initial">{{ employeeInitial(row.employeeName) }}</span>
              <span>
                <strong>{{ row.employeeName }}</strong>
                <small>{{ row.employeeNo || '未填写工号' }}</small>
              </span>
            </button>
          </template>
        </el-table-column>
        <el-table-column prop="orgName" label="所属组织" min-width="95" show-overflow-tooltip />
        <el-table-column label="月计划" min-width="60" align="center">
          <template #default="{ row }"><span class="work-count">{{ row.monthPlanCount }}</span></template>
        </el-table-column>
        <el-table-column label="周计划" min-width="60" align="center">
          <template #default="{ row }"><span class="work-count">{{ row.weekPlanCount }}</span></template>
        </el-table-column>
        <el-table-column label="日计划" min-width="60" align="center">
          <template #default="{ row }"><span class="work-count">{{ row.dayPlanCount }}</span></template>
        </el-table-column>
        <el-table-column label="成果" min-width="56" align="center">
          <template #default="{ row }"><span class="work-count is-result">{{ row.resultCount }}</span></template>
        </el-table-column>
        <el-table-column label="全部记录" min-width="72" align="center">
          <template #default="{ row }"><strong class="work-total">{{ row.totalWorkCount }}</strong></template>
        </el-table-column>
        <el-table-column label="最近更新" min-width="125">
          <template #default="{ row }">{{ formatDateTime(row.latestActivityAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEmployee(row)">
              查看工作<el-icon class="el-icon--right"><ArrowRight /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    </section>

    <section class="page-panel leader-project-day-panel" aria-labelledby="project-day-package-title">
      <div class="project-week-package__head">
        <div>
          <h2 id="project-day-package-title">项目组日计划</h2>
          <p>可随时打印；只打印已通过成员的日计划。</p>
        </div>
        <div class="project-week-selector project-day-selector">
          <span>
            <strong>计划日期</strong>
            <small>{{ dayPlanDate }}</small>
          </span>
          <el-date-picker
            v-model="dayPlanDate"
            type="date"
            format="YYYY年MM月DD日"
            value-format="YYYY-MM-DD"
            :clearable="false"
            placeholder="选择计划日期"
            aria-label="选择项目组日计划日期"
            @change="changeDayPackageDate"
          />
        </div>
      </div>

      <el-table
        v-loading="dayPackageLoading"
        class="project-week-package__table"
        :data="dayPackages"
        row-key="groupId"
        empty-text="当前账号没有可汇总的项目组"
      >
        <el-table-column label="项目组" min-width="150">
          <template #default="{ row }">
            <div class="project-week-group"><span>{{ employeeInitial(row.groupName) }}</span><strong>{{ row.groupName }}</strong></div>
          </template>
        </el-table-column>
        <el-table-column prop="planDate" label="计划日期" min-width="130" />
        <el-table-column label="已通过/总人数" min-width="190">
          <template #default="{ row }">
            <div class="project-week-progress">
              <strong>{{ row.submittedCount }}/{{ row.totalMemberCount }}</strong>
              <el-progress :percentage="packagePercentage(row)" :stroke-width="6" :show-text="false" :status="packageComplete(row) ? 'success' : undefined" />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }"><el-tag :type="packageHasPrintable(row) ? 'success' : 'info'" effect="plain">{{ packageHasPrintable(row) ? '可打印' : '暂无可打印' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="未完成人员" min-width="210">
          <template #default="{ row }">
            <span v-if="packageComplete(row)" class="project-week-complete">全员已通过</span>
            <el-tooltip v-else :content="pendingMemberText(row)" placement="top" :show-after="250"><span class="project-week-pending">{{ pendingMemberPreview(row) }}</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right" align="center">
          <template #default="{ row }">
            <el-button type="primary" link :icon="Printer" :loading="dayPrintLoadingGroupId === row.groupId" :disabled="!packageHasPrintable(row)" @click="printProjectDayPlan(row)">打印整组日计划</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="page-panel leader-project-week-panel">
      <section class="project-week-package" aria-labelledby="project-week-package-title">
      <div class="project-week-package__head">
        <div>
          <h2 id="project-week-package-title">项目组周计划</h2>
          <p>只统计项目组成员，不含领导；可打印或下载已通过成员的周计划。</p>
        </div>
        <div class="project-week-selector">
          <span>
            <strong>统计周</strong>
            <small>{{ selectedWeekRange }}</small>
          </span>
          <el-date-picker
            v-model="packageWeekStart"
            type="date"
            format="YYYY年 第ww周"
            value-format="YYYY-MM-DD"
            :clearable="false"
            placeholder="选择统计周"
            aria-label="选择项目组周计划统计周"
            @change="changePackageWeek"
          />
        </div>
      </div>

      <el-table
        v-loading="packageLoading"
        class="project-week-package__table"
        :data="weekPackages"
        row-key="groupId"
        empty-text="当前账号没有可汇总的项目组"
      >
        <el-table-column label="项目组" min-width="150">
          <template #default="{ row }">
            <div class="project-week-group">
              <span>{{ employeeInitial(row.groupName) }}</span>
              <strong>{{ row.groupName }}</strong>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="统计周期" min-width="190">
          <template #default="{ row }">{{ weekRange(row.weekStart, row.weekEnd) }}</template>
        </el-table-column>
        <el-table-column label="已通过/成员数" min-width="190">
          <template #default="{ row }">
            <div class="project-week-progress">
              <strong>{{ row.approvedCount }}/{{ row.totalMemberCount }}</strong>
              <el-progress
                :percentage="packagePercentage(row)"
                :stroke-width="6"
                :show-text="false"
                :status="packageComplete(row) ? 'success' : undefined"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="packageHasPrintable(row) ? 'success' : 'info'" effect="plain">
              {{ packageHasPrintable(row) ? '可打印' : '暂无可打印' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="未完成成员" min-width="210">
          <template #default="{ row }">
            <span v-if="packageComplete(row)" class="project-week-complete">全部成员已通过</span>
            <el-tooltip v-else :content="pendingMemberText(row)" placement="top" :show-after="250">
              <span class="project-week-pending">{{ pendingMemberPreview(row) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right" align="center">
          <template #default="{ row }">
            <div class="project-week-row-actions">
              <el-button
                type="primary"
                link
                :icon="Download"
                :loading="planExcelLoadingGroupId === row.groupId"
                :disabled="!packageHasPrintable(row)"
                @click="downloadProjectWeekPlan(row)"
              >下载 Excel</el-button>
              <el-button
                type="primary"
                link
                :icon="Printer"
                :loading="printLoadingGroupId === row.groupId"
                :disabled="!packageHasPrintable(row)"
                @click="printProjectWeekPlan(row)"
              >打印</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      </section>

      <section class="project-week-package project-week-result-package" aria-labelledby="project-week-result-package-title">
      <div class="project-week-package__head project-week-result-package__head">
        <div>
          <h2 id="project-week-result-package-title">项目组周成果</h2>
          <p>只统计项目组成员，不含领导；可打印或下载已通过成员的周成果。</p>
        </div>
        <span class="project-week-shared-period">统计周：{{ selectedWeekRange }}</span>
      </div>

      <el-table
        v-loading="resultPackageLoading"
        class="project-week-package__table"
        :data="weekResultPackages"
        row-key="groupId"
        empty-text="当前统计周暂无可汇总的项目组周成果"
      >
        <el-table-column label="项目组" min-width="150">
          <template #default="{ row }">
            <div class="project-week-group">
              <span>{{ employeeInitial(row.groupName) }}</span>
              <strong>{{ row.groupName }}</strong>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="统计周期" min-width="190">
          <template #default="{ row }">{{ weekRange(row.weekStart, row.weekEnd) }}</template>
        </el-table-column>
        <el-table-column label="已通过/成员数" min-width="190">
          <template #default="{ row }">
            <div class="project-week-progress">
              <strong>{{ row.approvedCount }}/{{ row.totalMemberCount }}</strong>
              <el-progress
                :percentage="packagePercentage(row)"
                :stroke-width="6"
                :show-text="false"
                :status="packageComplete(row) ? 'success' : undefined"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="packageHasPrintable(row) ? 'success' : 'info'" effect="plain">
              {{ packageHasPrintable(row) ? '可打印' : '暂无可打印' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="未完成成员" min-width="210">
          <template #default="{ row }">
            <span v-if="packageComplete(row)" class="project-week-complete">全部成员成果已通过</span>
            <el-tooltip v-else :content="pendingMemberText(row)" placement="top" :show-after="250">
              <span class="project-week-pending">{{ pendingMemberPreview(row) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right" align="center">
          <template #default="{ row }">
            <div class="project-week-row-actions">
              <el-button
                type="primary"
                link
                :icon="Download"
                :loading="resultExcelLoadingGroupId === row.groupId"
                :disabled="!packageHasPrintable(row)"
                @click="downloadProjectWeekResult(row)"
              >下载 Excel</el-button>
              <el-button
                type="primary"
                link
                :icon="Printer"
                :loading="resultPrintLoadingGroupId === row.groupId"
                :disabled="!packageHasPrintable(row)"
                @click="printProjectWeekResult(row)"
              >打印</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      </section>
    </section>

    <section v-if="printPackage" class="project-week-print-sheet" aria-hidden="true">
      <table>
        <thead>
          <tr class="project-week-print-title-row">
            <th :colspan="projectWeekPrintColumns.length">项目组人员工作周计划及完成情况表</th>
          </tr>
          <tr class="project-week-print-meta-row">
            <th colspan="2">项目名称</th>
            <td colspan="2" class="project-week-print-project">&nbsp;</td>
            <th>统计周期</th>
            <td colspan="2">{{ printWeekPeriod }}</td>
          </tr>
          <tr>
            <th v-for="column in projectWeekPrintColumns" :key="column.key" :class="column.headerClass">{{ column.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in printPackage.rows" :key="row.ownerId">
            <td v-for="column in projectWeekPrintColumns" :key="column.key" :class="column.cellClass">
              {{ column.value(row, index) }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <DailyPlanPrintSheet v-if="dayPrintPackage" :rows="dayPrintRows" :plan-date="dayPrintPeriod" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Calendar, Download, EditPen, Printer, Refresh, Search } from '@element-plus/icons-vue'
import DailyPlanPrintSheet from '@/components/DailyPlanPrintSheet.vue'
import {
  getLeaderProjectDayPlanPackageApi,
  listLeaderEmployeesApi,
  listLeaderProjectDayPlanPackagesApi,
  type LeaderEmployeeSummary,
  type ProjectDayPlanPackage,
  type ProjectDayPlanPackageDetail,
} from '@/api/leader'
import { errorMessage } from '@/api/performance'
import {
  downloadLeaderProjectWeekPlanExcelApi,
  getLeaderProjectWeekPlanPackageApi,
  listLeaderProjectWeekPlanPackagesApi,
  type ProjectWeekPlanPackage,
  type ProjectWeekPlanPackageDetail,
  type ProjectWeekPlanPrintRow,
} from '@/api/weekPlan'
import {
  downloadLeaderProjectWeekResultExcelApi,
  getLeaderProjectWeekResultPackageApi,
  listLeaderProjectWeekResultPackagesApi,
  type ProjectWeekResultPackage,
  type ProjectWeekResultPackageDetail,
  type ProjectWeekResultPrintRow,
} from '@/api/weekResult'
import { useLeaderOrgScope } from '@/composables/useLeaderOrgScope'
import { saveBlob } from '@/utils/download'

const router = useRouter()
const route = useRoute()
const { orgOptions, scopeOrgId, orgLoading, loadOrgScope } = useLeaderOrgScope()
const employees = ref<LeaderEmployeeSummary[]>([])
const keyword = ref('')
const loading = ref(false)
const dayPackageLoading = ref(false)
const packageLoading = ref(false)
const resultPackageLoading = ref(false)
const dayPlanDate = ref(formatDate(new Date()))
const packageWeekStart = ref(currentMonday())
const dayPackages = ref<ProjectDayPlanPackage[]>([])
const weekPackages = ref<ProjectWeekPlanPackage[]>([])
const weekResultPackages = ref<ProjectWeekResultPackage[]>([])
const printPackage = ref<ProjectWeekPlanPackageDetail | ProjectWeekResultPackageDetail | null>(null)
const dayPrintPackage = ref<ProjectDayPlanPackageDetail | null>(null)
const dayPrintLoadingGroupId = ref<number | null>(null)
const printLoadingGroupId = ref<number | null>(null)
const resultPrintLoadingGroupId = ref<number | null>(null)
const planExcelLoadingGroupId = ref<number | null>(null)
const resultExcelLoadingGroupId = ref<number | null>(null)
const hasFilter = computed(() => scopeOrgId.value != null || Boolean(keyword.value.trim()))
const selectedWeekRange = computed(() => weekRange(packageWeekStart.value, shiftDate(packageWeekStart.value, 6)))
const printWeekPeriod = computed(() => {
  const info = printPackage.value?.packageInfo
  return info ? chineseWeekRange(info.weekStart, info.weekEnd) : ''
})
const dayPrintPeriod = computed(() => dayPrintPackage.value?.packageInfo.planDate || '')
const dayPrintRows = computed(() => (dayPrintPackage.value?.rows || []).map((row) => ({
  rowKey: row.ownerId,
  employeeName: row.employeeName,
  workContent: row.workContent,
})))

type ProjectWeekPrintRow = ProjectWeekPlanPrintRow | ProjectWeekResultPrintRow

const projectWeekPrintColumns: Array<{
  key: string
  label: string
  headerClass?: string
  cellClass?: string
  value: (row: ProjectWeekPrintRow, index: number) => string | number
}> = [
  { key: 'seq', label: '序号', headerClass: 'print-compact-column', cellClass: 'print-center print-compact-column', value: (_row, index) => index + 1 },
  { key: 'name', label: '姓名', headerClass: 'print-compact-column', cellClass: 'print-center print-compact-column', value: (row) => row.employeeName },
  { key: 'work', label: '工作内容', cellClass: 'print-work-content', value: (row) => row.workContent },
  { key: 'deliverable', label: '交付物', cellClass: 'print-multiline-cell', value: (row) => printDeliverableText(row) },
  { key: 'completion', label: '完成情况', cellClass: 'print-multiline-cell', value: (row) => printCompletionText(row) },
  { key: 'remark', label: '备注', value: (row) => printRemarkText(row) },
  { key: 'signature', label: '签字', headerClass: 'print-compact-column', cellClass: 'print-center print-compact-column', value: () => '' },
]

async function loadEmployees(showMessage = false) {
  loading.value = true
  try {
    employees.value = await listLeaderEmployeesApi({
      scopeOrgId: scopeOrgId.value,
      keyword: keyword.value.trim() || undefined,
    })
    if (showMessage) ElMessage.success('直属员工列表已刷新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadDayPackages(showMessage = false) {
  dayPackageLoading.value = true
  try {
    dayPackages.value = await listLeaderProjectDayPlanPackagesApi({
      scopeOrgId: scopeOrgId.value,
      planDate: dayPlanDate.value,
    })
    if (showMessage) ElMessage.success('项目组日计划状态已刷新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    dayPackageLoading.value = false
  }
}

async function loadWeekPackages(showMessage = false) {
  packageLoading.value = true
  try {
    weekPackages.value = await listLeaderProjectWeekPlanPackagesApi({
      scopeOrgId: scopeOrgId.value,
      weekStart: packageWeekStart.value,
    })
    if (showMessage) ElMessage.success('项目组周计划状态已刷新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    packageLoading.value = false
  }
}

async function loadWeekResultPackages(showMessage = false) {
  resultPackageLoading.value = true
  try {
    weekResultPackages.value = await listLeaderProjectWeekResultPackagesApi({
      scopeOrgId: scopeOrgId.value,
      weekStart: packageWeekStart.value,
    })
    if (showMessage) ElMessage.success('项目组周成果状态已刷新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    resultPackageLoading.value = false
  }
}

async function refreshWorkbench() {
  await Promise.all([loadEmployees(false), loadDayPackages(false), loadWeekPackages(false), loadWeekResultPackages(false)])
  ElMessage.success('工作台已刷新')
}

async function applyWorkbenchFilter() {
  await Promise.all([loadEmployees(true), loadDayPackages(false), loadWeekPackages(false), loadWeekResultPackages(false)])
}

async function resetFilter() {
  scopeOrgId.value = undefined
  keyword.value = ''
  await Promise.all([loadEmployees(false), loadDayPackages(false), loadWeekPackages(false), loadWeekResultPackages(false)])
}

async function changeDayPackageDate(value?: string) {
  dayPlanDate.value = value || formatDate(new Date())
  await loadDayPackages(false)
}

async function changePackageWeek(value?: string) {
  packageWeekStart.value = mondayOf(value || packageWeekStart.value)
  await Promise.all([loadWeekPackages(false), loadWeekResultPackages(false)])
}

async function printProjectWeekPlan(row: ProjectWeekPlanPackage) {
  if (!packageHasPrintable(row)) {
    ElMessage.warning('暂无已通过的周计划，不能打印')
    return
  }
  printLoadingGroupId.value = row.groupId
  try {
    printPackage.value = await getLeaderProjectWeekPlanPackageApi(row.groupId, {
      weekStart: packageWeekStart.value,
    })
    await nextTick()
    window.print()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    printPackage.value = null
    printLoadingGroupId.value = null
  }
}

async function printProjectDayPlan(row: ProjectDayPlanPackage) {
  if (!packageHasPrintable(row)) {
    ElMessage.warning('暂无已通过的日计划，不能打印')
    return
  }
  dayPrintLoadingGroupId.value = row.groupId
  try {
    dayPrintPackage.value = await getLeaderProjectDayPlanPackageApi(row.groupId, {
      planDate: dayPlanDate.value,
    })
    await nextTick()
    window.print()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    dayPrintPackage.value = null
    dayPrintLoadingGroupId.value = null
  }
}

async function printProjectWeekResult(row: ProjectWeekResultPackage) {
  if (!packageHasPrintable(row)) {
    ElMessage.warning('暂无已通过的周成果，不能打印')
    return
  }
  resultPrintLoadingGroupId.value = row.groupId
  try {
    printPackage.value = await getLeaderProjectWeekResultPackageApi(row.groupId, {
      weekStart: packageWeekStart.value,
    })
    await nextTick()
    window.print()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    printPackage.value = null
    resultPrintLoadingGroupId.value = null
  }
}

async function downloadProjectWeekPlan(row: ProjectWeekPlanPackage) {
  if (!packageHasPrintable(row)) {
    ElMessage.warning('暂无已通过的成员周计划，不能下载')
    return
  }
  planExcelLoadingGroupId.value = row.groupId
  try {
    const blob = await downloadLeaderProjectWeekPlanExcelApi(row.groupId, {
      weekStart: packageWeekStart.value,
    })
    saveBlob(blob, packageExcelFileName(row.groupName, '周计划', row.weekStart))
    ElMessage.success('周计划 Excel 已下载')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    planExcelLoadingGroupId.value = null
  }
}

async function downloadProjectWeekResult(row: ProjectWeekResultPackage) {
  if (!packageHasPrintable(row)) {
    ElMessage.warning('暂无已通过的成员周成果，不能下载')
    return
  }
  resultExcelLoadingGroupId.value = row.groupId
  try {
    const blob = await downloadLeaderProjectWeekResultExcelApi(row.groupId, {
      weekStart: packageWeekStart.value,
    })
    saveBlob(blob, packageExcelFileName(row.groupName, '周成果', row.weekStart))
    ElMessage.success('周成果 Excel 已下载')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    resultExcelLoadingGroupId.value = null
  }
}

function openEmployee(row: LeaderEmployeeSummary) {
  router.push({
    path: `/leader/employees/${row.ownerId}/work`,
    query: { fromOrg: scopeOrgId.value == null ? undefined : String(scopeOrgId.value) },
  })
}

function writeMonthPlan() {
  router.push('/leader/my-month-plans/new/edit')
}

function writeWeekPlan() {
  router.push('/leader/my-week-plans')
}

function writeDayPlan() {
  router.push('/leader/my-day-plans')
}

function employeeInitial(name: string) {
  return name?.trim().slice(-1) || '员'
}

function formatDateTime(value?: string) {
  if (!value) return '暂无工作记录'
  return value.replace('T', ' ').slice(0, 16)
}

function formatDate(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function parseDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}

function mondayOf(value: string) {
  const date = parseDate(value)
  const offset = (date.getDay() + 6) % 7
  date.setDate(date.getDate() - offset)
  return formatDate(date)
}

function currentMonday() {
  return mondayOf(formatDate(new Date()))
}

function shiftDate(value: string, days: number) {
  const date = parseDate(value)
  date.setDate(date.getDate() + days)
  return formatDate(date)
}

function weekRange(start: string, end: string) {
  return `${start} 至 ${end}`
}

function packageExcelFileName(groupName: string, packageType: string, weekStart: string) {
  return `${groupName}_${packageType}_${weekStart}.xlsx`.replace(/[\\/:*?"<>|]/g, '_')
}

function chineseWeekRange(start: string, end: string) {
  const display = (value: string) => {
    const [year, month, day] = value.split('-').map(Number)
    return `${year} 年 ${month} 月 ${day} 日`
  }
  return `${display(start)} - ${display(end)}`
}

function packagePercentage(row: ProjectDayPlanPackage | ProjectWeekPlanPackage | ProjectWeekResultPackage) {
  return row.totalMemberCount ? Math.round((packagePrintableCount(row) / row.totalMemberCount) * 100) : 0
}

function packagePrintableCount(row: ProjectDayPlanPackage | ProjectWeekPlanPackage | ProjectWeekResultPackage) {
  return Number(('submittedCount' in row ? row.submittedCount : row.approvedCount) || 0)
}

function packageHasPrintable(row: ProjectDayPlanPackage | ProjectWeekPlanPackage | ProjectWeekResultPackage) {
  return packagePrintableCount(row) > 0
}

function packageComplete(row: ProjectDayPlanPackage | ProjectWeekPlanPackage | ProjectWeekResultPackage) {
  return row.totalMemberCount > 0 && packagePrintableCount(row) >= row.totalMemberCount
}

function pendingMemberText(row: ProjectDayPlanPackage | ProjectWeekPlanPackage | ProjectWeekResultPackage) {
  return row.pendingMembers.map((item) => `${item.employeeName}（${item.statusLabel}）`).join('、') || '暂无成员'
}

function pendingMemberPreview(row: ProjectDayPlanPackage | ProjectWeekPlanPackage | ProjectWeekResultPackage) {
  const names = row.pendingMembers.slice(0, 3).map((item) => item.employeeName).join('、')
  return row.pendingMembers.length > 3 ? `${names} 等 ${row.pendingMembers.length} 人` : names || '暂无成员'
}

function printCompletionText(row: ProjectWeekPlanPrintRow | ProjectWeekResultPrintRow) {
  return 'completionStatus' in row ? row.completionStatus || '' : ''
}

function printDeliverableText(row: ProjectWeekPlanPrintRow | ProjectWeekResultPrintRow) {
  const deliverable = 'deliverable' in row ? row.deliverable || '' : ''
  const quality = 'quality' in row ? row.quality || '' : ''
  if (!quality) return deliverable
  if (!deliverable) return `质量：${quality}`
  return `交付物：${deliverable}\n质量：${quality}`
}

function printRemarkText(row: ProjectWeekPlanPrintRow | ProjectWeekResultPrintRow) {
  return 'remark' in row ? row.remark || '' : ''
}

onMounted(async () => {
  try {
    await loadOrgScope()
    const requestedOrgId = Number(route.query.org)
    scopeOrgId.value = Number.isFinite(requestedOrgId)
      && orgOptions.value.some((item) => item.value === requestedOrgId)
      ? requestedOrgId
      : undefined
    await Promise.all([loadEmployees(false), loadDayPackages(false), loadWeekPackages(false), loadWeekResultPackages(false)])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
})
</script>

<style scoped>
.leader-people-page {
  display: grid;
  width: min(100%, var(--content-max));
  margin-inline: auto;
  gap: 24px;
}

.leader-people-panel,
.leader-project-day-panel,
.leader-project-week-panel {
  min-height: 0;
}

.leader-project-day-panel {
  padding-bottom: 20px;
}

.project-week-package {
  margin-bottom: 24px;
  padding: 2px 0 24px;
  border-bottom: 1px solid var(--line);
}

.project-week-package__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 14px;
}

.project-week-package__head h2 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
  letter-spacing: 0;
}

.project-week-package__head p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.project-week-selector {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 12px;
  border: 1px solid #cfdad4;
  border-radius: 8px;
  background: #f6f8f5;
}

.project-week-selector > span {
  display: grid;
  gap: 3px;
}

.project-week-selector strong {
  color: var(--ink);
  font-size: 12px;
}

.project-week-selector small {
  color: var(--muted);
  font-size: 10px;
  white-space: nowrap;
}

.project-week-selector .el-date-editor {
  width: 185px;
}

.project-day-selector .el-date-editor {
  width: 172px;
}

.project-week-package__table {
  --el-table-border-color: #d9e1dc;
  --el-table-header-bg-color: #f1f5f2;
}

.project-week-result-package {
  margin-bottom: 0;
  padding: 0;
  border-bottom: 0;
}

.project-week-result-package__head {
  align-items: flex-end;
}

.project-week-shared-period {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}

.project-week-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.project-week-group > span {
  display: grid;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 7px;
  color: #fff;
  background: #315e59;
  font-size: 11px;
  font-weight: 750;
}

.project-week-group strong {
  color: var(--ink);
  font-size: 13px;
}

.project-week-progress {
  display: grid;
  grid-template-columns: 45px minmax(80px, 1fr);
  gap: 10px;
  align-items: center;
}

.project-week-progress strong {
  color: var(--ink);
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace;
  font-size: 12px;
}

.project-week-complete {
  color: #2e745f;
  font-size: 12px;
  font-weight: 700;
}

.project-week-pending {
  display: block;
  overflow: hidden;
  color: #6d5c45;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-week-row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  white-space: nowrap;
}

.project-week-row-actions :deep(.el-button) {
  margin-left: 0;
}

.project-week-print-sheet {
  display: none;
}

.leader-people-page__header {
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}

.leader-people-page__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.leader-people-filter {
  padding-left: 16px;
}

.leader-people-filter::before {
  display: none;
}

.leader-people-filter .el-select {
  width: 250px;
}

.leader-people-filter .el-input {
  width: 280px;
}

.leader-people-summary {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 24px;
  margin-bottom: 14px;
}

.leader-people-summary > div {
  display: flex;
  align-items: baseline;
  gap: 7px;
}

.leader-people-summary strong {
  color: var(--ink);
  font: 700 24px/1 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.leader-people-summary span {
  color: var(--muted);
  font-size: 12px;
}

.leader-people-table-wrap {
  overflow: auto;
}

.leader-people-table-wrap :deep(.el-table) {
  min-width: 820px;
}

.employee-name-link {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  max-width: 100%;
  padding: 4px 2px;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.employee-name-link:hover strong,
.employee-name-link:focus-visible strong {
  color: var(--blue);
}

.employee-name-link:focus-visible {
  outline: 2px solid rgb(36 116 166 / 28%);
  outline-offset: 3px;
  border-radius: 6px;
}

.employee-name-link > span:last-child,
.employee-name-link strong,
.employee-name-link small {
  display: block;
  min-width: 0;
}

.employee-name-link strong {
  overflow: hidden;
  color: var(--ink);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.employee-name-link small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.employee-initial {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: #315e59;
  font-size: 12px;
  font-weight: 700;
}

.work-count,
.work-total {
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace;
}

.work-count {
  color: #536b65;
}

.work-count.is-result {
  color: #9a5538;
}

.work-total {
  color: var(--ink);
}

@media (max-width: 900px) {
  .project-week-package__head {
    align-items: stretch;
    flex-direction: column;
  }

  .project-week-selector {
    justify-content: space-between;
  }

  .leader-people-page__actions {
    width: 100%;
  }

  .leader-people-page__actions .el-button {
    flex: 1;
    margin-left: 0;
  }

  .leader-people-summary {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }
}

@page {
  size: A4 landscape;
  margin: 9mm;
}

@media print {
  :global(html),
  :global(body),
  :global(#app),
  :global(.app-shell),
  :global(.main) {
    display: block !important;
    width: 100% !important;
    height: auto !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: visible !important;
    background: #fff !important;
  }

  :global(.sidebar),
  :global(.topbar) {
    display: none !important;
  }

  :global(body *) {
    visibility: hidden !important;
  }

  .leader-people-page {
    display: block !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    background: #fff !important;
  }

  .leader-people-page > :not(.project-week-print-sheet):not(.daily-plan-print-sheet) {
    display: none !important;
  }

  .project-week-print-sheet,
  .project-week-print-sheet * {
    visibility: visible !important;
  }

  .project-week-print-sheet {
    position: static;
    display: block;
    width: 100%;
    margin: 0;
    padding: 0;
    color: #000;
    background: #fff;
    font-family: "Microsoft YaHei", "SimSun", sans-serif;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
    break-after: avoid;
    page-break-after: avoid;
  }

  .project-week-print-sheet .project-week-print-title-row th {
    height: 34px;
    padding: 7px 8px;
    color: #fff;
    background: #5b8ff9;
    font-size: 15px;
    line-height: 1.35;
    text-align: center;
  }

  .project-week-print-sheet .project-week-print-meta-row {
    height: 38px;
    font-size: 10px;
  }

  .project-week-print-sheet .project-week-print-meta-row > * {
    padding: 5px 7px;
    background: #fff;
    vertical-align: middle;
  }

  .project-week-print-sheet .project-week-print-meta-row th {
    background: #eef5fb;
    font-size: 10px;
    text-align: center;
  }

  .project-week-print-sheet .project-week-print-meta-row td:last-child {
    white-space: nowrap;
    text-align: center;
  }

  .project-week-print-sheet table {
    width: 100%;
    border-collapse: collapse;
    table-layout: auto;
    color: #000;
    border: 1px solid #8699ac;
    font-size: 9px;
    line-height: 1.45;
    break-after: avoid;
    page-break-after: avoid;
  }

  .project-week-print-sheet th,
  .project-week-print-sheet td {
    box-sizing: border-box;
    padding: 5px 6px;
    border: 1px solid #8699ac;
    overflow-wrap: anywhere;
    word-break: break-word;
    vertical-align: middle;
  }

  .project-week-print-sheet th {
    background: #d9eaf7;
    font-size: 10px;
    font-weight: 700;
    text-align: center;
  }

  .project-week-print-sheet thead {
    display: table-header-group;
  }

  .project-week-print-sheet tbody tr {
    break-inside: avoid;
    page-break-inside: avoid;
  }

  .project-week-print-sheet tbody td {
    min-height: 38px;
    vertical-align: top;
  }

  .project-week-print-sheet .print-compact-column {
    width: 1%;
    white-space: nowrap;
  }
  .print-center { text-align: center; }
  .print-work-content,
  .print-multiline-cell { white-space: pre-wrap; }
}
</style>
