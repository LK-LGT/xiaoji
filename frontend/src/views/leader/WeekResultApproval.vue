<template>
  <section class="week-result-approval-page" :class="{ 'page-panel': !embedded, 'is-embedded': embedded }">
    <header v-if="!embedded" class="page-header week-result-approval-header">
      <div>
        <h1 class="page-title">{{ pageTitle }}</h1>
        <p class="page-subtitle">{{ pageSubtitle }}</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadRows">刷新</el-button>
    </header>

    <div class="filter-bar week-result-approval-filter">
      <el-input
        v-model="keyword"
        clearable
        :prefix-icon="Search"
        placeholder="搜索姓名、工号或工作内容"
        @keyup.enter="search"
        @clear="search"
      />
      <el-date-picker
        v-model="weekStart"
        type="date"
        format="YYYY年 第ww周"
        value-format="YYYY-MM-DD"
        clearable
        placeholder="全部统计周"
        @change="normalizeSelectedWeek"
      />
      <el-select v-model="status" clearable placeholder="全部审批状态">
        <el-option label="未提交" value="NOT_SUBMITTED" />
        <el-option label="待审批" value="SUBMITTED" />
        <el-option label="已通过" value="APPROVED" />
        <el-option label="已驳回" value="REJECTED" />
      </el-select>
      <el-button type="primary" :icon="Search" @click="search">查询</el-button>
      <el-button :disabled="!hasFilter" @click="resetFilters">重置</el-button>
      <el-button v-if="embedded" :icon="Refresh" :loading="loading" @click="loadRows">刷新</el-button>
    </div>

    <div class="week-result-approval-summary" aria-live="polite">
      <span>共 <strong>{{ total }}</strong> 条周成果记录</span>
      <small>{{ isDepartmentWorkspace ? '这里只审批领导本人周成果' : '这里只审批直属员工周成果' }}</small>
    </div>

    <el-table
      v-loading="loading"
      class="week-result-approval-table leader-compact-table"
      :data="rows"
      row-key="id"
      empty-text="暂无员工周成果记录"
    >
      <el-table-column label="序号" width="64" align="center">
        <template #default="{ $index }">{{ (pageNo - 1) * pageSize + $index + 1 }}</template>
      </el-table-column>
      <el-table-column :label="ownerLabel" min-width="150">
        <template #default="{ row }">
          <button type="button" class="approval-owner" @click="openDetail(row)">
            <span>{{ employeeInitial(row.employeeName) }}</span>
            <span><strong>{{ row.employeeName }}</strong><small>{{ row.employeeNo || '未填写工号' }}</small></span>
          </button>
        </template>
      </el-table-column>
      <el-table-column prop="groupName" label="项目组" min-width="120" show-overflow-tooltip />
      <el-table-column label="统计周期" min-width="190">
        <template #default="{ row }">{{ periodText(row.weekStart, row.weekEnd) }}</template>
      </el-table-column>
      <el-table-column label="工作内容" min-width="260">
        <template #default="{ row }"><div class="approval-content-preview">{{ row.workContent }}</div></template>
      </el-table-column>
      <el-table-column label="周计划标准" min-width="240">
        <template #default="{ row }">
          <div class="approval-standard-preview">
            <span>交付物：{{ row.planDeliverable || '未填写' }}</span>
            <span>质量：{{ row.quality || '未填写' }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="成果状态" width="96" align="center">
        <template #default="{ row }"><el-tag :type="statusMeta(row.status).type" effect="plain">{{ statusMeta(row.status).label }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="186" fixed="right" align="center">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">查看</el-button>
          <el-button v-if="row.status === 'SUBMITTED'" link type="success" @click="approve(row)">通过</el-button>
          <el-button v-if="row.status === 'SUBMITTED'" link type="danger" @click="reject(row)">驳回</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="total > 0" class="week-result-approval-pagination">
      <span>每页 {{ pageSize }} 条</span>
      <el-pagination
        v-model:current-page="pageNo"
        :page-size="pageSize"
        layout="prev, pager, next, total"
        :total="total"
        @current-change="loadRows"
      />
    </div>

    <el-drawer v-model="detailVisible" :title="`${ownerLabel}周成果详情`" size="min(820px, 96vw)">
      <template v-if="activeRow">
        <div class="week-result-detail-heading">
          <div>
            <strong>{{ activeRow.employeeName }}</strong>
            <span>{{ activeRow.groupName || '未分配项目组' }} · {{ periodText(activeRow.weekStart, activeRow.weekEnd) }}</span>
          </div>
          <el-tag :type="statusMeta(activeRow.status).type" effect="plain">{{ statusMeta(activeRow.status).label }}</el-tag>
        </div>

        <dl class="week-result-detail-list">
          <div><dt>工作计划内容</dt><dd>{{ activeRow.workContent || '未填写' }}</dd></div>
          <div><dt>成果交付物</dt><dd>{{ activeRow.deliverable || '未填写' }}</dd></div>
          <div><dt>完成情况</dt><dd>{{ activeRow.completionStatus || '未填写' }}</dd></div>
          <div><dt>备注</dt><dd>{{ activeRow.remark || '无' }}</dd></div>
          <div><dt>计划交付物</dt><dd>{{ activeRow.planDeliverable || '未填写' }}</dd></div>
          <div><dt>质量标准</dt><dd>{{ activeRow.quality || '未填写' }}</dd></div>
        </dl>

        <div v-if="activeRow.status === 'NOT_SUBMITTED'" class="week-result-not-submitted-hint">
          该员工的周计划已经审批通过，但尚未提交本周成果。
        </div>

        <section class="week-result-evidence-section">
          <h3>成果证据</h3>
          <div v-if="activeRow.evidences.length" class="week-result-evidence-list">
            <button
              v-for="evidence in activeRow.evidences"
              :key="evidence.id"
              type="button"
              :disabled="downloadingEvidenceId !== null"
              @click="openEvidence(evidence)"
            >
              <el-icon><Paperclip /></el-icon>
              <span><strong>{{ evidence.fileName }}</strong><small>{{ fileSizeText(evidence.fileSize) }}</small></span>
              <el-icon v-if="downloadingEvidenceId === evidence.id" class="is-loading"><Loading /></el-icon>
              <el-icon v-else><View /></el-icon>
            </button>
          </div>
          <el-empty v-else :image-size="56" description="本次成果未上传证据文件" />
        </section>

        <section v-if="activeRow.approvalComment || activeRow.approveAt" class="week-result-decision-record">
          <strong>审批记录</strong>
          <span>{{ activeRow.approvalComment || '无补充意见' }}</span>
          <small>{{ formatDateTime(activeRow.approveAt) }}</small>
        </section>

        <div v-if="activeRow.status === 'SUBMITTED'" class="week-result-detail-actions">
          <el-button type="danger" plain @click="reject(activeRow)">驳回</el-button>
          <el-button type="primary" @click="approve(activeRow)">审批通过</el-button>
        </div>
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, Paperclip, Refresh, Search, View } from '@element-plus/icons-vue'
import {
  approveDepartmentWeekResultApi,
  approveLeaderWeekResultApi,
  downloadWeekResultApprovalEvidenceApi,
  listDepartmentWeekResultApprovalsApi,
  listLeaderWeekResultApprovalsApi,
  rejectDepartmentWeekResultApi,
  rejectLeaderWeekResultApi,
  type WeekResultApprovalItem,
  type WeekResultEvidence,
} from '@/api/weekResult'

