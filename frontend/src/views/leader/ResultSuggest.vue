<template>
  <section v-loading="approvalType === 'month' && loading" class="page-panel">
    <div class="page-header">
      <div>
        <span class="result-page-kicker">成果管理</span>
        <span class="result-page-hint">员工成果审批与团队档案</span>
      </div>
      <div v-if="approvalType === 'month'" class="toolbar">
        <el-button :icon="Refresh" :disabled="actionBusy" @click="refresh()">刷新</el-button>
        <el-button type="primary" :icon="Download" :loading="exporting" :disabled="loading || actionBusy" @click="exportList">导出审批清单</el-button>
      </div>
    </div>

    <div class="result-approval-type-switch">
      <el-tabs
        :model-value="activeView"
        class="result-center-view-tabs"
        aria-label="员工成果管理视图"
        @tab-change="changeView"
      >
        <el-tab-pane label="待我处理" name="approval" />
        <el-tab-pane label="团队进度" name="progress" />
        <el-tab-pane label="成果档案" name="archive" />
      </el-tabs>
    </div>

    <section v-if="activeView === 'progress'" class="leader-result-progress" aria-label="团队成果进度">
      <div class="status-strip mt16">
        <span class="status-pill"><strong>{{ tableRows.length }}</strong> 条成果</span>
        <span class="status-pill"><strong>{{ pendingCount }}</strong> 条待处理</span>
        <span class="status-pill"><strong>{{ completedCount }}</strong> 条已完成</span>
        <span class="status-pill"><strong>{{ issueCount }}</strong> 条证据问题</span>
      </div>
      <div v-if="employeeProgress.length" class="leader-progress-list">
        <article v-for="item in employeeProgress" :key="item.key" class="leader-progress-row">
          <div><strong>{{ item.name }}</strong><span>{{ item.total }} 条成果</span></div>
          <div class="leader-progress-bar"><span :style="{ width: `${item.percent}%` }"></span></div>
          <div><span>待处理 {{ item.pending }}</span><strong>{{ item.percent }}%</strong></div>
        </article>
      </div>
      <div v-else class="leader-result-empty">暂无团队成果进度数据</div>
    </section>

    <section v-else-if="activeView === 'archive'" class="leader-result-archive" aria-label="员工成果档案">
      <div v-if="archiveRows.length" class="leader-archive-list">
        <article v-for="row in archiveRows" :key="row.id" class="leader-archive-row">
          <div><strong>{{ row.employee }}</strong><span>{{ row.resultDate || '周期未填写' }}</span></div>
          <div><strong>{{ row.result }}</strong><span>{{ row.plan }}</span></div>
          <el-tag size="small" type="success" effect="plain">已处理</el-tag>
          <el-button link type="primary" :icon="View" @click="openEvidence(row)">查看详情</el-button>
        </article>
      </div>
      <div v-else class="leader-result-empty">暂无已处理成果档案</div>
    </section>

    <template v-else>
      <el-tabs
        v-model="approvalType"
        class="result-approval-tabs"
        aria-label="选择成果审批类型"
        @tab-change="changeApprovalType"
      >
        <el-tab-pane label="月计划成果" name="month" />
        <el-tab-pane label="周计划成果" name="week" />
      </el-tabs>

    <template v-if="approvalType === 'month'">
    <div class="status-strip mt16">
      <span class="status-pill"><strong>{{ tableRows.length }}</strong> 条成果</span>
      <span class="status-pill"><strong>{{ selectedRows.length }}</strong> 条已选</span>
      <span class="status-pill"><strong>{{ pendingCount }}</strong> 条待审批</span>
      <span class="status-pill"><strong>{{ issueCount }}</strong> 条证据问题</span>
    </div>

    <div class="filter-bar">
      <el-select
        v-model="scopeOrgId"
        clearable
        :loading="orgLoading"
        :disabled="!orgOptions.length"
        placeholder="全部直属范围"
      >
        <el-option
          v-for="item in orgOptions"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
      <el-date-picker v-model="query.dateRange" type="daterange" start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD" />
      <el-select v-model="query.status" clearable placeholder="成果状态">
        <el-option label="待审批" value="待审批" />
        <el-option label="审批通过" value="审批通过" />
        <el-option label="审批驳回" value="审批驳回" />
      </el-select>
      <el-select v-model="query.evidence" clearable placeholder="证据状态">
        <el-option label="完整" value="完整" />
        <el-option label="缺失" value="缺失" />
      </el-select>
      <el-button :icon="Search" @click="refresh()">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </div>

    <div v-if="selectedRows.length" class="batch-bar">
      <div>已选择 <strong>{{ selectedRows.length }}</strong> 条成果，可批量审批。</div>
      <div class="batch-actions">
        <el-button size="small" type="success" :loading="batchAction === '审批通过'" :disabled="actionBusy && batchAction !== '审批通过'" @click="batchSuggest('审批通过')">通过</el-button>
        <el-button size="small" type="danger" :loading="batchAction === '审批驳回'" :disabled="actionBusy && batchAction !== '审批驳回'" @click="batchSuggest('审批驳回')">驳回</el-button>
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

    <el-empty v-if="!loading && !monthGroups.length" description="没有符合条件的成果记录" />
    <article
      v-for="group in monthGroups"
      v-show="pagedMonthKeys.has(group.key)"
      :key="group.key"
      class="result-month-group"
    >
      <button
        type="button"
        class="result-month-toggle"
        :aria-expanded="expandedMonthKeys.has(group.key)"
        :aria-controls="`leader-result-month-${group.key}`"
        @click="toggleMonth(group.key)"
      >
        <el-icon><ArrowDown v-if="expandedMonthKeys.has(group.key)" /><ArrowRight v-else /></el-icon>
        <strong>{{ group.label }}</strong>
        <span>{{ group.employees.length }} 名员工 · {{ group.rows.length }} 条成果</span>
      </button>
      <div :id="`leader-result-month-${group.key}`" v-show="expandedMonthKeys.has(group.key)" class="result-month-content">
        <section class="employee-filter-panel" :aria-label="`${group.label}员工筛选`">
          <div class="employee-filter-heading">
            <div>
              <strong>选择员工</strong>
              <span>点击姓名查看该员工当月成果</span>
            </div>
            <span>{{ group.employees.length }} 人</span>
          </div>
          <div class="employee-filter-list" role="list">
            <button
              v-for="employeeGroup in group.employees"
              :key="employeeGroup.key"
              type="button"
              class="employee-filter-button"
              :class="{ 'is-active': activeEmployeeKeys.get(group.key) === employeeGroup.key }"
              :aria-pressed="activeEmployeeKeys.get(group.key) === employeeGroup.key"
              @click="selectEmployee(group.key, employeeGroup.key)"
            >
              <el-icon><User /></el-icon>
              <strong>{{ employeeGroup.name }}</strong>
              <span>{{ employeeGroup.rows.length }} 条</span>
              <em v-if="employeeGroup.pendingCount">{{ employeeGroup.pendingCount }} 待审</em>
              <em v-if="selectedCount(group.key, employeeGroup.key)" class="is-selected">
                已选 {{ selectedCount(group.key, employeeGroup.key) }}
              </em>
            </button>
          </div>
        </section>

        <div v-if="!activeEmployeeKeys.get(group.key)" class="employee-result-prompt">
          <el-icon><User /></el-icon>
          <div><strong>请选择一名员工</strong><span>选择后显示该员工在 {{ group.label }} 的全部成果。</span></div>
        </div>

        <template v-for="employeeGroup in group.employees" :key="employeeGroup.key">
          <section v-if="activeEmployeeKeys.get(group.key) === employeeGroup.key" class="employee-result-view">
            <div class="employee-result-context">
              <div>
                <strong>{{ employeeGroup.name }}</strong>
                <span>{{ group.label }} · {{ employeeGroup.rows.length }} 条成果</span>
              </div>
              <span v-if="employeeGroup.pendingCount">{{ employeeGroup.pendingCount }} 条待审批</span>
              <span v-else class="is-complete">本月成果已处理</span>
            </div>
            <div class="result-approval-list" role="list">
              <article
                v-for="row in employeeGroup.rows"
                :key="row.id"
                class="result-approval-item"
                :class="{ 'is-terminal': row.terminal }"
                role="listitem"
              >
                <el-checkbox
                  class="result-select"
                  :model-value="isRowSelected(group.key, employeeGroup.key, row)"
                  :disabled="!canSelect(row)"
                  :aria-label="`选择成果：${row.result}`"
                  @change="toggleRowSelection(group.key, employeeGroup.key, row, Boolean($event))"
                />
                <div class="result-approval-main">
                  <div class="result-approval-title">
                    <strong>{{ row.result }}</strong>
                    <div>
                      <ResultStatusTag :status="suggestionDisplayStatus(row.suggestion)" />
                    </div>
                  </div>
                  <div class="result-approval-meta">
                    <span><small>关联计划</small><strong>{{ row.plan }}</strong></span>
                    <span><small>成果证据</small><button type="button" class="evidence-name-button" @click="openEvidence(row)">{{ row.evidence }}</button></span>
                  </div>
                  <div class="result-check-summary" :title="row.issue">
                    <el-tag size="small" :type="row.issue === '无' ? 'success' : 'warning'">{{ row.issue === '无' ? '检查通过' : '需要核对' }}</el-tag>
                    <span>{{ compactIssueText(row.issue) }}</span>
                    <el-button link type="primary" @click="openEvidence(row)">查看检查详情</el-button>
                  </div>
                </div>
                <div class="result-approval-actions">
                  <el-button type="primary" plain :icon="View" @click="openEvidence(row)">查看证据</el-button>
                  <template v-if="!row.terminal">
                    <el-button type="success" plain :icon="Check" :disabled="actionBusy" @click="openSuggest(row, '审批通过')">通过</el-button>
                    <el-button type="danger" plain :icon="Close" :disabled="actionBusy" @click="openSuggest(row, '审批驳回')">驳回</el-button>
                  </template>
                </div>
              </article>
            </div>
          </section>
        </template>
      </div>
    </article>

    <div v-if="monthGroups.length > monthPageSize" class="month-pagination">
      <el-pagination v-model:current-page="monthPage" :page-size="monthPageSize" layout="prev, pager, next, total" :total="monthGroups.length" />
    </div>

    <el-drawer
      v-model="evidenceVisible"
      title="成果证据"
      class="evidence-drawer"
      :size="`${evidenceDrawerWidth}px`"
      @closed="closeEvidence"
    >
      <button
        type="button"
        class="drawer-resize-handle"
        aria-label="拖动调整成果证据面板宽度"
        title="左右拖动调整宽度"
        @pointerdown="beginEvidenceDrawerResize"
      ><span /></button>
      <div class="drawer-stack">
        <el-skeleton v-if="evidenceLoading" :rows="7" animated />
        <template v-else-if="activeRow">
        <el-descriptions :column="evidenceDrawerWide ? 2 : 1" border>
          <el-descriptions-item label="员工">{{ activeRow.employee }}</el-descriptions-item>
          <el-descriptions-item label="关联计划">{{ activeRow.plan }}</el-descriptions-item>
          <el-descriptions-item label="成果" :span="evidenceDrawerWide ? 2 : 1">{{ activeRow.result }}</el-descriptions-item>
          <el-descriptions-item label="证据" :span="evidenceDrawerWide ? 2 : 1">{{ activeRow.evidence }}</el-descriptions-item>
          <el-descriptions-item label="检查结果" :span="evidenceDrawerWide ? 2 : 1">{{ activeRow.issue }}</el-descriptions-item>
        </el-descriptions>
        <AiReviewPanel :review="aiReview" compact empty-text="该成果尚未生成AI检查记录。" />
        <div>
          <div class="section-header">
            <div>
              <h2>证据文件</h2>
              <p>审批前可直接查看图片、PDF、Word内容或压缩包目录，也可以下载原文件。</p>
            </div>
          </div>
          <el-empty v-if="!evidenceFiles.length" description="该成果没有可查看的证据文件" />
          <div v-else class="evidence-file-list">
            <article v-for="evidence in evidenceFiles" :key="evidence.id" class="evidence-file-item">
              <span class="evidence-file-type">{{ fileExtension(evidence.name) || '文件' }}</span>
              <div>
                <strong>{{ evidence.name }}</strong>
                <span>{{ evidence.type }} · {{ evidence.status }}</span>
              </div>
              <div class="evidence-file-actions">
                <el-button
                  type="primary"
                  plain
                  :icon="View"
                  :loading="previewingEvidenceId === evidence.id"
                  :disabled="previewingEvidenceId !== null || downloadingEvidenceId !== null"
                  @click="previewEvidence(evidence)"
                >打开查看</el-button>
                <el-button
                  plain
                  :icon="Download"
                  :loading="downloadingEvidenceId === evidence.id"
                  :disabled="downloadingEvidenceId !== null"
                  @click="downloadEvidence(evidence)"
                >下载</el-button>
              </div>
            </article>
          </div>
        </div>
        </template>
      </div>
    </el-drawer>

    <el-dialog
      v-model="previewVisible"
      class="evidence-preview-dialog"
      :title="previewData?.fileName || activePreviewEvidence?.name || '证据预览'"
      width="min(1100px, 92vw)"
      append-to-body
      destroy-on-close
      @closed="closeEvidencePreview"
    >
      <div v-loading="previewLoading" class="evidence-preview-body">
        <template v-if="previewData">
          <img v-if="previewData.previewType === 'IMAGE' && previewObjectUrl" class="evidence-image-preview" :src="previewObjectUrl" :alt="previewData.fileName" />
          <iframe v-else-if="['PDF', 'TEXT'].includes(previewData.previewType) && previewObjectUrl" class="evidence-document-frame" :src="previewObjectUrl" :title="previewData.fileName" />
          <pre v-else-if="previewData.previewType === 'DOCUMENT'" class="evidence-text-preview">{{ previewData.textContent || '文档没有可提取的文字，请下载后查看完整内容。' }}</pre>
          <section v-else-if="previewData.previewType === 'ARCHIVE'" class="archive-preview">
            <div class="archive-preview-heading">
              <div><strong>文件夹内容</strong><span>{{ archiveFileCount }} 个文件</span></div>
              <span>点击文件即可打开，目录结构保持原样</span>
            </div>
            <div v-if="activeArchiveEntry" class="archive-entry-preview">
              <div class="archive-entry-preview-head">
                <strong>{{ activeArchiveEntry.path }}</strong>
                <el-button link @click="closeArchiveEntryPreview">关闭预览</el-button>
              </div>
              <img v-if="activeArchiveEntry.previewType === 'IMAGE' && archiveEntryObjectUrl" :src="archiveEntryObjectUrl" :alt="activeArchiveEntry.name" />
              <iframe v-else-if="archiveEntryObjectUrl" :src="archiveEntryObjectUrl" :title="activeArchiveEntry.name" />
            </div>
            <div class="archive-entry-list" role="list">
              <button
                v-for="entry in previewData.entries"
                :key="entry.path"
                type="button"
                class="archive-entry-item"
                :class="{ 'is-folder': entry.directory }"
                :disabled="entry.directory || archiveEntryLoadingPath === entry.path"
                @click="openArchiveEntry(entry)"
              >
                <span class="archive-entry-type">{{ entry.directory ? '目录' : fileExtension(entry.name) || '文件' }}</span>
                <span><strong>{{ entry.name }}</strong><small>{{ entry.path }}</small></span>
                <em>{{ entry.directory ? '' : fileSizeText(entry.size) }}</em>
                <b v-if="!entry.directory">{{ archiveEntryLoadingPath === entry.path ? '打开中' : '打开' }}</b>
              </button>
            </div>
          </section>
          <div v-else class="evidence-preview-fallback">
            <strong>该文件需要使用本地软件打开</strong>
            <span>下载原文件后即可查看完整内容。</span>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button v-if="activePreviewEvidence" type="primary" :icon="Download" @click="downloadEvidence(activePreviewEvidence)">下载原文件</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="suggestVisible" :title="suggestForm.type" width="560px">
      <el-form label-position="top">
        <el-form-item label="审批结论">
          <el-select v-model="suggestForm.type">
            <el-option label="审批通过" value="审批通过" />
            <el-option label="审批驳回" value="审批驳回" />
          </el-select>
        </el-form-item>
        <el-form-item label="审批意见">
          <el-input v-model="suggestForm.comment" type="textarea" :rows="4" maxlength="300" show-word-limit :disabled="submitting" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="submitting" @click="suggestVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitSuggest">确认审批</el-button>
      </template>
    </el-dialog>
    </template>

    <WeekResultApproval v-else embedded workspace="leader" />
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowRight, Check, Close, Download, Refresh, Search, User, View } from '@element-plus/icons-vue'
import {
  batchSubmitResultSuggestionsApi,
  createLeaderExportTaskApi,
  downloadLeaderExportTaskApi,
  downloadLeaderResultEvidenceApi,
  getLeaderExportDownloadInfoApi,
  getLeaderResultEvidencePreviewApi,
  getResultSuggestionApi,
  listResultSuggestionsApi,
  openLeaderResultEvidenceArchiveEntryApi,
  submitResultSuggestionApi,
  type ResultEvidenceArchiveEntry,
  type ResultEvidencePreview,
} from '@/api/leader'
import { errorMessage } from '@/api/performance'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { useLeaderOrgScope } from '@/composables/useLeaderOrgScope'
import { saveBlob } from '@/utils/download'
import { getLatestAiReviewApi, type AiReview } from '@/api/aiReview'
import AiReviewPanel from '@/components/AiReviewPanel.vue'
import WeekResultApproval from '@/views/leader/WeekResultApproval.vue'
import ResultStatusTag from '@/components/results/ResultStatusTag.vue'
import {
  mapResultSuggestion,
  suggestionStatusCodes,
} from '@/views/performanceAdapters'
import { currentMonthKey, groupRowsByMonth, paginateMonthGroups } from '@/utils/monthGroups'

