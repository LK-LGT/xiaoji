<template>
  <section v-loading="loading" class="page-panel daily-review-page">
    <div class="page-header">
      <div><h1 class="page-title">日计划点评</h1><p class="page-subtitle">{{ isDepartmentMode ? '可选填写直属领导日计划的工作建议。' : '可选填写员工日计划的工作建议。' }}</p></div>
      <div class="toolbar">
        <el-button :icon="Refresh" :disabled="actionBusy" @click="refresh()">刷新</el-button>
        <el-tooltip v-if="!isDepartmentMode" :disabled="selectedRows.length > 0" content="请先在下方勾选需要打印的日计划" placement="bottom">
          <span><el-button type="primary" :icon="Printer" :loading="printing" :disabled="!canPrint" @click="printSelected">打印所选</el-button></span>
        </el-tooltip>
      </div>
    </div>

    <div class="status-strip daily-review-status mt16" aria-label="日计划点评统计">
      <span class="status-pill"><strong>{{ pendingCount }}</strong> 条未点评</span>
      <span class="status-pill"><strong>{{ tableRows.length }}</strong> 条当前记录</span>
    </div>

    <div class="filter-bar daily-review-filter" aria-label="日计划点评筛选">
      <span class="daily-review-filter__label">筛选</span>
      <el-select v-model="scopeOrgId" clearable :loading="orgLoading" :disabled="!orgOptions.length" :placeholder="isDepartmentMode ? '全部部门范围' : '全部直属范围'"><el-option v-for="item in orgOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select>
      <el-date-picker v-model="query.dateRange" type="daterange" start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD" />
      <el-select v-model="query.status" clearable placeholder="点评状态"><el-option label="未点评" value="未点评" /><el-option label="已点评" value="已点评" /></el-select>
      <el-select v-model="query.missing" clearable placeholder="字段检查"><el-option label="完整" value="无" /><el-option label="有缺失" value="missing" /></el-select>
      <el-button :icon="Search" @click="refresh()">查询</el-button><el-button @click="reset">重置</el-button>
    </div>

    <div v-if="selectedRows.length" class="batch-bar daily-review-batch">
      <div>已选择 <strong>{{ selectedRows.length }}</strong> 条日计划。</div>
      <div class="batch-actions"><el-button size="small" type="primary" :loading="batchAction === 'comment'" :disabled="!selectedReviewableRows.length || (actionBusy && batchAction !== 'comment')" @click="batchComment">批量点评</el-button><el-button size="small" :disabled="actionBusy" @click="clearSelection">清空选择</el-button></div>
    </div>

    <el-table ref="tableRef" class="daily-review-table" :data="pagedRows" row-key="id" border highlight-current-row empty-text="暂无符合条件的日计划" @selection-change="onSelectionChange" @row-dblclick="openDetail">
      <el-table-column type="selection" width="48" :selectable="canSelect" reserve-selection />
      <el-table-column label="序号" width="62" align="center"><template #default="{ $index }"><span class="daily-review-index">{{ (currentPage - 1) * pageSize + $index + 1 }}</span></template></el-table-column>
      <el-table-column label="员工" min-width="176"><template #default="{ row }"><button type="button" class="daily-review-person" @click.stop="openDetail(row)"><strong>{{ row.employee }}</strong><span>{{ row.group || '未分配组织' }}</span><small>{{ row.date }}</small></button></template></el-table-column>
      <el-table-column label="日计划内容" min-width="360"><template #default="{ row }"><button type="button" class="daily-review-content" @click.stop="openDetail(row)">{{ row.content || '未填写日计划内容' }}</button></template></el-table-column>
      <el-table-column label="点评状态" min-width="168"><template #default="{ row }"><div class="daily-review-state"><el-tag :type="reviewStatusTag(row.reviewStatus)">{{ row.reviewStatus }}</el-tag><span :class="{ 'is-warning': row.missing !== '无' }">{{ row.missing === '无' ? '字段完整' : row.missing }}</span></div></template></el-table-column>
      <el-table-column label="操作" width="180" fixed="right"><template #default="{ row }"><div class="daily-review-actions" @click.stop><el-button link type="primary" :icon="View" @click="openDetail(row)">查看</el-button><el-button link type="primary" :icon="EditPen" :disabled="!canReview(row) || actionBusy" @click="openComment(row)">点评</el-button></div></template></el-table-column>
    </el-table>

    <div class="pagination-row daily-review-pagination"><el-pagination v-model:current-page="currentPage" v-model:page-size="pageSize" :page-sizes="[10, 20, 50]" layout="total, sizes, prev, pager, next, jumper" :total="tableRows.length" background /></div>

    <el-drawer v-model="detailVisible" title="日计划详情" size="min(860px, calc(100vw - 24px))">
      <div v-if="activeRow" class="daily-review-detail">
        <header class="daily-review-detail__head"><div><span>{{ activeRow.date }}</span><h2>{{ activeRow.employee }}的日计划</h2><p>{{ activeRow.group || '未分配组织' }} · {{ formatDateTime(activeRow.submittedAt) }} 提交</p></div><el-tag :type="reviewStatusTag(activeRow.reviewStatus)">{{ activeRow.reviewStatus }}</el-tag></header>

        <section class="daily-review-plan"><div class="daily-review-plan__title"><div><span>计划内容</span><h3>当天工作安排</h3></div><small>双击列表也可进入此详情</small></div><p>{{ activeRow.content || '未填写日计划内容' }}</p></section>

        <section class="daily-review-checks"><h3>计划信息</h3><dl><div><dt>字段完整性</dt><dd :class="{ 'is-warning': activeRow.missing !== '无' }">{{ activeRow.missing === '无' ? '完整' : activeRow.missing }}</dd></div><div><dt>当前点评</dt><dd>{{ activeRow.leaderComment || '暂无点评' }}</dd></div></dl></section>

        <section class="daily-review-trace"><h3>过程留痕</h3><el-timeline><el-timeline-item :timestamp="formatDateTime(activeRow.submittedAt)">{{ isDepartmentMode ? '领导提交日计划' : '员工提交日计划' }}</el-timeline-item><el-timeline-item :timestamp="formatDateTime(activeRow.submittedAt)">系统字段检查：{{ activeRow.missing === '无' ? '通过' : activeRow.missing }}</el-timeline-item><el-timeline-item :timestamp="activeRow.reviewedAt ? formatDateTime(activeRow.reviewedAt) : '可选点评'">{{ activeRow.reviewedAt ? `${isDepartmentMode ? '部门负责人' : '直属领导'}已完成点评` : '上级可按需填写点评' }}</el-timeline-item></el-timeline></section>

        <footer v-if="canReview(activeRow)" class="daily-review-detail__actions"><el-button type="primary" :disabled="actionBusy" @click="openComment(activeRow)">提交点评</el-button></footer>
      </div>
    </el-drawer>
    <el-dialog v-model="commentVisible" title="提交点评" width="560px">
      <el-form label-position="top">
        <el-form-item label="点评内容">
          <el-input v-model="commentForm.comment" type="textarea" :rows="5" maxlength="300" show-word-limit :disabled="submitting" placeholder="可选填写工作建议，留空则不提交点评" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="submitting" @click="commentVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitComment">提交点评</el-button>
      </template>
    </el-dialog>
    <DailyPlanPrintSheet v-if="!isDepartmentMode && selectedPrintRows.length" :rows="selectedPrintRows" :plan-date="selectedPrintPeriod" />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { EditPen, Printer, Refresh, Search, View } from '@element-plus/icons-vue'