const props = withDefaults(defineProps<{
  embedded?: boolean
  workspace?: 'leader' | 'department'
}>(), {
  embedded: false,
})
const route = useRoute()
const isDepartmentWorkspace = computed(() => props.workspace
  ? props.workspace === 'department'
  : route.path.startsWith('/department/'))
const embedded = computed(() => props.embedded)
const pageTitle = computed(() => isDepartmentWorkspace.value ? '领导周成果审批' : '员工周成果审批')
const ownerLabel = computed(() => isDepartmentWorkspace.value ? '直属领导' : '员工')
const pageSubtitle = computed(() => isDepartmentWorkspace.value
  ? '审批直属领导本人提交的周成果；普通员工周成果由其直属领导审批。'
  : '核对直属员工的完成情况、交付物和成果证据，审批结果计入项目组周成果进度。')
const hasFilter = computed(() => Boolean(keyword.value.trim() || weekStart.value || status.value))

const loading = ref(false)
const rows = ref<WeekResultApprovalItem[]>([])
const total = ref(0)
const pageNo = ref(1)
const pageSize = 10
const keyword = ref('')
const weekStart = ref('')
const status = ref<'' | 'NOT_SUBMITTED' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'>('')
const detailVisible = ref(false)
const activeRow = ref<WeekResultApprovalItem | null>(null)
const downloadingEvidenceId = ref<string | null>(null)

function mondayOf(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(year, month - 1, day)
  date.setDate(date.getDate() - ((date.getDay() + 6) % 7))
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
}

function normalizeSelectedWeek(value?: string) {
  weekStart.value = value ? mondayOf(value) : ''
}