type ResultSuggestionRow = ReturnType<typeof mapResultSuggestion>
type ApprovalType = 'month' | 'week'
type ResultCenterView = 'approval' | 'progress' | 'archive'
type EmployeeResultGroup = {
  key: string
  name: string
  rows: ResultSuggestionRow[]
  pendingCount: number
}

const route = useRoute()
const router = useRouter()
const approvalType = ref<ApprovalType>(route.query.type === 'week' ? 'week' : 'month')
const activeView = computed<ResultCenterView>(() => ['progress', 'archive'].includes(String(route.query.view)) ? String(route.query.view) as ResultCenterView : 'approval')
const { orgOptions, scopeOrgId, orgLoading, loadOrgScope, resetOrgScope } = useLeaderOrgScope()
const query = reactive({
  dateRange: [] as string[],
  status: '',
  evidence: '',
})
const tableRows = ref<ResultSuggestionRow[]>([])
const selectedRowsByMonth = reactive(new Map<string, ResultSuggestionRow[]>())
const selectedRows = computed(() => [...selectedRowsByMonth.values()].flat())
const monthPage = ref(1)
const monthPageSize = 10
const expandedMonthKeys = reactive(new Set<string>())
const activeEmployeeKeys = reactive(new Map<string, string>())
const activeRow = ref<ResultSuggestionRow | null>(null)
const evidenceVisible = ref(false)
const suggestVisible = ref(false)
const loading = ref(false)
const evidenceLoading = ref(false)
const submitting = ref(false)
const aiReview = ref<AiReview | null>(null)
const batchAction = ref('')
const exporting = ref(false)
let refreshRequestId = 0
let evidenceRequestId = 0
const suggestForm = reactive({
  type: '审批通过',
  comment: '',
})
const evidenceFiles = ref<{ id: number; name: string; type: string; status: string }[]>([])
const downloadingEvidenceId = ref<number | null>(null)
const evidenceDrawerWidth = ref(defaultEvidenceDrawerWidth())
const evidenceDrawerWide = computed(() => evidenceDrawerWidth.value >= 900)
const previewVisible = ref(false)
const previewLoading = ref(false)
const previewingEvidenceId = ref<number | null>(null)
const previewData = ref<ResultEvidencePreview | null>(null)
const activePreviewEvidence = ref<{ id: number; name: string; type: string; status: string } | null>(null)
const previewObjectUrl = ref('')
const activeArchiveEntry = ref<ResultEvidenceArchiveEntry | null>(null)
const archiveEntryObjectUrl = ref('')
const archiveEntryLoadingPath = ref('')
const archiveFileCount = computed(() => previewData.value?.entries.filter((entry) => !entry.directory).length || 0)
let stopEvidenceDrawerResize: (() => void) | null = null
const pendingCount = computed(() => tableRows.value.filter((row) => row.suggestion === '待审批').length)
const issueCount = computed(() => tableRows.value.filter((row) => row.issue !== '无').length)
const completedCount = computed(() => tableRows.value.filter((row) => row.terminal).length)
const employeeProgress = computed(() => {
  const groups = new Map<string, { key: string; name: string; total: number; pending: number }>()
  tableRows.value.forEach((row) => {
    const key = String(row.employeeId || row.employee)
    const item = groups.get(key) || { key, name: row.employee, total: 0, pending: 0 }
    item.total += 1
    if (!row.terminal) item.pending += 1
    groups.set(key, item)
  })
  return [...groups.values()].map((item) => ({ ...item, percent: item.total ? Math.round((item.total - item.pending) * 100 / item.total) : 0 }))
})
const archiveRows = computed(() => tableRows.value.filter((row) => row.terminal))
const actionBusy = computed(() => submitting.value || Boolean(batchAction.value))
const monthGroups = computed(() => groupRowsByMonth(tableRows.value, (row) => row.resultDate)
  .map((group) => ({ ...group, employees: groupRowsByEmployee(group.rows) })))
