<template>
  <section v-loading="loading" class="page-panel department-people-page">
    <div class="page-header department-people-page__header">
      <div>
        <h1 class="page-title">部门工作台</h1>
        <p class="page-subtitle">先按组织快速定位成员，再查看项目组月计划与成果完整度。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading || packageLoading" @click="refreshDashboard">刷新</el-button>
    </div>

    <section class="project-plan-package" aria-labelledby="project-plan-package-title">
      <div class="project-plan-package__head">
        <div>
          <h2 id="project-plan-package-title">项目组月计划</h2>
          <p>按计划归属月份统计；项目组全员计划审批通过后，整组内容才会统一开放。</p>
        </div>
        <div class="package-month-filter">
          <span class="package-month-filter__icon" aria-hidden="true">
            <el-icon><Calendar /></el-icon>
          </span>
          <span class="package-month-filter__copy">
            <strong>计划月份</strong>
            <small>同步筛选月计划与成果</small>
          </span>
          <el-date-picker
            v-model="packageMonth"
            type="month"
            format="YYYY年MM月"
            value-format="YYYY-MM"
            :clearable="false"
            placeholder="请选择计划月份"
            aria-label="选择计划月份"
            @change="loadPackages"
          />
        </div>
      </div>

      <el-table
        v-loading="packageLoading"
        class="project-plan-package__table"
        :data="visiblePackages"
        row-key="groupId"
        empty-text="当前范围暂无项目组"
      >
        <el-table-column label="项目组" min-width="160">
          <template #default="{ row }">
            <div class="project-group-name">
              <span class="project-group-mark">{{ projectGroupInitial(row.groupName) }}</span>
              <span><strong>{{ row.groupName }}</strong><small>{{ row.departmentName || '未归属部门' }}</small></span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="planMonth" label="计划月份" width="110" />
        <el-table-column label="已通过/总人数" min-width="205">
          <template #default="{ row }">
            <div class="package-progress">
              <strong>{{ row.approvedCount }}/{{ row.totalMemberCount }}</strong>
              <el-progress
                :percentage="packagePercentage(row)"
                :stroke-width="6"
                :show-text="false"
                :status="row.contentAvailable ? 'success' : undefined"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="packageStatusType(row)" effect="plain">{{ packageStatusLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="待完成人员" min-width="245">
          <template #default="{ row }">
            <span v-if="row.contentAvailable" class="package-complete-text">全员已通过</span>
            <span v-else-if="!row.pendingMembers.length" class="package-pending-empty">暂无成员</span>
            <el-tooltip v-else :content="pendingMemberText(row)" placement="top" :show-after="250">
              <span class="package-pending-text">{{ pendingMemberPreview(row) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right" align="center">
          <template #default="{ row }">
            <el-tooltip
              :disabled="row.contentAvailable"
              content="需项目组所有成员计划审批通过后开放"
              placement="top"
            >
              <span>
                <el-button
                  link
                  type="primary"
                  :icon="row.contentAvailable ? View : Lock"
                  :disabled="!row.contentAvailable"
                  @click="openPackage(row)"
                >
                  查看整组计划
                </el-button>
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="project-result-package" aria-labelledby="project-result-package-title">
      <div class="project-plan-package__head">
        <div>
          <h2 id="project-result-package-title">项目组成果</h2>
          <p>与上方计划共用月份；组内员工和领导的成果全部审批通过后，整组内容才会开放。</p>
        </div>
        <span class="shared-month-note">{{ packageMonth }}</span>
      </div>
      <ProjectResultPackageTable
        :packages="visibleResultPackages"
        :loading="packageLoading"
      />
    </section>

    <section
      class="department-workbench"
      :class="{ 'department-workbench--single-page': !hasEmployeePagination }"
      aria-labelledby="department-members-title"
    >
      <aside v-loading="orgLoading" class="department-org-sidebar" aria-label="组织导航">
        <div class="department-org-sidebar__head">
          <strong>组织导航</strong>
          <span>选择组织后查看该层级及下级员工</span>
        </div>
        <button
          type="button"
          class="all-org-button"
          :class="{ active: selectedOrgId == null }"
          @click="selectAllOrganizations"
        >
          <el-icon><OfficeBuilding /></el-icon>
          <span>全部部门</span>
          <small>{{ allEmployees.length }}</small>
        </button>
        <el-tree
          v-if="orgTree.length"
          class="department-org-tree"
          :data="orgTree"
          node-key="id"
          default-expand-all
          highlight-current
          :expand-on-click-node="false"
          :current-node-key="selectedOrgId"
          @node-click="selectOrganization"
        >
          <template #default="{ data }">
            <span class="department-org-node">
              <el-icon>
                <OfficeBuilding v-if="data.orgType === 'DEPARTMENT'" />
                <FolderOpened v-else />
              </el-icon>
              <span>{{ data.label }}</span>
              <small>{{ organizationEmployeeCounts.get(data.id) || 0 }}</small>
            </span>
          </template>
        </el-tree>
        <el-empty v-else-if="!orgLoading" :image-size="46" description="暂无组织" />
      </aside>

      <div class="department-members">
        <header class="department-members__head">
          <div>
            <span>当前范围</span>
            <h2 id="department-members-title">{{ selectedOrganizationLabel }}</h2>
            <p>点击员工姓名查看计划与成果，进入后可继续按类型、状态和时间筛选。</p>
          </div>
          <dl class="department-members__metrics" aria-label="当前范围工作统计">
            <div><dt>员工</dt><dd>{{ visibleEmployees.length }}</dd></div>
            <div><dt>月计划</dt><dd>{{ visibleMonthPlanCount }}</dd></div>
            <div><dt>成果</dt><dd>{{ visibleResultCount }}</dd></div>
          </dl>
        </header>

        <div class="filter-bar department-people-filter">
          <el-input
            v-model="keyword"
            clearable
            :prefix-icon="Search"
            placeholder="搜索员工姓名或工号"
            @keyup.enter="applyKeyword"
            @clear="applyKeyword"
          />
          <el-button type="primary" :icon="Search" @click="applyKeyword">查询</el-button>
          <el-button :disabled="!hasFilter" @click="resetFilter">重置</el-button>
        </div>

        <div class="department-list-meta" aria-live="polite">
          <span>共 <strong>{{ visibleEmployees.length }}</strong> 名员工</span>
          <span v-if="appliedKeyword">关键词：{{ appliedKeyword }}</span>
        </div>

        <div class="department-people-table-wrap">
          <el-table
            :data="pagedEmployees"
            row-key="ownerId"
            :empty-text="emptyText"
          >
            <el-table-column label="序号" width="58" align="center">
              <template #default="{ $index }">
                <span class="employee-row-index">{{ (employeePageNo - 1) * employeePageSize + $index + 1 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="员工" min-width="150" fixed="left">
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
            <el-table-column prop="orgName" label="所属组织" min-width="105" show-overflow-tooltip />
            <el-table-column label="月计划" width="68" align="center">
              <template #default="{ row }"><span class="work-count">{{ row.monthPlanCount }}</span></template>
            </el-table-column>
            <el-table-column label="周计划" width="68" align="center">
              <template #default="{ row }"><span class="work-count">{{ row.weekPlanCount }}</span></template>
            </el-table-column>
            <el-table-column label="日计划" width="68" align="center">
              <template #default="{ row }"><span class="work-count">{{ row.dayPlanCount }}</span></template>
            </el-table-column>
            <el-table-column label="成果" width="62" align="center">
              <template #default="{ row }"><span class="work-count is-result">{{ row.resultCount }}</span></template>
            </el-table-column>
            <el-table-column label="总记录" width="72" align="center">
              <template #default="{ row }"><strong class="work-total">{{ row.totalWorkCount }}</strong></template>
            </el-table-column>
            <el-table-column label="最近更新" width="142">
              <template #default="{ row }">{{ formatDateTime(row.latestActivityAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="92" fixed="right" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openEmployee(row)">
                  查看<el-icon class="el-icon--right"><ArrowRight /></el-icon>
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="hasEmployeePagination" class="pagination-row department-employee-pagination">
            <el-pagination
              v-model:current-page="employeePageNo"
              v-model:page-size="employeePageSize"
              :page-sizes="[10, 20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              :total="visibleEmployees.length"
              background
              @size-change="changeEmployeePageSize"
            />
          </div>
        </div>
      </div>
    </section>

    <el-drawer
      v-model="packageDetailVisible"
      title="项目组完整月计划"
      size="min(1120px, calc(100vw - 24px))"
      @closed="closePackageDetail"
    >
      <el-skeleton v-if="packageDetailLoading" :rows="8" animated />
      <div v-else-if="packageDetail" class="package-detail">
        <header class="package-detail__summary">
          <div>
            <span>{{ packageDetail.packageInfo.departmentName || '所属部门' }}</span>
            <h2>{{ packageDetail.packageInfo.groupName }}</h2>
            <p>{{ packageDetail.packageInfo.planMonth }} · 全组 {{ packageDetail.packageInfo.totalMemberCount }} 人计划均已审批通过</p>
          </div>
          <el-tag type="success" effect="plain">完整开放</el-tag>
        </header>

        <el-collapse v-model="activePlanNames" class="package-plan-collapse">
          <el-collapse-item v-for="plan in packageDetail.plans" :key="plan.id" :name="plan.id">
            <template #title>
              <div class="package-plan-title">
                <span class="employee-initial">{{ employeeInitial(plan.employeeName) }}</span>
                <span><strong>{{ plan.employeeName }}</strong><small>{{ plan.employeeNo || '未填写工号' }}</small></span>
                <el-tag type="success" size="small" effect="plain">V{{ plan.version }} 已通过</el-tag>
                <time>{{ formatDateTime(plan.approvedAt) }}</time>
              </div>
            </template>
            <div class="package-plan-content">
              <dl class="package-plan-meta">
                <div><dt>审批人</dt><dd>{{ plan.approverName || '未记录' }}</dd></div>
                <div><dt>审批意见</dt><dd>{{ plan.leaderComment || '审批通过' }}</dd></div>
              </dl>
              <el-table :data="plan.items" border empty-text="暂无计划事项">
                <el-table-column type="index" label="#" width="55" />
                <el-table-column label="任务" min-width="150">
                  <template #default="{ row }"><div class="package-cell-text compact">{{ row.taskName || '未填写' }}</div></template>
                </el-table-column>
                <el-table-column label="任务内容" min-width="220">
                  <template #default="{ row }">
                    <MonthPlanTaskContentView class="package-cell-text" :content="row.taskContent" empty-text="未填写" />
                  </template>
                </el-table-column>
                <el-table-column label="考核标准（交付物、质量）" min-width="320">
                  <template #default="{ row }"><div class="package-cell-text">{{ formatMonthPlanStandard(row.deliverable, row.quality) }}</div></template>
                </el-table-column>
                <el-table-column label="绩效权重" width="100" align="right">
                  <template #default="{ row }">{{ row.performanceWeight ?? 0 }}%</template>
                </el-table-column>
                <el-table-column label="预估价值" width="120" align="right">
                  <template #default="{ row }">{{ formatCurrency(row.estimatedValue) }}</template>
                </el-table-column>
                <el-table-column label="完成时间段" width="126" align="center">
                  <template #default="{ row }">{{ formatMonthPlanPeriod(row.estimatedStartDate, row.estimatedEndDate) }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Calendar, FolderOpened, Lock, OfficeBuilding, Refresh, Search, View } from '@element-plus/icons-vue'
import {
  getProjectMonthPlanPackageApi,
  listDepartmentEmployeesApi,
  listProjectMonthPlanPackagesApi,
  listProjectResultPackagesApi,
  type DepartmentEmployeeSummary,
  type ProjectMonthPlanPackage,
  type ProjectMonthPlanPackageDetail,
  type ProjectResultPackage,
} from '@/api/department'
import type { OrgNode } from '@/api/performance'
import { errorMessage } from '@/api/performance'
import { useDepartmentOrgScope } from '@/composables/useDepartmentOrgScope'
import ProjectResultPackageTable from '@/components/ProjectResultPackageTable.vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'

const route = useRoute()
const router = useRouter()
const { orgTree, orgOptions, selectedOrgId, orgLoading, loadOrgScope } = useDepartmentOrgScope(false)
const allEmployees = ref<DepartmentEmployeeSummary[]>([])
const keyword = ref('')
const appliedKeyword = ref('')
const employeePageNo = ref(1)
const employeePageSize = ref(10)
const loading = ref(false)
const packageMonth = ref(currentMonth())
const packages = ref<ProjectMonthPlanPackage[]>([])
const resultPackages = ref<ProjectResultPackage[]>([])
const packageLoading = ref(false)
const packageDetailVisible = ref(false)
const packageDetailLoading = ref(false)
const packageDetail = ref<ProjectMonthPlanPackageDetail>()
const activePlanNames = ref<string[]>([])

const organizationEmployeeCounts = computed(() => {
  const directCounts = new Map<number, number>()
  allEmployees.value.forEach((employee) => {
    directCounts.set(employee.orgId, (directCounts.get(employee.orgId) || 0) + 1)
  })
  const totals = new Map<number, number>()
  const countNode = (node: OrgNode): number => {
    const total = (directCounts.get(node.id) || 0)
      + (node.children || []).reduce((sum, child) => sum + countNode(child), 0)
    totals.set(node.id, total)
    return total
  }
  orgTree.value.forEach(countNode)
  return totals
})

const selectedOrganizationIds = computed(() => {
  if (selectedOrgId.value == null) return null
  const selected = findOrganization(orgTree.value, selectedOrgId.value)
  if (!selected) return new Set<number>()
  const ids = new Set<number>()
  collectOrganizationIds(selected, ids)
  return ids
})

const visibleEmployees = computed(() => {
  const normalizedKeyword = appliedKeyword.value.trim().toLowerCase()
  return allEmployees.value.filter((employee) => {
    if (selectedOrganizationIds.value && !selectedOrganizationIds.value.has(employee.orgId)) return false
    if (!normalizedKeyword) return true
    return employee.employeeName.toLowerCase().includes(normalizedKeyword)
      || (employee.employeeNo || '').toLowerCase().includes(normalizedKeyword)
  })
})

const pagedEmployees = computed(() => {
  const start = (employeePageNo.value - 1) * employeePageSize.value
  return visibleEmployees.value.slice(start, start + employeePageSize.value)
})

const visiblePackages = computed(() => packages.value.filter((item) => (
  !selectedOrganizationIds.value || selectedOrganizationIds.value.has(item.groupId)
)))

const visibleResultPackages = computed(() => resultPackages.value.filter((item) => (
  !selectedOrganizationIds.value || selectedOrganizationIds.value.has(item.groupId)
)))

const visibleMonthPlanCount = computed(() => visibleEmployees.value.reduce(
  (total, employee) => total + Number(employee.monthPlanCount || 0),
  0,
))

const visibleResultCount = computed(() => visibleEmployees.value.reduce(
  (total, employee) => total + Number(employee.resultCount || 0),
  0,
))

const hasEmployeePagination = computed(() => visibleEmployees.value.length > employeePageSize.value)

const hasFilter = computed(() => selectedOrgId.value != null || Boolean(keyword.value.trim()) || Boolean(appliedKeyword.value))
const selectedOrganizationLabel = computed(() => {
  if (selectedOrgId.value == null) return '全部部门'
  return orgOptions.value.find((item) => item.value === selectedOrgId.value)?.label || '所选组织'
})
const emptyText = computed(() => hasFilter.value
  ? '当前筛选条件下没有员工'
  : '当前部门及下级组织暂无员工，请先在员工管理中配置所属组织')

async function loadEmployees(showMessage = false) {
  loading.value = true
  try {
    allEmployees.value = await listDepartmentEmployeesApi()
    const lastPage = Math.max(1, Math.ceil(visibleEmployees.value.length / employeePageSize.value))
    employeePageNo.value = Math.min(employeePageNo.value, lastPage)
    if (showMessage) ElMessage.success('部门员工列表已刷新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadPackages() {
  packageLoading.value = true
  try {
    const [planPackages, projectResultPackages] = await Promise.all([
      listProjectMonthPlanPackagesApi({ planMonth: packageMonth.value }),
      listProjectResultPackagesApi({ planMonth: packageMonth.value }),
    ])
    packages.value = planPackages
    resultPackages.value = projectResultPackages
    syncFilterRoute()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    packageLoading.value = false
  }
}

async function refreshDashboard() {
  await Promise.all([loadEmployees(false), loadPackages()])
  ElMessage.success('部门工作台已刷新')
}

function applyKeyword() {
  appliedKeyword.value = keyword.value.trim()
  employeePageNo.value = 1
  syncFilterRoute()
}

function resetFilter() {
  selectedOrgId.value = undefined
  keyword.value = ''
  appliedKeyword.value = ''
  employeePageNo.value = 1
  syncFilterRoute()
}

function selectOrganization(node: OrgNode) {
  selectedOrgId.value = node.id
  employeePageNo.value = 1
  syncFilterRoute()
}

function selectAllOrganizations() {
  selectedOrgId.value = undefined
  employeePageNo.value = 1
  syncFilterRoute()
}

function changeEmployeePageSize(size: number) {
  employeePageSize.value = size
  employeePageNo.value = 1
}

function syncFilterRoute() {
  void router.replace({
    path: '/department/dashboard',
    query: {
      org: selectedOrgId.value == null ? undefined : String(selectedOrgId.value),
      keyword: appliedKeyword.value || undefined,
      planMonth: packageMonth.value,
    },
  })
}

async function openPackage(row: ProjectMonthPlanPackage) {
  if (!row.contentAvailable) return
  packageDetailVisible.value = true
  packageDetailLoading.value = true
  packageDetail.value = undefined
  activePlanNames.value = []
  try {
    const detail = await getProjectMonthPlanPackageApi(row.groupId, { planMonth: row.planMonth })
    packageDetail.value = detail
    activePlanNames.value = detail.plans.length ? [detail.plans[0].id] : []
  } catch (error) {
    packageDetailVisible.value = false
    ElMessage.error(errorMessage(error))
    await loadPackages()
  } finally {
    packageDetailLoading.value = false
  }
}

function closePackageDetail() {
  packageDetail.value = undefined
  activePlanNames.value = []
}

function packagePercentage(row: ProjectMonthPlanPackage) {
  if (!row.totalMemberCount) return 0
  return Math.round((row.approvedCount / row.totalMemberCount) * 100)
}

function packageStatusLabel(row: ProjectMonthPlanPackage) {
  if (row.status === 'OPEN') return '完整开放'
  if (row.status === 'EMPTY') return '暂无成员'
  return '等待集齐'
}

function packageStatusType(row: ProjectMonthPlanPackage): 'success' | 'warning' | 'info' {
  if (row.status === 'OPEN') return 'success'
  if (row.status === 'EMPTY') return 'info'
  return 'warning'
}

function pendingMemberText(row: ProjectMonthPlanPackage) {
  return row.pendingMembers.map((member) => `${member.employeeName}（${member.statusLabel}）`).join('、')
}

function pendingMemberPreview(row: ProjectMonthPlanPackage) {
  const names = row.pendingMembers
    .slice(0, 3)
    .map((member) => `${member.employeeName}（${member.statusLabel}）`)
    .join('、')
  return row.pendingMembers.length > 3 ? `${names} 等 ${row.pendingMembers.length} 人` : names
}

function projectGroupInitial(name: string) {
  return name?.trim().slice(0, 1) || '组'
}

function openEmployee(row: DepartmentEmployeeSummary) {
  router.push({
    path: `/department/employees/${row.ownerId}/work`,
    query: {
      fromOrg: selectedOrgId.value == null ? undefined : String(selectedOrgId.value),
      fromKeyword: appliedKeyword.value || undefined,
    },
  })
}

function findOrganization(nodes: OrgNode[], id: number): OrgNode | undefined {
  for (const node of nodes) {
    if (node.id === id) return node
    const child = findOrganization(node.children || [], id)
    if (child) return child
  }
  return undefined
}

function collectOrganizationIds(node: OrgNode, result: Set<number>) {
  result.add(node.id)
  for (const child of node.children || []) {
    collectOrganizationIds(child, result)
  }
}

function employeeInitial(name: string) {
  return name?.trim().slice(-1) || '员'
}

function formatDateTime(value?: string) {
  if (!value) return '暂无工作记录'
  return value.replace('T', ' ').slice(0, 16)
}

function formatCurrency(value?: number) {
  return `¥${Number(value || 0).toLocaleString('zh-CN')}`
}

function currentMonth() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

onMounted(async () => {
  try {
    await loadOrgScope()
    const requestedOrgId = Number(route.query.org)
    selectedOrgId.value = Number.isFinite(requestedOrgId)
      && orgOptions.value.some((item) => item.value === requestedOrgId)
      ? requestedOrgId
      : undefined
    keyword.value = typeof route.query.keyword === 'string' ? route.query.keyword : ''
    appliedKeyword.value = keyword.value.trim()
    packageMonth.value = typeof route.query.planMonth === 'string' && /^\d{4}-\d{2}$/.test(route.query.planMonth)
      ? route.query.planMonth
      : currentMonth()
    await Promise.all([loadEmployees(false), loadPackages()])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
})
</script>

<style scoped>
.department-people-page {
  display: flex;
  flex-direction: column;
}

.department-people-page__header {
  order: 0;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}

.department-workbench { order: 1; }
.project-plan-package {
  order: 2;
  padding: 22px 0;
  border-top: 1px solid var(--line);
  border-bottom: 0;
}

.project-result-package {
  order: 3;
  padding: 22px 0 0;
  border-top: 1px solid var(--line);
}

.shared-month-note {
  color: var(--muted);
  font: 650 13px/1.4 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.project-plan-package__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 14px;
}

.project-plan-package__head h2,
.package-detail__summary h2 {
  margin: 0;
  color: var(--ink);
  font-size: 17px;
  line-height: 1.35;
}

.project-plan-package__head p,
.package-detail__summary p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.package-month-filter {
  display: flex;
  min-width: 380px;
  align-items: center;
  gap: 10px;
  padding: 10px 11px;
  border: 1px solid #b8cbc5;
  border-radius: 8px;
  background: #f1f7f4;
}

.package-month-filter__icon {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 7px;
  color: #fff;
  background: #2f7569;
  font-size: 17px;
}

.package-month-filter__copy {
  display: grid;
  min-width: 112px;
  gap: 2px;
}

.package-month-filter__copy strong {
  color: var(--ink);
  font-size: 13px;
  line-height: 1.35;
}

.package-month-filter__copy small {
  color: #61746e;
  font-size: 10px;
  line-height: 1.35;
}

.package-month-filter .el-date-editor {
  width: 174px;
  margin-left: auto;
}

.package-month-filter :deep(.el-input__wrapper) {
  min-height: 36px;
  box-shadow: 0 0 0 1px #8eada5 inset;
}

.package-month-filter :deep(.el-input__inner) {
  color: #20342f;
  font-weight: 700;
}

.project-plan-package__table {
  border: 1px solid var(--line);
  border-radius: 7px;
}

.project-group-name,
.package-plan-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.project-group-name > span:last-child,
.project-group-name strong,
.project-group-name small,
.package-plan-title > span:nth-child(2),
.package-plan-title strong,
.package-plan-title small {
  display: block;
  min-width: 0;
}

.project-group-name strong,
.package-plan-title strong {
  overflow: hidden;
  color: var(--ink);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-group-name small,
.package-plan-title small {
  margin-top: 2px;
  color: var(--muted);
  font-size: 10px;
}

.project-group-mark {
  display: grid;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 5px;
  color: #fff;
  background: #315e59;
  font-size: 12px;
  font-weight: 700;
}

.package-progress {
  display: grid;
  grid-template-columns: 42px minmax(80px, 1fr);
  align-items: center;
  gap: 10px;
}

.package-progress strong {
  color: var(--ink);
  font: 700 12px/1 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.package-pending-text,
.package-pending-empty,
.package-complete-text {
  display: block;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.package-pending-text {
  color: #8a5a22;
  cursor: help;
}

.package-pending-empty {
  color: var(--muted);
}

.package-complete-text {
  color: #28725f;
}

.package-detail {
  padding: 0 4px 28px;
}

.package-detail__summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 0 0 18px;
  border-bottom: 1px solid var(--line);
}

.package-detail__summary > div > span {
  display: block;
  margin-bottom: 4px;
  color: #31705f;
  font-size: 11px;
  font-weight: 700;
}

.package-plan-collapse {
  border-top: 0;
}

.package-plan-collapse :deep(.el-collapse-item__header) {
  min-height: 64px;
  height: auto;
  padding: 8px 2px;
  line-height: normal;
}

.package-plan-title {
  width: 100%;
  padding-right: 14px;
}

.package-plan-title > span:nth-child(2) {
  width: 150px;
}

.package-plan-title time {
  margin-left: auto;
  color: var(--muted);
  font: 500 11px/1.4 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.package-plan-content {
  padding: 2px 0 20px;
}

.package-plan-meta {
  display: grid;
  grid-template-columns: minmax(130px, 0.7fr) minmax(0, 1.3fr);
  margin: 0 0 14px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fbfcfb;
}

.package-plan-meta > div {
  min-width: 0;
  padding: 11px 13px;
}

.package-plan-meta > div + div {
  border-left: 1px solid var(--line);
}

.package-plan-meta dt {
  margin-bottom: 5px;
  color: var(--muted);
  font-size: 10px;
}

.package-plan-meta dd {
  margin: 0;
  color: var(--ink);
  font-size: 12px;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.package-cell-text {
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.package-cell-text.compact {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.department-workbench {
  display: grid;
  grid-template-columns: 238px minmax(0, 1fr);
  gap: 20px;
  padding-top: 20px;
}

.department-workbench--single-page .department-org-sidebar {
  position: relative;
  top: auto;
  align-self: stretch;
}

.department-org-sidebar {
  position: sticky;
  top: 82px;
  align-self: start;
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #f8faf7;
}

.department-org-sidebar__head {
  display: grid;
  gap: 4px;
  padding: 15px 14px 11px;
  border-bottom: 1px solid var(--line);
}

.department-org-sidebar__head strong {
  color: var(--ink);
  font-size: 14px;
}

.department-org-sidebar__head span {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.45;
}

.all-org-button {
  display: inline-flex;
  width: calc(100% - 20px);
  min-height: 34px;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  margin: 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--muted);
  background: #fff;
  cursor: pointer;
}

.all-org-button:hover,
.all-org-button:focus-visible,
.all-org-button.active {
  border-color: #86aaa0;
  color: var(--blue);
  background: #f3f8f5;
}

.all-org-button:focus-visible {
  outline: 2px solid rgb(36 116 166 / 22%);
  outline-offset: 2px;
}

.all-org-button span {
  color: inherit;
}

.all-org-button small {
  min-width: 20px;
  padding: 2px 5px;
  border-radius: 4px;
  color: inherit;
  background: rgb(49 94 89 / 9%);
  font: 700 10px/1.2 "IBM Plex Mono", "Cascadia Mono", monospace;
  text-align: center;
}

.department-org-tree {
  max-height: 430px;
  padding: 3px 8px 10px;
  overflow: auto;
  --el-tree-node-hover-bg-color: #f3f8f5;
}

.department-org-tree :deep(.el-tree-node__content) {
  min-height: 36px;
  border-radius: 5px;
}

.department-org-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  color: var(--blue);
  background: #eaf3ef;
}

.department-org-node {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 7px;
  padding-right: 8px;
}

.department-org-node > span {
  overflow: hidden;
  flex: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.department-org-node small {
  color: var(--muted);
  font: 700 10px/1 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.department-members {
  min-width: 0;
}

.department-members__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  min-height: 74px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.department-members__head > div:first-child {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.department-members__head > div:first-child > span {
  color: var(--blue);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .08em;
}

.department-members__head h2 {
  margin: 0;
  overflow: hidden;
  color: var(--ink);
  font-size: 18px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.department-members__head p {
  max-width: 660px;
  margin: 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}

.department-members__metrics {
  display: flex;
  flex: 0 0 auto;
  margin: 0;
}

.department-members__metrics > div {
  display: grid;
  gap: 3px;
  min-width: 72px;
  padding: 0 14px;
  border-left: 1px solid var(--line);
}

.department-members__metrics dt {
  color: var(--muted);
  font-size: 10px;
}

.department-members__metrics dd {
  margin: 0;
  color: var(--ink);
  font: 700 19px/1 "IBM Plex Mono", "Cascadia Mono", monospace;
}

.department-people-filter {
  align-items: center;
  gap: 8px;
  margin: 16px 0 11px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f5f8f5;
}

.department-people-filter::before {
  display: none;
}

.department-people-filter .el-input {
  width: min(320px, 100%);
}

.department-people-filter .el-button { min-width: 72px; }

.department-list-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  min-height: 27px;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 11px;
}

.department-list-meta span + span {
  padding-left: 12px;
  border-left: 1px solid var(--line);
}

.department-list-meta strong {
  color: var(--ink);
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace;
}

.department-people-table-wrap {
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 7px;
}

.department-people-table-wrap :deep(.el-table) {
  min-width: 900px;
}

.department-people-table-wrap :deep(.el-table__header th) {
  color: #596b65;
  background: #f6f8f4;
  font-size: 11px;
  font-weight: 750;
}

.department-people-table-wrap :deep(.el-table__cell) {
  padding: 12px 0;
}

.employee-row-index {
  color: #80908a;
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.department-employee-pagination {
  justify-content: flex-end;
  margin: 0;
  padding: 12px 14px;
  border-top: 1px solid var(--line);
  background: #fbfcfa;
}

.department-employee-pagination :deep(.el-pagination) {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 4px;
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
  .project-plan-package__head {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .package-month-filter {
    width: 100%;
    min-width: 0;
  }

  .department-workbench {
    grid-template-columns: 1fr;
    gap: 16px;
  }

  .department-org-sidebar {
    position: static;
  }

  .department-workbench--single-page .department-org-sidebar {
    position: static;
    align-self: start;
  }

  .department-org-tree {
    max-height: 250px;
  }

  .department-members__head {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }

  .department-members__metrics {
    width: 100%;
  }

  .department-members__metrics > div {
    flex: 1;
    padding: 0 12px;
  }

  .department-members__metrics > div:first-child {
    padding-left: 0;
    border-left: 0;
  }

  .package-plan-meta {
    grid-template-columns: 1fr;
  }

  .package-plan-meta > div + div {
    border-top: 1px solid var(--line);
    border-left: 0;
  }

  .package-plan-title time {
    display: none;
  }
}

@media (max-width: 600px) {
  .package-month-filter {
    display: grid;
    grid-template-columns: 34px minmax(0, 1fr);
  }

  .package-month-filter .el-date-editor {
    grid-column: 1 / -1;
    width: 100%;
    margin-left: 0;
  }

  .department-people-filter {
    align-items: stretch;
  }

  .department-people-filter .el-input {
    width: 100%;
  }

  .department-people-filter .el-button {
    flex: 1 1 0;
  }

  .department-employee-pagination {
    justify-content: center;
  }

  .department-employee-pagination :deep(.el-pagination) {
    justify-content: center;
  }

  .department-employee-pagination :deep(.el-pagination__sizes),
  .department-employee-pagination :deep(.el-pagination__jump) {
    display: none;
  }
}
</style>