function periodText(start: string, end: string) { return `${start} 至 ${end}` }
function employeeInitial(name: string) { return name?.trim().slice(-1) || '员' }
function formatDateTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 16) : '' }
function fileSizeText(size: number) {
  if (!size) return '大小未知'
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
function statusMeta(value: WeekResultApprovalItem['status']) {
  return ({
    NOT_SUBMITTED: { label: '未提交', type: 'info' as const },
    SUBMITTED: { label: '待审批', type: 'warning' as const },
    APPROVED: { label: '已通过', type: 'success' as const },
    REJECTED: { label: '已驳回', type: 'danger' as const },
  })[value]
}

async function loadRows() {
  loading.value = true
  try {
    const params = {
      keyword: keyword.value.trim() || undefined,
      weekStart: weekStart.value || undefined,
      status: status.value,
      pageNo: pageNo.value,
      pageSize,
    }
    const data = isDepartmentWorkspace.value
      ? await listDepartmentWeekResultApprovalsApi(params)
      : await listLeaderWeekResultApprovalsApi(params)
    rows.value = data.items
    total.value = data.total
    if (!data.items.length && data.total > 0 && pageNo.value > 1) {
      pageNo.value = Math.ceil(data.total / pageSize)
      await loadRows()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '周成果审批列表加载失败')
  } finally {
    loading.value = false
  }
}

async function search() { pageNo.value = 1; await loadRows() }
async function resetFilters() {
  keyword.value = ''
  weekStart.value = ''
  status.value = ''
  pageNo.value = 1
  await loadRows()
}
function openDetail(row: WeekResultApprovalItem) { activeRow.value = row; detailVisible.value = true }

async function approve(row: WeekResultApprovalItem) {
  try {
    const { value } = await ElMessageBox.prompt('可填写审批意见', '通过周成果', {
      inputValue: '同意，成果符合本周计划要求',
      confirmButtonText: '确认通过',
      inputValidator: (text) => text.length <= 500 || '审批意见不能超过500个字符',
    })
    if (isDepartmentWorkspace.value) await approveDepartmentWeekResultApi(row.id, value.trim())
    else await approveLeaderWeekResultApi(row.id, value.trim())
    ElMessage.success('周成果已审批通过')
    detailVisible.value = false
    await loadRows()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '审批失败')
  }
}

async function reject(row: WeekResultApprovalItem) {
  try {
    const { value } = await ElMessageBox.prompt('说明需要修改的内容，提交人可修改后重新提交', '驳回周成果', {
      confirmButtonText: '确认驳回',
      inputValidator: (text) => Boolean(text.trim()) && text.length <= 500 || '请输入不超过500个字符的驳回原因',
    })
    if (isDepartmentWorkspace.value) await rejectDepartmentWeekResultApi(row.id, value.trim())
    else await rejectLeaderWeekResultApi(row.id, value.trim())
    ElMessage.success('周成果已驳回')
    detailVisible.value = false
    await loadRows()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '驳回失败')
  }
}

async function openEvidence(evidence: WeekResultEvidence) {
  if (!activeRow.value || downloadingEvidenceId.value !== null) return
  const previewable = ['pdf', 'png', 'jpg', 'jpeg', 'webp'].includes(evidence.fileType.toLowerCase())
  const previewWindow = previewable ? window.open('', '_blank') : null
  downloadingEvidenceId.value = evidence.id
  try {
    const workspace = isDepartmentWorkspace.value ? 'department' : 'leader'
    const blob = await downloadWeekResultApprovalEvidenceApi(workspace, activeRow.value.id, evidence.id)
    const url = URL.createObjectURL(blob)
    if (previewable && previewWindow) {
      previewWindow.location.href = url
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } else {
      previewWindow?.close()
      const link = document.createElement('a')
      link.href = url
      link.download = evidence.fileName
      link.click()
      URL.revokeObjectURL(url)
    }
  } catch (error) {
    previewWindow?.close()
    ElMessage.error(error instanceof Error ? error.message : '成果证据打开失败')
  } finally {
    downloadingEvidenceId.value = null
  }
}

onMounted(async () => {
  await loadRows()
  // BUG-R6-14：成果 ID 是字符串主键。
  const requestedId = String(route.query.id || '')
  const requested = rows.value.find((item) => item.id === requestedId)
  if (requested) openDetail(requested)
})
</script>