const pagedMonthGroups = computed(() => paginateMonthGroups(monthGroups.value, monthPage.value, monthPageSize))
const pagedMonthKeys = computed(() => new Set(pagedMonthGroups.value.map((group) => group.key)))
const allMonthsExpanded = computed(() => monthGroups.value.length > 0 && monthGroups.value.every((group) => expandedMonthKeys.has(group.key)))
const allMonthsCollapsed = computed(() => monthGroups.value.every((group) => !expandedMonthKeys.has(group.key)))
const autoQuery = useAutoQuery(
  () => [scopeOrgId.value, query.dateRange?.[0], query.dateRange?.[1], query.status, query.evidence],
  () => refresh(false),
)

function changeApprovalType(value: string | number | boolean | undefined) {
  const nextType: ApprovalType = value === 'week' ? 'week' : 'month'
  approvalType.value = nextType
  const nextQuery = { ...route.query }
  delete nextQuery.id
  if (nextType === 'week') nextQuery.type = 'week'
  else delete nextQuery.type
  void router.replace({ path: '/leader/result-suggest', query: nextQuery })
}

function changeView(value: string | number | boolean | undefined) {
  const view: ResultCenterView = value === 'progress' || value === 'archive' ? value : 'approval'
  const nextQuery = { ...route.query }
  if (view === 'approval') {
    delete (nextQuery as Record<string, unknown>).view
  } else {
    nextQuery.view = view
  }
  void router.replace({ path: '/leader/result-suggest', query: nextQuery })
}