import DailyPlanPrintSheet from '@/components/DailyPlanPrintSheet.vue'
import {
  batchCommentDailyReviewsApi,
  commentDailyReviewApi,
  getDailyReviewApi,
  listDailyReviewsApi,
} from '@/api/leader'
import {
  batchCommentDepartmentDailyReviewsApi,
  commentDepartmentDailyReviewApi,
  getDepartmentDailyReviewApi,
  listDepartmentDailyReviewsApi,
} from '@/api/department'
import { errorMessage } from '@/api/performance'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { useLeaderOrgScope } from '@/composables/useLeaderOrgScope'
import { useDepartmentOrgScope } from '@/composables/useDepartmentOrgScope'
import { formatDateTime, mapDailyReview, reviewStatusCodes } from '@/views/performanceAdapters'
import { useMessageStore } from '@/stores/messages'

type DailyReviewRow = ReturnType<typeof mapDailyReview>

const route = useRoute()
const isDepartmentMode = computed(() => route.path.startsWith('/department/'))
const messageStore = useMessageStore()
const tableRef = ref()
const leaderOrgScope = useLeaderOrgScope()
const departmentOrgScope = useDepartmentOrgScope(false)
const orgOptions = computed(() => isDepartmentMode.value
  ? departmentOrgScope.orgOptions.value
  : leaderOrgScope.orgOptions.value)