<style scoped>
.week-result-approval-page.is-embedded { padding-top: 2px; }
.week-result-approval-header { padding-bottom:18px; border-bottom:1px solid var(--line); }
.week-result-approval-filter { padding-left:0; }
.week-result-approval-filter::before { display:none; }
.week-result-approval-filter .el-input { width:280px; }
.week-result-approval-filter .el-date-editor { width:190px; }
.week-result-approval-filter .el-select { width:150px; }
.week-result-approval-summary { display:flex; align-items:center; justify-content:space-between; gap:16px; margin:4px 0 12px; color:var(--muted); font-size:12px; }
.week-result-approval-summary strong { color:var(--ink); font-family:"IBM Plex Mono","Cascadia Mono",monospace; font-size:16px; }
.week-result-approval-table { --el-table-border-color:#d9e1dc; --el-table-header-bg-color:#f1f5f2; }
.approval-owner { display:flex; align-items:center; gap:9px; width:100%; padding:2px 0; border:0; color:inherit; background:transparent; text-align:left; cursor:pointer; }
.approval-owner > span:first-child { display:grid; width:30px; height:30px; flex:0 0 auto; place-items:center; border-radius:50%; color:#fff; background:#315e59; font-size:11px; font-weight:700; }
.approval-owner > span:last-child,.approval-owner strong,.approval-owner small { display:block; min-width:0; }
.approval-owner strong { color:var(--ink); font-size:13px; }
.approval-owner small { margin-top:2px; color:var(--muted); font-size:10px; }
.approval-owner:hover strong,.approval-owner:focus-visible strong { color:var(--blue); }
.approval-content-preview { display:-webkit-box; max-height:48px; overflow:hidden; line-height:1.55; white-space:pre-wrap; overflow-wrap:anywhere; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
.approval-standard-preview { display:grid; gap:3px; line-height:1.45; color:#405750; font-size:12px; }
.approval-standard-preview span { display:-webkit-box; max-height:38px; overflow:hidden; white-space:pre-wrap; overflow-wrap:anywhere; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
.week-result-approval-pagination { display:flex; align-items:center; justify-content:space-between; gap:16px; padding-top:14px; color:var(--muted); font-size:12px; }
.week-result-detail-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding-bottom:16px; border-bottom:1px solid var(--line); }
.week-result-detail-heading > div { display:grid; gap:5px; }
.week-result-detail-heading strong { color:var(--ink); font-size:18px; }
.week-result-detail-heading span { color:var(--muted); font-size:12px; }
.week-result-detail-list { display:grid; margin:18px 0 0; border:1px solid var(--line); }
.week-result-detail-list > div { display:grid; grid-template-columns:120px minmax(0,1fr); }
.week-result-detail-list > div + div { border-top:1px solid var(--line); }
.week-result-detail-list dt,.week-result-detail-list dd { margin:0; padding:12px 14px; line-height:1.65; white-space:pre-wrap; overflow-wrap:anywhere; }
.week-result-detail-list dt { color:#476058; background:#f2f5f2; font-size:12px; font-weight:700; }
.week-result-detail-list dd { border-left:1px solid var(--line); color:var(--ink); }
.week-result-evidence-section { margin-top:20px; }
.week-result-evidence-section h3 { margin:0 0 10px; color:var(--ink); font-size:14px; }
.week-result-evidence-list { display:grid; gap:8px; }
.week-result-evidence-list button { display:grid; grid-template-columns:20px minmax(0,1fr) 20px; align-items:center; gap:10px; width:100%; min-height:52px; padding:8px 12px; border:1px solid var(--line); border-radius:6px; color:#315e59; background:#fff; text-align:left; cursor:pointer; }
.week-result-evidence-list button:hover:not(:disabled),.week-result-evidence-list button:focus-visible { border-color:#7ca397; background:#f5f9f7; }
.week-result-evidence-list button:disabled { cursor:wait; opacity:.72; }
.week-result-evidence-list button > span { display:grid; gap:3px; min-width:0; }
.week-result-evidence-list strong { overflow:hidden; color:var(--ink); font-size:13px; text-overflow:ellipsis; white-space:nowrap; }
.week-result-evidence-list small { color:var(--muted); font-size:11px; }
.week-result-decision-record { display:grid; gap:5px; margin-top:18px; padding:12px 14px; border:1px solid var(--line); background:#f7f9f7; }
.week-result-decision-record strong { color:var(--ink); font-size:12px; }
.week-result-decision-record span { white-space:pre-wrap; overflow-wrap:anywhere; }
.week-result-decision-record small { color:var(--muted); }
.week-result-not-submitted-hint { margin-top:18px; padding:12px 14px; border:1px solid #d7dfda; border-radius:6px; color:#536861; background:#f5f7f5; font-size:13px; line-height:1.6; }
.week-result-detail-actions { display:flex; justify-content:flex-end; gap:10px; margin-top:22px; padding-top:16px; border-top:1px solid var(--line); }
@media (max-width:800px) { .week-result-approval-filter .el-input,.week-result-approval-filter .el-date-editor,.week-result-approval-filter .el-select { width:100%; }.week-result-detail-list > div { grid-template-columns:1fr; }.week-result-detail-list dd { border-top:1px solid var(--line); border-left:0; } }
</style>