function suggestionDisplayStatus(value: string) {
  if (value === '审批通过') return 'APPROVED'
  if (value === '审批驳回') return 'REJECTED'
  return 'SUBMITTED'
}

function resetMonthView() {
  monthPage.value = 1
  expandedMonthKeys.clear()
  activeEmployeeKeys.clear()
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

function groupRowsByEmployee(rows: ResultSuggestionRow[]): EmployeeResultGroup[] {
  const groups = new Map<string, EmployeeResultGroup>()
  rows.forEach((row) => {
    const key = String(row.employeeId || row.employee)
    const current = groups.get(key)
    if (current) {
      current.rows.push(row)
      if (!row.terminal) current.pendingCount += 1
      return
    }
    groups.set(key, {
      key,
      name: row.employee,
      rows: [row],
      pendingCount: row.terminal ? 0 : 1,
    })
  })
  return [...groups.values()].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN', { numeric: true }))
}

function selectionKey(monthKey: string, employeeKey: string) {
  return `${monthKey}::${employeeKey}`
}

function selectedCount(monthKey: string, employeeKey: string) {
  return selectedRowsByMonth.get(selectionKey(monthKey, employeeKey))?.length || 0
}

function selectEmployee(monthKey: string, employeeKey: string) {
  activeEmployeeKeys.set(monthKey, employeeKey)
}

async function refresh(showMessage = true) {
  const requestId = ++refreshRequestId
  loading.value = true
  try {
    const data = await listResultSuggestionsApi({
      scopeOrgId: scopeOrgId.value,
      startDate: query.dateRange?.[0],
      endDate: query.dateRange?.[1],
      suggestionStatus: suggestionStatusCodes[query.status] || undefined,
      evidenceStatus: query.evidence === '完整' ? 'COMPLETE' : query.evidence === '缺失' ? 'INCOMPLETE' : undefined,
    })
    if (requestId !== refreshRequestId) return
    tableRows.value = data.map(mapResultSuggestion)
    clearSelection()
    resetMonthView()
    if (showMessage) ElMessage.success('员工成果审批列表已刷新')
  } catch (error) {
    if (requestId !== refreshRequestId) return
    tableRows.value = []
    clearSelection()
    resetMonthView()
    ElMessage.error(errorMessage(error))
  } finally {
    if (requestId === refreshRequestId) loading.value = false
  }
}

async function exportList() {
  if (query.dateRange?.length !== 2) {
    ElMessage.warning('导出前请选择日期范围')
    return
  }
  exporting.value = true
  try {
    const task = await createLeaderExportTaskApi({
      dimensionType: 'RESULT_SUGGESTION_LIST',
      dimensionId: scopeOrgId.value == null ? undefined : String(scopeOrgId.value),
      periodType: 'DAY',
      periodStart: query.dateRange?.[0],
      periodEnd: query.dateRange?.[1],
      formats: ['PDF'],
      includeEvidence: true,
    })
    const info = await getLeaderExportDownloadInfoApi(task.id)
    saveBlob(await downloadLeaderExportTaskApi(task.id), info.fileName)
    ElMessage.success(`员工成果审批清单已导出，校验值 ${info.checksum || '无'}`)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    exporting.value = false
  }
}

async function reset() {
  autoQuery.pause()
  resetOrgScope()
  query.dateRange = []
  query.status = ''
  query.evidence = ''
  await refresh(false)
  autoQuery.resume()
  ElMessage.success('筛选条件已重置')
}

async function openEvidence(row: ResultSuggestionRow) {
  const requestId = ++evidenceRequestId
  evidenceDrawerWidth.value = defaultEvidenceDrawerWidth()
  evidenceVisible.value = true
  evidenceLoading.value = true
  activeRow.value = null
  aiReview.value = null
  evidenceFiles.value = []
  try {
    const detail = mapResultSuggestion(await getResultSuggestionApi(row.id))
    if (requestId !== evidenceRequestId) return
    activeRow.value = detail
    evidenceFiles.value = detail.evidences
    aiReview.value = await getLatestAiReviewApi('RESULT', Number(row.id))
  } catch (error) {
    if (requestId !== evidenceRequestId) return
    evidenceVisible.value = false
    ElMessage.error(errorMessage(error))
  } finally {
    if (requestId === evidenceRequestId) evidenceLoading.value = false
  }
}

function closeEvidence() {
  evidenceRequestId += 1
  evidenceLoading.value = false
  activeRow.value = null
  evidenceFiles.value = []
  previewVisible.value = false
  closeEvidencePreview()
}

function fileExtension(fileName: string) {
  const extension = fileName.split('.').pop()?.trim().toUpperCase()
  return extension && extension !== fileName.toUpperCase() ? extension : ''
}

function fileSizeText(size: number) {
  if (!Number.isFinite(size) || size <= 0) return '--'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function compactIssueText(issue: string) {
  if (!issue || issue === '无') return '未发现需要额外处理的证据问题。'
  const normalized = issue.replace(/\s+/g, ' ').trim()
  const riskSummary = normalized.match(/共发现[^。]+。?/)
  if (riskSummary) return riskSummary[0]
  const firstSentence = normalized.split('。')[0]
  return firstSentence.length > 72 ? `${firstSentence.slice(0, 72)}...` : `${firstSentence}。`
}

function defaultEvidenceDrawerWidth() {
  if (typeof window === 'undefined') return 960
  if (window.innerWidth <= 1000) return Math.max(320, window.innerWidth - 24)
  return Math.max(720, window.innerWidth - 360)
}

function revokePreviewUrl(target: typeof previewObjectUrl | typeof archiveEntryObjectUrl) {
  if (target.value) URL.revokeObjectURL(target.value)
  target.value = ''
}

async function previewEvidence(evidence: { id: number; name: string; type: string; status: string }) {
  if (!activeRow.value || previewingEvidenceId.value !== null) return
  previewingEvidenceId.value = evidence.id
  previewLoading.value = true
  previewVisible.value = true
  activePreviewEvidence.value = evidence
  previewData.value = null
  closeArchiveEntryPreview()
  revokePreviewUrl(previewObjectUrl)
  try {
    const detail = await getLeaderResultEvidencePreviewApi(activeRow.value.id, evidence.id)
    previewData.value = detail
    if (['IMAGE', 'PDF', 'TEXT'].includes(detail.previewType)) {
      const blob = await downloadLeaderResultEvidenceApi(activeRow.value.id, evidence.id)
      previewObjectUrl.value = URL.createObjectURL(blob)
    }
  } catch (error) {
    previewVisible.value = false
    ElMessage.error(errorMessage(error))
  } finally {
    previewLoading.value = false
    previewingEvidenceId.value = null
  }
}

async function openArchiveEntry(entry: ResultEvidenceArchiveEntry) {
  if (!activeRow.value || !activePreviewEvidence.value || entry.directory || archiveEntryLoadingPath.value) return
  archiveEntryLoadingPath.value = entry.path
  closeArchiveEntryPreview()
  try {
    const blob = await openLeaderResultEvidenceArchiveEntryApi(activeRow.value.id, activePreviewEvidence.value.id, entry.path)
    const objectUrl = URL.createObjectURL(blob)
    if (['IMAGE', 'PDF', 'TEXT'].includes(entry.previewType)) {
      activeArchiveEntry.value = entry
      archiveEntryObjectUrl.value = objectUrl
      return
    }
    const link = document.createElement('a')
    link.href = objectUrl
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    archiveEntryLoadingPath.value = ''
  }
}

function closeArchiveEntryPreview() {
  revokePreviewUrl(archiveEntryObjectUrl)
  activeArchiveEntry.value = null
}

function closeEvidencePreview() {
  previewLoading.value = false
  previewData.value = null
  activePreviewEvidence.value = null
  revokePreviewUrl(previewObjectUrl)
  closeArchiveEntryPreview()
}

function beginEvidenceDrawerResize(event: PointerEvent) {
  if (window.innerWidth <= 760) return
  event.preventDefault()
  stopEvidenceDrawerResize?.()
  const startX = event.clientX
  const startWidth = evidenceDrawerWidth.value
  const move = (pointerEvent: PointerEvent) => {
    const minimum = 460
    const maximum = Math.max(minimum, window.innerWidth - 24)
    evidenceDrawerWidth.value = Math.min(maximum, Math.max(minimum, startWidth + startX - pointerEvent.clientX))
  }
  const stop = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
    document.body.style.removeProperty('cursor')
    document.body.style.removeProperty('user-select')
    stopEvidenceDrawerResize = null
  }
  stopEvidenceDrawerResize = stop
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop, { once: true })
}