const scopeOrgId = computed<number | undefined>({
  get: () => isDepartmentMode.value
    ? departmentOrgScope.selectedOrgId.value
    : leaderOrgScope.scopeOrgId.value,
  set: (value) => {
    if (isDepartmentMode.value) departmentOrgScope.selectedOrgId.value = value
    else leaderOrgScope.scopeOrgId.value = value
  },
})
const orgLoading = computed(() => isDepartmentMode.value
  ? departmentOrgScope.orgLoading.value
  : leaderOrgScope.orgLoading.value)
const routeDate = typeof route.query.date === 'string' ? route.query.date : ''
const routeDayPlanId = typeof route.query.dayPlanId === 'string' ? route.query.dayPlanId : ''
const defaultDate = formatDate(new Date())
const query = reactive({
  dateRange: routeDate ? [routeDate, routeDate] : [defaultDate, defaultDate] as string[],
  status: '',
  missing: '',
})
const tableRows = ref<DailyReviewRow[]>([])
const selectedRows = ref<DailyReviewRow[]>([])
const currentPage = ref(1)
const pageSize = ref(10)
const detailVisible = ref(false)
const commentVisible = ref(false)
const activeRow = ref<DailyReviewRow | null>(null)
const loading = ref(false)
const submitting = ref(false)
const batchAction = ref('')
const printing = ref(false)
let refreshRequestId = 0
const commentForm = reactive({
  comment: '',
})
const pendingCount = computed(() => tableRows.value.filter(canReview).length)
const pagedRows = computed(() => tableRows.value.slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value))
const actionBusy = computed(() => submitting.value || Boolean(batchAction.value))
const selectedReviewableRows = computed(() => selectedRows.value.filter(canReview))
const selectedPrintRows = computed(() => {
  const selectedIds = new Set(selectedRows.value.map((row) => row.id))
  return tableRows.value
    .filter((row) => selectedIds.has(row.id))
    .map((row) => ({ rowKey: row.id, employeeName: row.employee, workContent: row.content || '' }))
})
const selectedPrintPeriod = computed(() => {
  const dates = [...new Set(selectedRows.value.map((row) => row.date).filter(Boolean))].sort()
  if (!dates.length) return ''
  return dates.length === 1 ? dates[0] : `${dates[0]} 至 ${dates[dates.length - 1]}`
})
const canPrint = computed(() => selectedPrintRows.value.length > 0 && !loading.value && !actionBusy.value && !printing.value)
const autoQuery = useAutoQuery(
  () => [scopeOrgId.value, query.dateRange?.[0], query.dateRange?.[1], query.status, query.missing],
  () => refresh(false),
)

async function refresh(showMessage = true) {
  const requestId = ++refreshRequestId
  loading.value = true
  try {
    const request = {
      scopeOrgId: scopeOrgId.value,
      startDate: query.dateRange?.[0] || undefined,
      endDate: query.dateRange?.[1] || undefined,
      reviewStatus: reviewStatusCodes[query.status] || undefined,
      missingOnly: query.missing === 'missing' ? true : query.missing === '无' ? false : undefined,
    }
    const data = isDepartmentMode.value
      ? await listDepartmentDailyReviewsApi(request)
      : await listDailyReviewsApi(request)
    if (requestId !== refreshRequestId) return
    tableRows.value = data.map(mapDailyReview)
    currentPage.value = 1
    clearSelection()
    if (showMessage) ElMessage.success('查询条件已应用')
  } catch (error) {
    if (requestId !== refreshRequestId) return
    ElMessage.error(errorMessage(error))
  } finally {
    if (requestId === refreshRequestId) loading.value = false
  }
}

async function reset() {
  autoQuery.pause()
  if (isDepartmentMode.value) departmentOrgScope.resetOrgScope()
  else leaderOrgScope.resetOrgScope()
  query.dateRange = [defaultDate, defaultDate]
  query.status = ''
  query.missing = ''
  await refresh(false)
  autoQuery.resume()
  ElMessage.success('筛选条件已重置')
}

async function openDetail(row: DailyReviewRow) {
  await openDetailById(row.id)
}

