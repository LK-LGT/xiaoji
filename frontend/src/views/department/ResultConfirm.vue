<template>
  <section class="page-panel">
    <div class="page-header">
      <div>
        <h1 class="page-title">成果审批与查看</h1>
        <p class="page-subtitle">审批组内领导成果；项目组员工和领导的成果全部通过后，再查看整组完整成果。</p>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :disabled="actionBusy" @click="refresh()">刷新</el-button>
        <el-button
          v-if="activeTab === 'approval'"
          type="primary"
          :icon="Download"
          :loading="exporting"
          :disabled="loading || actionBusy"
          @click="exportList"
        >导出审批清单</el-button>
      </div>
    </div>

    <div class="filter-bar mt16">
      <el-date-picker v-model="query.month" type="month" value-format="YYYY-MM" placeholder="全部月份" />
      <el-select
        v-model="selectedOrgId"
        clearable
        :loading="orgLoading"
        :disabled="!orgOptions.length"
        placeholder="全部授权组织"
      >
        <el-option v-for="item in orgOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <template v-if="activeTab === 'approval'">
        <el-select v-model="query.status" clearable placeholder="审批状态">
          <el-option label="待审批" value="待确认" />
          <el-option label="暂不可审批" value="不可确认" />
          <el-option label="已通过" value="已确认" />
          <el-option label="已驳回" value="已驳回" />
        </el-select>
        <el-input v-model="query.keyword" clearable placeholder="领导/成果" />
      </template>
      <el-button :icon="Search" @click="refresh()">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <el-tabs v-model="activeTab" class="result-tabs">
      <el-tab-pane label="领导成果审批" name="approval">
        <AiReviewPanel
          class="mt16"
          :review="null"
          :display-report="false"
          title="AI成果核验辅助"
          empty-text="成果提交时会自动检查证据完整性与验收项覆盖情况；打开详情后可结合AI报告完成审批。"
        />

        <div class="status-strip mt16">
          <span class="status-pill"><strong>{{ total }}</strong> 条领导成果</span>
          <span class="status-pill"><strong>{{ selectedRows.length }}</strong> 条已选</span>
          <span class="status-pill"><strong>{{ confirmableCount }}</strong> 条待审批</span>
          <span class="status-pill"><strong>{{ blockedCount }}</strong> 条暂不可审批</span>
        </div>

        <div v-if="selectedRows.length" class="batch-bar">
          <div>已选择 <strong>{{ selectedRows.length }}</strong> 条领导成果。</div>
          <div class="batch-actions">
            <el-button size="small" :loading="exporting" :disabled="actionBusy" @click="batchExport">导出证据清单</el-button>
            <el-button size="small" :disabled="actionBusy" @click="clearSelection">清空选择</el-button>
          </div>
        </div>

        <div class="month-group-toolbar">
          <div><strong>按成果月份归档</strong><span>{{ monthGroups.length }} 个月份分组</span></div>
          <div>
            <el-button link :disabled="allMonthsExpanded" @click="expandAllMonths">全部展开</el-button>
            <el-button link :disabled="allMonthsCollapsed" @click="collapseAllMonths">全部收起</el-button>
          </div>
        </div>

        <el-empty v-if="!loading && !monthGroups.length" description="没有待部门审批的领导成果" />
        <article v-for="group in monthGroups" :key="group.key" class="result-month-group">
          <button
            type="button"
            class="result-month-toggle"
            :aria-expanded="expandedMonthKeys.has(group.key)"
            :aria-controls="`department-result-month-${group.key}`"
            @click="toggleMonth(group.key)"
          >
            <el-icon><ArrowDown v-if="expandedMonthKeys.has(group.key)" /><ArrowRight v-else /></el-icon>
            <strong>{{ group.label }}</strong>
            <span>{{ group.rows.length }} 条领导成果</span>
          </button>
          <div :id="`department-result-month-${group.key}`" v-show="expandedMonthKeys.has(group.key)">
            <el-table
              ref="monthTables"
              v-loading="loading"
              :data="group.rows"
              border
              highlight-current-row
              :row-class-name="rowClassName"
              @selection-change="onMonthSelectionChange(group.key, $event)"
              @row-dblclick="openDetail"
            >
              <el-table-column type="selection" width="48" />
              <el-table-column prop="employee" label="领导" width="120" />
              <el-table-column prop="plan" label="计划/成果" min-width="260" show-overflow-tooltip />
              <el-table-column prop="evidence" label="成果证据" min-width="190" show-overflow-tooltip />
              <el-table-column label="状态" width="115">
                <template #default="{ row }">
                  <el-tag :type="statusTag(row.status)">{{ approvalStatusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="270" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" :icon="View" @click="openDetail(row)">详情与AI</el-button>
                  <el-tooltip :disabled="row.status === '待确认'" content="成果暂未满足审批条件" placement="top">
                    <span>
                      <el-button
                        link
                        type="success"
                        :icon="Lock"
                        :disabled="row.status !== '待确认' || actionBusy"
                        @click="openVerify(row)"
                      >审批通过</el-button>
                    </span>
                  </el-tooltip>
                  <el-button
                    link
                    type="danger"
                    :icon="Close"
                    :loading="processingId === row.id"
                    :disabled="row.status === '已确认' || row.status === '已驳回' || actionBusy"
                    @click="reject(row)"
                  >驳回</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </article>

        <div v-if="total" class="month-pagination">
          <el-pagination
            v-model:current-page="pageNo"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50, 100]"
            layout="total, sizes, prev, pager, next, jumper"
            :total="total"
            @current-change="changePage"
            @size-change="changePageSize"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane label="项目组完整成果" name="packages">
        <div class="package-rule">
          <strong>开放条件</strong>
          <span>普通员工成果由直属领导审批通过，组内领导成果由部门负责人审批通过；项目组全员全部完成后开放只读查看。</span>
        </div>
        <ProjectResultPackageTable
          :packages="resultPackages"
          :loading="packageLoading"
          empty-text="当前范围暂无项目组成果"
        />
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="detailVisible" title="领导成果审批详情" size="680px" @closed="closeDetail">
      <div class="drawer-stack">
        <el-skeleton v-if="detailLoading" :rows="7" animated />
        <template v-else-if="activeRow">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="领导">{{ activeRow.employee }}</el-descriptions-item>
            <el-descriptions-item label="计划/成果">{{ activeRow.plan }}</el-descriptions-item>
            <el-descriptions-item label="成果证据">{{ activeRow.evidence }}</el-descriptions-item>
            <el-descriptions-item label="问题">{{ activeRow.issue }}</el-descriptions-item>
          </el-descriptions>
          <AiReviewPanel :review="aiReview" compact empty-text="该成果尚未生成AI检查记录。" />
          <div class="section-header mt16">
            <div>
              <h2>成果证据</h2>
              <p>审批前可逐个下载并核对证据文件。</p>
            </div>
          </div>
          <el-table :data="activeRow.evidences" border empty-text="暂无成果证据">
            <el-table-column prop="name" label="文件" min-width="180" show-overflow-tooltip />
            <el-table-column prop="type" label="类型" width="100" />
            <el-table-column prop="status" label="状态" width="100" />
            <el-table-column label="评审" width="90">
              <template #default="{ row }">
                <el-tag :type="row.reviewPassed ? 'success' : 'info'">{{ row.reviewPassed ? '通过' : '待确认' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  :icon="Download"
                  :loading="downloadingEvidenceId === row.id"
                  :disabled="downloadingEvidenceId !== null"
                  @click="downloadEvidence(row)"
                >下载</el-button>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="verifyVisible" title="审批通过确认" width="520px" @closed="authPassword = ''">
      <el-form label-position="top">
        <el-form-item label="审批成果">
          <el-input :model-value="activeRow ? `${activeRow.employee} / ${activeRow.plan}` : ''" disabled />
        </el-form-item>
        <el-form-item label="当前登录密码">
          <el-input
            v-model="authPassword"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="输入当前账号密码完成身份复核"
            :disabled="submitting"
            @keyup.enter="submitConfirm"
          />
        </el-form-item>
        <el-form-item label="审批意见">
          <el-input v-model="confirmComment" type="textarea" :rows="3" maxlength="500" show-word-limit :disabled="submitting" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="submitting" @click="verifyVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitConfirm">审批通过</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowRight, Close, Download, Lock, Refresh, Search, View } from '@element-plus/icons-vue'
import {
  confirmResultApi,
  createExportTaskApi,
  downloadDepartmentResultEvidenceApi,
  getResultConfirmApi,
  listProjectResultPackagesApi,
  listResultConfirmsApi,
  rejectResultApi,
  type ProjectResultPackage,
} from '@/api/department'
import { errorMessage, periodRange } from '@/api/performance'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { useDepartmentOrgScope } from '@/composables/useDepartmentOrgScope'
import { confirmStatusCodes, mapResultConfirm } from '@/views/performanceAdapters'
import { saveBlob } from '@/utils/download'
import { getLatestAiReviewApi, type AiReview } from '@/api/aiReview'
import AiReviewPanel from '@/components/AiReviewPanel.vue'
import ProjectResultPackageTable from '@/components/ProjectResultPackageTable.vue'
import { currentMonthKey, groupRowsByMonth } from '@/utils/monthGroups'

type FinalConfirmRow = ReturnType<typeof mapResultConfirm>
type ResultTab = 'approval' | 'packages'

const route = useRoute()
const { orgOptions, selectedOrgId, orgLoading, loadOrgScope, resetOrgScope } = useDepartmentOrgScope(false)
const activeTab = ref<ResultTab>('approval')
const query = reactive({ month: '', status: '', keyword: '' })
const tableRows = ref<FinalConfirmRow[]>([])
const resultPackages = ref<ProjectResultPackage[]>([])
const selectedRowsByMonth = reactive(new Map<string, FinalConfirmRow[]>())
const selectedRows = computed(() => [...selectedRowsByMonth.values()].flat())
const monthTables = ref<Array<{ clearSelection?: () => void }>>([])
const pageNo = ref(1)
const pageSize = ref(10)
const total = ref(0)
const expandedMonthKeys = reactive(new Set<string>())
const activeRow = ref<FinalConfirmRow | null>(null)
const aiReview = ref<AiReview | null>(null)
const detailVisible = ref(false)
const verifyVisible = ref(false)
const authPassword = ref('')
const confirmComment = ref('确认领导成果通过。')
const loading = ref(false)
const packageLoading = ref(false)
const detailLoading = ref(false)
const submitting = ref(false)
const exporting = ref(false)
const downloadingEvidenceId = ref<number | null>(null)
const processingId = ref('')
let refreshRequestId = 0
let detailRequestId = 0

const confirmableCount = computed(() => tableRows.value.filter((row) => row.status === '待确认').length)
const blockedCount = computed(() => tableRows.value.filter((row) => row.status === '不可确认').length)
const actionBusy = computed(() => submitting.value || Boolean(processingId.value))
const monthGroups = computed(() => groupRowsByMonth(tableRows.value, (row) => row.resultDate))
const allMonthsExpanded = computed(() => monthGroups.value.length > 0 && monthGroups.value.every((group) => expandedMonthKeys.has(group.key)))
const allMonthsCollapsed = computed(() => monthGroups.value.every((group) => !expandedMonthKeys.has(group.key)))
const autoQuery = useAutoQuery(
  () => [selectedOrgId.value, query.month, query.status, query.keyword],
  () => {
    pageNo.value = 1
    return refresh(false)
  },
)

function resetMonthView() {
  expandedMonthKeys.clear()
  const currentKey = currentMonthKey()
  if (monthGroups.value.some((group) => group.key === currentKey)) expandedMonthKeys.add(currentKey)
}

function toggleMonth(key: string) {
  expandedMonthKeys.has(key) ? expandedMonthKeys.delete(key) : expandedMonthKeys.add(key)
}

function expandAllMonths() {
  monthGroups.value.forEach((group) => expandedMonthKeys.add(group.key))
}

function collapseAllMonths() {
  expandedMonthKeys.clear()
}

async function refresh(showMessage = true) {
  const requestId = ++refreshRequestId
  loading.value = true
  packageLoading.value = true
  try {
    const [data, packages] = await Promise.all([
      listResultConfirmsApi({
        orgId: selectedOrgId.value,
        periodMonth: query.month || undefined,
        confirmStatus: confirmStatusCodes[query.status] || undefined,
        keyword: query.keyword || undefined,
        pageNo: pageNo.value,
        pageSize: pageSize.value,
      }),
      listProjectResultPackagesApi({
        orgId: selectedOrgId.value,
        planMonth: query.month || undefined,
      }),
    ])
    if (requestId !== refreshRequestId) return
    if (!data.items.length && data.total > 0 && pageNo.value > 1) {
      pageNo.value = Math.ceil(data.total / pageSize.value)
      await refresh(showMessage)
      return
    }
    tableRows.value = data.items.map(mapResultConfirm)
    resultPackages.value = packages
    total.value = data.total
    clearSelection()
    resetMonthView()
    if (showMessage) ElMessage.success('成果数据已刷新')
  } catch (error) {
    if (requestId !== refreshRequestId) return
    tableRows.value = []
    resultPackages.value = []
    total.value = 0
    clearSelection()
    resetMonthView()
    ElMessage.error(errorMessage(error))
  } finally {
    if (requestId === refreshRequestId) {
      loading.value = false
      packageLoading.value = false
    }
  }
}

async function reset() {
  autoQuery.pause()
  Object.assign(query, { month: '', status: '', keyword: '' })
  pageNo.value = 1
  resetOrgScope()
  await refresh(false)
  autoQuery.resume()
  ElMessage.success('筛选条件已重置')
}

async function changePage(page: number) {
  pageNo.value = page
  clearSelection()
  await refresh(false)
}

async function changePageSize(size: number) {
  pageSize.value = size
  pageNo.value = 1
  clearSelection()
  await refresh(false)
}

async function exportList() {
  await createEvidenceExport(false)
}

async function openDetail(row: FinalConfirmRow) {
  await openDetailById(row.id)
}

async function openDetailById(id: string) {
  const requestId = ++detailRequestId
  activeTab.value = 'approval'
  detailVisible.value = true
  detailLoading.value = true
  activeRow.value = null
  aiReview.value = null
  try {
    const detail = mapResultConfirm(await getResultConfirmApi(id))
    if (requestId !== detailRequestId) return
    activeRow.value = detail
    aiReview.value = await getLatestAiReviewApi('RESULT', String(id))
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
  detailLoading.value = false
  activeRow.value = null
  aiReview.value = null
}

async function downloadEvidence(evidence: { id: number; name: string }) {
  if (!activeRow.value || downloadingEvidenceId.value !== null) return
  downloadingEvidenceId.value = evidence.id
  try {
    const blob = await downloadDepartmentResultEvidenceApi(activeRow.value.id, evidence.id)
    saveBlob(blob, evidence.name)
    ElMessage.success('成果证据已下载')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    downloadingEvidenceId.value = null
  }
}

function openVerify(row: FinalConfirmRow) {
  if (row.status !== '待确认' || actionBusy.value) return
  activeRow.value = row
  authPassword.value = ''
  verifyVisible.value = true
}

function rowClassName({ row }: { row: FinalConfirmRow }) {
  if (row.status === '不可确认') return 'danger-row clickable-row'
  if (row.issue !== '无') return 'warning-row clickable-row'
  return 'clickable-row'
}

function approvalStatusLabel(status: string) {
  if (status === '待确认') return '待审批'
  if (status === '不可确认') return '暂不可审批'
  if (status === '已确认') return '已通过'
  return status
}

function statusTag(status: string) {
  if (status === '已确认') return 'success'
  if (status === '已驳回') return 'info'
  if (status === '不可确认') return 'danger'
  return 'warning'
}

function onMonthSelectionChange(monthKey: string, rows: FinalConfirmRow[]) {
  if (rows.length) selectedRowsByMonth.set(monthKey, rows)
  else selectedRowsByMonth.delete(monthKey)
}

function clearSelection() {
  monthTables.value.forEach((table) => table.clearSelection?.())
  selectedRowsByMonth.clear()
}

async function batchExport() {
  await createEvidenceExport(true)
}

async function reject(row: FinalConfirmRow) {
  if (['已确认', '已驳回'].includes(row.status) || actionBusy.value) return
  try {
    const { value } = await ElMessageBox.prompt(`请输入驳回 ${row.employee} 成果的原因`, '驳回领导成果', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValidator: (text) => {
        if (!text?.trim()) return '请输入驳回原因'
        return text.trim().length <= 500 || '驳回原因不能超过 500 个字符'
      },
    })
    processingId.value = row.id
    const result = await rejectResultApi(row.id, { decision: 'REJECTED', comment: value.trim(), notifyEmployee: true })
    row.status = '已驳回'
    await refresh(false)
    ElMessage.success(result.message)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    processingId.value = ''
  }
}

async function submitConfirm() {
  if (!activeRow.value || submitting.value) return
  if (!authPassword.value) {
    ElMessage.warning('请输入当前登录密码')
    return
  }
  const comment = confirmComment.value.trim()
  if (!comment) {
    ElMessage.warning('请填写审批意见')
    return
  }
  submitting.value = true
  try {
    const result = await confirmResultApi(activeRow.value.id, {
      decision: 'CONFIRMED',
      comment,
      authPassword: authPassword.value,
      notifyEmployee: true,
    })
    verifyVisible.value = false
    authPassword.value = ''
    await refresh(false)
    ElMessage.success(result.message)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    submitting.value = false
  }
}

async function createEvidenceExport(selectedOnly: boolean) {
  if (exporting.value) return
  if (selectedOnly && !selectedRows.value.length) {
    ElMessage.warning('请选择需要导出的领导成果')
    return
  }
  exporting.value = true
  try {
    const task = await createExportTaskApi({
      dimensionType: 'RESULT_CONFIRM_LIST',
      dimensionId: selectedOnly
        ? `RESULTS:${selectedRows.value.map((row) => row.id).join(',')}`
        : selectedOrgId.value == null ? undefined : String(selectedOrgId.value),
      periodType: 'MONTH',
      ...(query.month ? periodRange(query.month) : {}),
      formats: ['PDF'],
      includeEvidence: true,
    })
    ElMessage.success(`领导成果清单导出任务已创建：${task.id}`)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  try {
    await loadOrgScope()
    await refresh(false)
    autoQuery.resume()
    if (typeof route.query.resultId === 'string') {
      await openDetailById(route.query.resultId)
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
})
</script>

<style scoped>
.result-tabs { margin-top: 12px; }
.package-rule { display: flex; align-items: flex-start; gap: 12px; margin: 12px 0 14px; padding: 12px 14px; border-left: 3px solid var(--primary); background: #f7f8f4; }
.package-rule strong { flex: 0 0 auto; color: var(--ink); font-size: 13px; }
.package-rule span { color: var(--muted); font-size: 13px; line-height: 1.6; }
.month-group-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 16px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px 8px 0 0; background: #f7f8f4; }
.month-group-toolbar > div { display: flex; align-items: center; gap: 10px; }
.month-group-toolbar span { color: var(--muted); font-size: 13px; }
.month-group-toolbar .el-button { margin: 0; }
.result-month-group { border: 1px solid var(--line); border-top: 0; background: var(--surface); }
.result-month-toggle { display: grid; grid-template-columns: 20px auto 1fr; align-items: center; gap: 8px; width: 100%; min-height: 52px; padding: 10px 14px; border: 0; color: var(--ink); background: #f7f8f4; text-align: left; cursor: pointer; }
.result-month-toggle:hover { background: #eef5f2; }
.result-month-toggle .el-icon { color: var(--primary); }
.result-month-toggle strong { font-size: 15px; }
.result-month-toggle span { color: var(--muted); font-size: 13px; }
.month-pagination { display: flex; justify-content: flex-end; padding: 14px 0 0; }

@media (max-width: 760px) {
  .package-rule { flex-direction: column; gap: 4px; }
}
</style>