async function downloadEvidence(evidence: { id: number; name: string }) {
  if (!activeRow.value || downloadingEvidenceId.value !== null) return
  downloadingEvidenceId.value = evidence.id
  try {
    const blob = await downloadLeaderResultEvidenceApi(activeRow.value.id, evidence.id)
    saveBlob(blob, evidence.name)
    ElMessage.success('成果证据已下载')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    downloadingEvidenceId.value = null
  }
}

function openSuggest(row: ResultSuggestionRow, type: string) {
  if (row.terminal) {
    ElMessage.warning('成果已完成审批，不能再次处理')
    return
  }
  activeRow.value = row
  Object.assign(suggestForm, {
    type,
    comment: type === '审批通过' ? '成果内容与证据符合要求，同意通过。' : '成果或证据需要补充，请修改后重新提交。',
  })
  suggestVisible.value = true
}

function isRowSelected(monthKey: string, employeeKey: string, row: ResultSuggestionRow) {
  const key = selectionKey(monthKey, employeeKey)
  return selectedRowsByMonth.get(key)?.some((item) => item.id === row.id) || false
}

function toggleRowSelection(monthKey: string, employeeKey: string, row: ResultSuggestionRow, selected: boolean) {
  const key = selectionKey(monthKey, employeeKey)
  const rows = selectedRowsByMonth.get(key) || []
  const next = selected ? [...rows.filter((item) => item.id !== row.id), row] : rows.filter((item) => item.id !== row.id)
  if (next.length) selectedRowsByMonth.set(key, next)
  else selectedRowsByMonth.delete(key)
}