async function openDetailById(id: string) {
  try {
    const detail = isDepartmentMode.value ? await getDepartmentDailyReviewApi(id) : await getDailyReviewApi(id)
    activeRow.value = mapDailyReview(detail)
    detailVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function openComment(row: DailyReviewRow) {
  activeRow.value = row
  commentForm.comment = ''
  commentVisible.value = true
}

function onSelectionChange(rows: DailyReviewRow[]) {
  const selectedIds = new Set(rows.map((row) => row.id))
  selectedRows.value = tableRows.value.filter((row) => selectedIds.has(row.id))
}

function canReview(row: DailyReviewRow) {
  return row.reviewStatus === '未点评'
}

function canSelect() {
  return !actionBusy.value
}

async function printSelected() {
  if (!canPrint.value) return
  printing.value = true
  try {
    await nextTick()
    window.print()
  } finally {
    printing.value = false
  }
}

function reviewStatusTag(status: string): 'success' | 'danger' | 'warning' {
  if (status === '已点评' || status === '风险已复核') return 'success'
  if (status === '风险' || status === '需补充') return 'danger'
  return 'warning'
}

function formatDate(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function clearSelection() {
  tableRef.value?.clearSelection?.()
  selectedRows.value = []
}

async function batchComment() {
  const rows = [...selectedReviewableRows.value]
  if (!rows.length || actionBusy.value) return
  try {
    await ElMessageBox.confirm(`确认对选中的 ${rows.length} 条日计划提交“建议继续执行”点评？`, '批量点评', { type: 'warning' })
    batchAction.value = 'comment'
    const payload = {
      ids: rows.map((row) => row.id),
      comment: '按计划继续推进。',
    }
    const results = isDepartmentMode.value
      ? await batchCommentDepartmentDailyReviewsApi(payload)
      : await batchCommentDailyReviewsApi(payload)
    rows.forEach((row) => { row.reviewStatus = '已点评' })
    clearSelection()
    await messageStore.refresh(true).catch(() => undefined)
    ElMessage.success(`已对 ${results.length} 条记录提交批量点评`)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(errorMessage(error))
  } finally {
    batchAction.value = ''
  }
}

async function submitComment() {
  if (!activeRow.value || submitting.value) return
  const comment = commentForm.comment.trim()
  if (!comment) {
    ElMessage.warning('请填写点评内容')
    return
  }
  submitting.value = true
  try {
    const payload = { comment }
    const result = isDepartmentMode.value
      ? await commentDepartmentDailyReviewApi(activeRow.value.id, payload)
      : await commentDailyReviewApi(activeRow.value.id, payload)
    activeRow.value.reviewStatus = '已点评'
    activeRow.value.leaderComment = payload.comment
    commentVisible.value = false
    await messageStore.refresh(true).catch(() => undefined)
    ElMessage.success(result.message)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    if (isDepartmentMode.value) await departmentOrgScope.loadOrgScope()
    else await leaderOrgScope.loadOrgScope()
    await refresh(false)
    autoQuery.resume()
    if (routeDayPlanId) {
      await openDetailById(routeDayPlanId)
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
})
</script>

<style scoped>
.daily-review-page { min-width:0; }
.daily-review-status { display:flex; flex-wrap:wrap; align-items:center; gap:0; min-height:48px; padding:0 16px; border:1px solid var(--line); border-radius:8px; background:#f7f9f7; }
.daily-review-status .status-pill { padding:0 18px; border:0; background:transparent; }
.daily-review-status .status-pill:first-child { padding-left:0; }
.daily-review-status .status-pill + .status-pill { border-left:1px solid #d9e1dd; }
.daily-review-filter { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:12px 0; padding:12px; border:1px solid var(--line); border-radius:8px; background:#f7f9f7; }
.daily-review-filter::before { display:none; }
.daily-review-filter__label { flex:0 0 auto; padding:0 4px; color:#52675f; font-size:12px; font-weight:800; }
.daily-review-filter .el-select { width:168px; }
.daily-review-filter .el-date-editor { width:260px; }
.daily-review-batch { margin-bottom:12px; }
.daily-review-table { width:100%; }
.daily-review-table :deep(.el-table__header th) { height:46px; color:#355149; background:#edf3ef; font-size:12px; font-weight:800; }
.daily-review-table :deep(.el-table__cell) { padding:12px 0; vertical-align:top; }
.daily-review-table :deep(.el-table__body tr) { cursor:pointer; }
.daily-review-table :deep(.el-table__body tr:hover .daily-review-person strong) { color:var(--blue); }
.daily-review-index { color:#61736d; font:700 12px/1 "IBM Plex Mono","Cascadia Mono",monospace; }
.daily-review-person,.daily-review-content { padding:0; border:0; color:inherit; background:transparent; text-align:left; cursor:pointer; }
.daily-review-person { display:grid; width:100%; min-width:0; gap:4px; }
.daily-review-person strong { color:var(--ink); font-size:13px; transition:color .16s ease; }
.daily-review-person span { overflow:hidden; color:var(--muted); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.daily-review-person small { color:#7c8985; font:500 10px/1.4 "IBM Plex Mono","Cascadia Mono",monospace; }
.daily-review-content { display:-webkit-box; overflow:hidden; width:100%; color:#344a43; font-size:12px; line-height:1.65; white-space:pre-wrap; overflow-wrap:anywhere; -webkit-box-orient:vertical; -webkit-line-clamp:3; }
.daily-review-person:focus-visible,.daily-review-content:focus-visible { outline:2px solid var(--blue); outline-offset:3px; }
.daily-review-state { display:grid; gap:7px; min-width:0; }
.daily-review-state > div { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.daily-review-actions { display:flex; flex-wrap:nowrap; align-items:center; gap:12px; white-space:nowrap; }
.daily-review-state > span { overflow:hidden; color:var(--muted); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.daily-review-state > span.is-warning { color:#a14f38; }
.daily-review-actions .el-button { margin:0; }
.daily-review-pagination { justify-content:flex-end; }
.daily-review-detail { display:grid; gap:16px; }
.daily-review-detail__head { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; padding-bottom:16px; border-bottom:1px solid var(--line); }
.daily-review-detail__head > div:first-child { display:grid; gap:4px; min-width:0; }
.daily-review-detail__head > div:last-child { display:flex; flex-wrap:wrap; gap:6px; }
.daily-review-detail__head span { color:var(--blue); font-size:11px; font-weight:800; }
.daily-review-detail__head h2 { margin:0; color:var(--ink); font-size:22px; }
.daily-review-detail__head p { margin:0; color:var(--muted); font-size:12px; }
.daily-review-plan { border:1px solid #cfdad4; background:#fff; }
.daily-review-plan__title { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:12px 14px; border-bottom:1px solid #cfdad4; background:#e6f0f6; }
.daily-review-plan__title > div { display:grid; gap:2px; }
.daily-review-plan__title span { color:#35675c; font-size:10px; font-weight:800; }
.daily-review-plan__title h3,.daily-review-checks h3,.daily-review-trace h3 { margin:0; color:var(--ink); font-size:15px; }
.daily-review-plan__title small { color:var(--muted); font-size:10px; }
.daily-review-plan > p { min-height:150px; margin:0; padding:16px; color:#2f433d; font-size:13px; line-height:1.8; white-space:pre-wrap; overflow-wrap:anywhere; }
.daily-review-checks,.daily-review-trace { padding:16px; border:1px solid var(--line); border-radius:8px; background:#fff; }
.daily-review-checks { display:grid; gap:12px; }
.daily-review-checks dl { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); margin:0; border:1px solid var(--line); }
.daily-review-checks dl > div { display:grid; grid-template-columns:92px minmax(0,1fr); min-width:0; border-bottom:1px solid var(--line); }
.daily-review-checks dl > div:nth-child(odd) { border-right:1px solid var(--line); }
.daily-review-checks dl > div:nth-last-child(-n+2) { border-bottom:0; }
.daily-review-checks dt,.daily-review-checks dd { margin:0; padding:10px 12px; font-size:12px; line-height:1.55; overflow-wrap:anywhere; }
.daily-review-checks dt { color:#536860; background:#f5f8f6; font-weight:700; }
.daily-review-checks dd { color:var(--ink); }
.daily-review-checks dd.is-warning { color:#a14f38; }
.daily-review-trace { display:grid; gap:14px; }
.daily-review-detail__actions { position:sticky; bottom:0; display:flex; justify-content:flex-end; gap:10px; padding:12px 0 2px; background:#fff; }
@media (max-width:760px) {
  .daily-review-status { align-items:flex-start; flex-direction:column; padding:8px 12px; }
  .daily-review-status .status-pill { width:100%; padding:8px 0; }
  .daily-review-status .status-pill + .status-pill { border-top:1px solid #d9e1dd; border-left:0; }
  .daily-review-filter { align-items:stretch; }
  .daily-review-filter__label { width:100%; }
  .daily-review-filter .el-select,.daily-review-filter .el-date-editor { width:100%; }
  .daily-review-filter .el-button { flex:1 1 0; }
  .daily-review-detail__head,.daily-review-plan__title { flex-direction:column; }
  .daily-review-checks dl { grid-template-columns:1fr; }
  .daily-review-checks dl > div,.daily-review-checks dl > div:nth-child(odd) { border-right:0; border-bottom:1px solid var(--line); }
  .daily-review-checks dl > div:last-child { border-bottom:0; }
}
</style>