function canSelect(row: ResultSuggestionRow) {
  return !row.terminal && !actionBusy.value
}

function clearSelection() {
  selectedRowsByMonth.clear()
}

async function batchSuggest(type: string) {
  if (actionBusy.value) return
  try {
    const rows = selectedRows.value.filter((row) => !row.terminal)
    const skippedCount = selectedRows.value.length - rows.length
    if (!rows.length) {
      ElMessage.warning('当前选择中没有可审批的成果')
      return
    }
    await ElMessageBox.confirm(`确认将选中的 ${rows.length} 条成果设为“${type}”？`, '批量审批', { type: 'warning' })
    batchAction.value = type
    const results = await batchSubmitResultSuggestionsApi({
      ids: rows.map((row) => row.id),
      decision: suggestionStatusCodes[type] || 'SUGGEST_CONFIRM',
      comment: type === '审批通过' ? '批量审批通过。' : '批量审批驳回，请补充成果或证据。',
      notifyEmployee: true,
    })
    rows.forEach((row) => {
      row.suggestion = type
      row.resultStatus = type === '审批通过' ? '已确认' : '已驳回'
      row.terminal = true
    })
    clearSelection()
    ElMessage.success(`已完成 ${results.length} 条成果的批量审批${skippedCount ? `，跳过 ${skippedCount} 条已终结成果` : ''}`)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    batchAction.value = ''
  }
}

async function submitSuggest() {
  if (!activeRow.value || submitting.value) return
  const comment = suggestForm.comment.trim()
  if (!comment) {
    ElMessage.warning('请填写审批意见')
    return
  }
  submitting.value = true
  try {
    const result = await submitResultSuggestionApi(activeRow.value.id, {
      decision: suggestionStatusCodes[suggestForm.type] || 'SUGGEST_CONFIRM',
      comment,
      notifyEmployee: true,
    })
    activeRow.value.suggestion = suggestForm.type
    activeRow.value.resultStatus = suggestForm.type === '审批通过' ? '已确认' : '已驳回'
    activeRow.value.terminal = true
    suggestVisible.value = false
    ElMessage.success(result.message)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    await loadOrgScope()
    await refresh(false)
    autoQuery.resume()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
})

onBeforeUnmount(() => {
  stopEvidenceDrawerResize?.()
  closeEvidencePreview()
})
</script>

<style scoped>
.page-header { align-items:center; min-height:36px; padding-bottom:10px; }
.result-page-kicker { margin-right:10px; color:var(--blue); font-size:12px; font-weight:750; }
.result-page-hint { color:var(--muted); font-size:12px; }
.result-approval-type-switch {
  margin: 0 0 18px;
  border-bottom: 1px solid var(--line);
}

.result-approval-tabs {
  width: 100%;
}

.result-center-view-tabs { width:100%; margin-bottom:6px; }
.result-center-view-tabs :deep(.el-tabs__header) { margin:0; }
.result-center-view-tabs :deep(.el-tabs__item) { height:42px; color:#63736e; font-size:14px; font-weight:700; }
.result-center-view-tabs :deep(.el-tabs__item.is-active) { color:var(--blue); }
.result-center-view-tabs :deep(.el-tabs__active-bar) { background:var(--blue); }
.result-center-view-tabs :deep(.el-tabs__content) { display:none; }
.leader-result-progress,.leader-result-archive { margin-top:16px; }
.leader-progress-list,.leader-archive-list { display:grid; gap:8px; margin-top:14px; }
.leader-progress-row,.leader-archive-row { display:grid; grid-template-columns:minmax(180px, .8fr) minmax(160px, 1.2fr) 140px; gap:16px; align-items:center; min-height:64px; padding:12px 14px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.leader-progress-row > div:first-child,.leader-progress-row > div:last-child,.leader-archive-row > div { display:grid; gap:4px; min-width:0; }
.leader-progress-row span,.leader-archive-row span { color:var(--muted); font-size:12px; }
.leader-progress-row strong,.leader-archive-row strong { overflow:hidden; color:var(--ink); font-size:14px; text-overflow:ellipsis; white-space:nowrap; }
.leader-progress-bar { height:8px; overflow:hidden; border-radius:999px; background:#e8efeb; }
.leader-progress-bar span { display:block; height:100%; background:var(--blue); }
.leader-progress-row > div:last-child { grid-template-columns:1fr auto; align-items:center; }
.leader-progress-row > div:last-child strong { color:var(--blue); font-variant-numeric:tabular-nums; }
.leader-archive-row { grid-template-columns:150px minmax(0,1fr) auto auto; }
.leader-result-empty { display:grid; min-height:220px; place-items:center; color:var(--muted); }

.result-approval-tabs :deep(.el-tabs__header) {
  margin: 0;
}

.result-approval-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.result-approval-tabs :deep(.el-tabs__item) {
  height: 46px;
  padding: 0 22px;
  color: #63736e;
  font-size: 14px;
  font-weight: 650;
  transition: color .18s ease, background-color .18s ease;
}

.result-approval-tabs :deep(.el-tabs__item:hover),
.result-approval-tabs :deep(.el-tabs__item:focus-visible) {
  color: var(--blue);
  background: #f4f8f5;
}

.result-approval-tabs :deep(.el-tabs__item.is-active) {
  color: var(--navy-800);
  font-weight: 750;
}

.result-approval-tabs :deep(.el-tabs__active-bar) {
  height: 3px;
  border-radius: 3px 3px 0 0;
  background: var(--blue);
}

.result-approval-tabs :deep(.el-tabs__content) {
  display: none;
}

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
.result-month-content { background: var(--surface); }
.employee-filter-panel { padding: 14px; border-bottom: 1px solid var(--line); background: #fbfcfa; }
.employee-filter-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.employee-filter-heading > div { display: flex; align-items: baseline; gap: 10px; }
.employee-filter-heading strong { font-size: 14px; }
.employee-filter-heading span { color: var(--muted); font-size: 12px; }
.employee-filter-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 11px; }
.employee-filter-button { display: flex; align-items: center; gap: 7px; min-height: 38px; padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink); background: var(--surface); cursor: pointer; transition: border-color 180ms ease, background-color 180ms ease, color 180ms ease; }
.employee-filter-button:hover { border-color: #7fa89e; background: #f3f7f5; }
.employee-filter-button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.employee-filter-button.is-active { color: #155f55; border-color: #4b8f83; background: #eaf4f0; }
.employee-filter-button .el-icon { color: #52746d; font-size: 15px; }
.employee-filter-button strong { max-width: 140px; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.employee-filter-button span { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.employee-filter-button em { padding-left: 7px; color: #a34d2e; border-left: 1px solid #d9dfda; font-size: 12px; font-style: normal; }
.employee-filter-button em.is-selected { color: #155f55; }
.employee-result-prompt { display: flex; align-items: center; justify-content: center; gap: 12px; min-height: 96px; padding: 18px; color: var(--muted); }
.employee-result-prompt > .el-icon { color: #6e8d86; font-size: 22px; }
.employee-result-prompt div { display: grid; gap: 3px; }
.employee-result-prompt strong { color: var(--ink); font-size: 14px; }
.employee-result-prompt span { font-size: 12px; }
.employee-result-context { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 50px; padding: 9px 14px; border-bottom: 1px solid var(--line); background: #f7f8f4; }
.employee-result-context > div { display: flex; align-items: baseline; gap: 9px; min-width: 0; }
.employee-result-context strong { font-size: 15px; }
.employee-result-context span { color: #9b482e; font-size: 12px; }
.employee-result-context > div span, .employee-result-context span.is-complete { color: var(--muted); }
.employee-result-view { min-width: 0; }
.result-approval-list { display: grid; }
.result-approval-item { display: grid; grid-template-columns: 28px minmax(0, 1fr) 132px; gap: 12px; align-items: start; padding: 12px 16px; border-bottom: 1px solid var(--line); background: var(--surface); }
.result-approval-item:last-child { border-bottom: 0; }
.result-approval-item:hover { background: #fbfcfa; }
.result-approval-item.is-terminal { background: #fafbf9; }
.result-select { margin-top: 4px; }
.result-approval-main { display: grid; gap: 8px; min-width: 0; }
.result-approval-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.result-approval-title > strong { min-width: 0; color: var(--ink); font-size: 15px; line-height: 1.55; overflow-wrap: anywhere; }
.result-approval-title > div { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 4px; }
.result-approval-meta { display: grid; grid-template-columns: minmax(150px, .7fr) minmax(220px, 1.3fr); gap: 12px 24px; }
.result-approval-meta > span { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 8px; align-items: baseline; min-width: 0; }
.result-approval-meta small { color: var(--muted); font-size: 12px; }
.result-approval-meta strong { min-width: 0; color: #37423f; font-size: 13px; font-weight: 500; overflow-wrap: anywhere; }
.result-check-summary { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px; align-items: center; min-width: 0; }
.result-check-summary > span { min-width: 0; overflow: hidden; color: #4b5653; font-size: 13px; line-height: 1.6; text-overflow: ellipsis; white-space: nowrap; }
.result-check-summary .el-button { margin: 0; }
.evidence-name-button { max-width: 100%; padding: 0; border: 0; color: var(--primary); background: transparent; font: inherit; text-align: left; overflow-wrap: anywhere; cursor: pointer; }
.evidence-name-button:hover { text-decoration: underline; }
.result-approval-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.result-approval-actions .el-button:first-child { grid-column: 1 / -1; }
.result-approval-actions .el-button { width: 100%; margin: 0; }
.month-pagination { display: flex; justify-content: flex-end; padding: 14px 0; }

:deep(.evidence-drawer) { max-width: calc(100vw - 24px); }
:deep(.evidence-drawer .el-drawer__body) { position: relative; overflow-x: hidden; padding: 16px 24px 24px; }
.drawer-stack { container: evidence-drawer / inline-size; display: grid; gap: 16px; min-width: 0; }
:deep(.evidence-drawer .ai-review) { min-width: 0; max-width: 100%; overflow: hidden; }
.drawer-resize-handle { position: absolute; z-index: 3; top: 0; bottom: 0; left: 0; width: 14px; padding: 0; border: 0; background: transparent; transform: translateX(-50%); cursor: col-resize; }
.drawer-resize-handle span { position: absolute; top: 50%; left: 50%; width: 4px; height: 48px; border-radius: 2px; background: #9aaba6; transform: translate(-50%, -50%); opacity: .58; transition: opacity 160ms ease, background-color 160ms ease; }
.drawer-resize-handle:hover span, .drawer-resize-handle:focus-visible span { background: var(--primary); opacity: 1; }
.drawer-resize-handle:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.evidence-file-list { display: grid; gap: 8px; }
.evidence-file-item { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 12px; border: 1px solid var(--line); border-radius: 6px; background: #fbfcfa; }
.evidence-file-type, .archive-entry-type { display: grid; place-items: center; min-height: 34px; padding: 0 6px; border-radius: 4px; color: #315f57; background: #e8f1ee; font-size: 11px; font-weight: 700; text-transform: uppercase; }
.evidence-file-item > div:nth-child(2) { display: grid; gap: 4px; min-width: 0; }
.evidence-file-item strong { color: var(--ink); font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.evidence-file-item span { color: var(--muted); font-size: 12px; }
.evidence-file-actions { display: flex; gap: 8px; }
.evidence-file-actions .el-button { margin: 0; }
:deep(.evidence-preview-dialog .el-dialog__body) { padding: 0 20px 18px; }
.evidence-preview-body { min-height: 320px; }
.evidence-image-preview { display: block; max-width: 100%; max-height: 68vh; margin: 0 auto; object-fit: contain; }
.evidence-document-frame { width: 100%; height: 68vh; border: 1px solid var(--line); background: #fff; }
.evidence-text-preview { max-height: 68vh; margin: 0; padding: 20px; overflow: auto; border: 1px solid var(--line); color: #28312f; background: #fbfcfa; font-family: inherit; font-size: 14px; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.evidence-preview-fallback { display: grid; place-items: center; align-content: center; gap: 8px; min-height: 320px; color: var(--muted); text-align: center; }
.evidence-preview-fallback strong { color: var(--ink); font-size: 16px; }
.archive-preview { display: grid; gap: 16px; }
.archive-preview-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 16px; border: 1px solid var(--line); background: #f7f8f4; }
.archive-preview-heading > div { display: flex; align-items: baseline; gap: 10px; }
.archive-preview-heading span { color: var(--muted); font-size: 12px; }
.archive-entry-preview { border: 1px solid var(--line); background: #fff; }
.archive-entry-preview-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 8px 12px; border-bottom: 1px solid var(--line); }
.archive-entry-preview-head strong { min-width: 0; font-size: 13px; overflow-wrap: anywhere; }
.archive-entry-preview img { display: block; max-width: 100%; max-height: 52vh; margin: 0 auto; object-fit: contain; }
.archive-entry-preview iframe { width: 100%; height: 52vh; border: 0; }
.archive-entry-list { display: grid; max-height: 52vh; overflow: auto; border: 1px solid var(--line); }
.archive-entry-item { display: grid; grid-template-columns: 52px minmax(0, 1fr) 78px 46px; gap: 12px; align-items: center; min-height: 54px; padding: 8px 12px; border: 0; border-bottom: 1px solid var(--line); color: var(--ink); background: #fff; text-align: left; cursor: pointer; }
.archive-entry-item:last-child { border-bottom: 0; }
.archive-entry-item:not(:disabled):hover { background: #f3f7f5; }
.archive-entry-item.is-folder { cursor: default; }
.archive-entry-item > span:nth-child(2) { display: grid; gap: 2px; min-width: 0; }
.archive-entry-item strong, .archive-entry-item small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.archive-entry-item strong { font-size: 13px; }
.archive-entry-item small, .archive-entry-item em { color: var(--muted); font-size: 11px; font-style: normal; }
.archive-entry-item b { color: var(--primary); font-size: 12px; text-align: right; }

@container evidence-drawer (max-width: 720px) {
  .evidence-file-item { grid-template-columns: 42px minmax(0, 1fr); }
  .evidence-file-actions { grid-column: 2; }
  .archive-preview-heading { align-items: flex-start; flex-direction: column; gap: 8px; }
  .archive-entry-item { grid-template-columns: 48px minmax(0, 1fr) 40px; }
  .archive-entry-item em { display: none; }
}

@media (max-width: 900px) {
  .leader-progress-row { grid-template-columns:1fr 110px; }
  .leader-progress-bar { grid-column:1 / -1; grid-row:2; }
  .leader-archive-row { grid-template-columns:1fr auto; }
  .leader-archive-row > div:nth-child(2) { grid-column:1 / -1; grid-row:2; }
  .leader-archive-row > .el-button { justify-self:end; }
  .employee-filter-heading,
  .employee-result-context { align-items: flex-start; flex-direction: column; gap: 6px; }
  .employee-filter-heading > div,
  .employee-result-context > div { align-items: flex-start; flex-direction: column; gap: 3px; }
  .employee-filter-button { flex: 1 1 180px; }
  .result-approval-item { grid-template-columns: 28px minmax(0, 1fr); }
  .result-approval-actions { grid-column: 2; display: flex; flex-wrap: wrap; }
  .result-approval-actions .el-button { width: auto; }
  .result-approval-meta { grid-template-columns: 1fr; }
  .evidence-file-item { grid-template-columns: 42px minmax(0, 1fr); }
  .evidence-file-actions { grid-column: 2; }
}

@media (max-width: 760px) {
  :deep(.evidence-drawer) { width: 100% !important; max-width: 100%; }
  .drawer-resize-handle { display: none; }
  .result-approval-title, .archive-preview-heading { align-items: flex-start; flex-direction: column; }
  .result-approval-title > div { justify-content: flex-start; }
  .result-approval-meta > span { grid-template-columns: 1fr; gap: 2px; }
  .result-check-summary { grid-template-columns: auto minmax(0, 1fr); }
  .result-check-summary .el-button { grid-column: 2; justify-self: start; }
  .archive-entry-item { grid-template-columns: 48px minmax(0, 1fr) 40px; }
  .archive-entry-item em { display: none; }
}
</style>
