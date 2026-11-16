<template>
  <section class="page-panel">
    <div class="page-header"><div><h1 class="page-title">周计划审批</h1><p class="page-subtitle">{{ pageSubtitle }}</p></div><el-button :loading="loading" @click="loadRows">刷新</el-button></div>
    <div class="filter-bar mt16"><el-select v-model="status" clearable placeholder="全部状态"><el-option label="待审批" value="PENDING" /><el-option label="已通过" value="APPROVED" /><el-option label="已驳回" value="REJECTED" /></el-select><el-date-picker v-model="weekStart" type="date" value-format="YYYY-MM-DD" clearable placeholder="按周一筛选" @change="changeWeekStart" /><el-button @click="loadRows">查询</el-button></div>
    <div v-if="selectedRows.length" class="batch-bar week-approval-batch">
      <div>已选择 <strong>{{ selectedRows.length }}</strong> 条周计划。</div>
      <div class="batch-actions">
        <el-button size="small" type="success" :loading="batchAction === 'approve'" :disabled="actionBusy && batchAction !== 'approve'" @click="batchApprove">批量通过</el-button>
        <el-button size="small" type="danger" :loading="batchAction === 'reject'" :disabled="actionBusy && batchAction !== 'reject'" @click="batchReject">批量驳回</el-button>
        <el-button size="small" :disabled="actionBusy" @click="clearSelection">清空选择</el-button>
      </div>
    </div>
    <el-table ref="tableRef" v-loading="loading" class="mt16 approval-list-table leader-compact-table" :data="rows" row-key="id" border empty-text="暂无周计划审批记录" @selection-change="onSelectionChange">
      <el-table-column type="selection" width="48" :selectable="canSelect" />
      <el-table-column prop="employeeName" :label="ownerLabel" width="110" /><el-table-column prop="departmentName" label="组织" min-width="130" show-overflow-tooltip /><el-table-column prop="weekStart" label="周开始" width="110" /><el-table-column prop="weekEnd" label="周结束" width="110" /><el-table-column label="周计划" width="70"><template #default>1 份</template></el-table-column>
      <el-table-column label="AI检查" width="156"><template #default="{ row }"><PlanAiReviewCell :review="aiReviews.reviewFor(row.id)" :loading="aiReviews.loading.value" @view="openAiReview(row)" /></template></el-table-column>
      <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="statusMeta(row.status).type">{{ statusMeta(row.status).label }}</el-tag></template></el-table-column>
      <el-table-column label="审批意见" min-width="170"><template #default="{ row }"><div class="approval-list-text compact">{{ row.approvalComment || '待审批' }}</div></template></el-table-column><el-table-column label="操作" width="175" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="open(row)">查看详情</el-button><el-button v-if="row.status === 'PENDING'" link type="success" :disabled="actionBusy" @click="approve(row)">通过</el-button><el-button v-if="row.status === 'PENDING'" link type="danger" :disabled="actionBusy" @click="reject(row)">驳回</el-button></template></el-table-column>
    </el-table>
    <el-drawer v-model="drawer" title="周计划审批详情" size="min(1120px, 96vw)">
      <template v-if="detail"><el-descriptions :column="2" border><el-descriptions-item :label="ownerLabel">{{ detail.summary.employeeName }}</el-descriptions-item><el-descriptions-item label="自然周">{{ detail.summary.weekStart }} 至 {{ detail.summary.weekEnd }}</el-descriptions-item><el-descriptions-item label="AI检查"><PlanAiReviewCell :review="aiReviews.reviewFor(detail.summary.id)" :loading="aiReviews.loading.value" @view="openAiReview(detail.summary)" /></el-descriptions-item></el-descriptions>
        <h3 class="mt16">周计划表</h3><el-table class="approval-detail-table" :data="detailRows" border><el-table-column label="姓名" width="120"><template #default>{{ detail.summary.employeeName }}</template></el-table-column><el-table-column label="工作内容" min-width="420"><template #default="{ row }"><div class="approval-text-cell">{{ row.content || '未填写' }}</div></template></el-table-column><el-table-column label="考核标准（交付物、质量）" min-width="320"><template #default="{ row }"><div class="approval-standard-cell"><p><strong>交付物：</strong>{{ row.deliverable || '未填写' }}</p><p><strong>质量：</strong>{{ row.quality || '未填写' }}</p></div></template></el-table-column><el-table-column label="统计周期" width="210"><template #default>{{ detail.summary.weekStart }} 至 {{ detail.summary.weekEnd }}</template></el-table-column></el-table>
        <div v-if="detail.summary.status === 'PENDING'" class="drawer-actions"><el-button type="danger" @click="reject(detail.summary)">驳回</el-button><el-button type="primary" @click="approve(detail.summary)">审批通过</el-button></div>
      </template>
    </el-drawer>
    <PlanAiReviewDrawer v-model="aiReviewDrawerVisible" plan-type="WEEK" :review="aiReviewRow ? aiReviews.reviewFor(aiReviewRow.id) : null" :loading="aiReviews.loading.value" :subject="aiReviewRow ? `${aiReviewRow.weekStart} 至 ${aiReviewRow.weekEnd} 周计划` : ''" :context="aiReviewRow?.employeeName || ''" />
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import {
  approveDepartmentWeekPlanApi,
  approveLeaderWeekPlanApi,
  getDepartmentWeekPlanApprovalApi,
  getLeaderWeekPlanApi,
  listDepartmentWeekPlanApprovalsApi,
  listLeaderWeekPlansApi,
  rejectDepartmentWeekPlanApi,
  rejectLeaderWeekPlanApi,
  weekPlanStatusMeta,
  type WeekPlanDetail,
  type WeekPlanStatus,
  type WeekPlanSummary,
} from '@/api/weekPlan'
import { mergeWeekPlanFields } from '@/utils/weekPlanDisplay'
import PlanAiReviewCell from '@/components/PlanAiReviewCell.vue'
import PlanAiReviewDrawer from '@/components/PlanAiReviewDrawer.vue'
import { useAiReviewCollection } from '@/composables/useAiReviewCollection'
const route = useRoute(); const loading = ref(false); const rows = ref<WeekPlanSummary[]>([]); const status = ref(''); const weekStart = ref(currentMonday()); const drawer = ref(false); const detail = ref<WeekPlanDetail | null>(null)
const tableRef = ref()
const selectedRows = ref<WeekPlanSummary[]>([])
const batchAction = ref('')
const aiReviews = useAiReviewCollection('WEEK_PLAN')
const aiReviewDrawerVisible = ref(false)
const aiReviewRow = ref<WeekPlanSummary | null>(null)
const isDepartmentWorkspace = computed(() => route.path.startsWith('/department/'))
const ownerLabel = computed(() => isDepartmentWorkspace.value ? '直属领导' : '员工')
const pageSubtitle = computed(() => isDepartmentWorkspace.value
  ? '审批直属领导本人周计划；普通员工周计划仍由直属领导审批。'
  : '审批直属下属周计划，并对照父级月计划检查拆解质量。')
const detailRows = computed(() => detail.value ? [mergeWeekPlanFields(detail.value.items)] : [])
const actionBusy = computed(() => Boolean(batchAction.value))
function statusMeta(value: WeekPlanStatus) { return weekPlanStatusMeta[value] }
async function loadRows() { loading.value = true; try { const params = { status: status.value as 'PENDING' | 'APPROVED' | 'REJECTED' | '', weekStart: weekStart.value || undefined }; rows.value = isDepartmentWorkspace.value ? await listDepartmentWeekPlanApprovalsApi(params) : await listLeaderWeekPlansApi(params); await aiReviews.load(rows.value.map((row) => row.id)); clearSelection() } catch (e) { ElMessage.error(e instanceof Error ? e.message : '审批列表加载失败') } finally { loading.value = false } }
function changeWeekStart(value: string) { weekStart.value = value ? mondayOf(value) : '' }
async function open(row: WeekPlanSummary) { try { const [plan] = await Promise.all([isDepartmentWorkspace.value ? getDepartmentWeekPlanApprovalApi(row.id) : getLeaderWeekPlanApi(row.id), aiReviews.loadOne(row.id)]); detail.value = plan; drawer.value = true } catch (e) { ElMessage.error(e instanceof Error ? e.message : '详情加载失败') } }
async function openAiReview(row: WeekPlanSummary) { aiReviewRow.value = row; aiReviewDrawerVisible.value = true; await aiReviews.loadOne(row.id) }
function canSelect(row: WeekPlanSummary) { return row.status === 'PENDING' && !actionBusy.value }
function onSelectionChange(value: WeekPlanSummary[]) { selectedRows.value = value }
function clearSelection() { tableRef.value?.clearSelection?.(); selectedRows.value = [] }
function approvePlan(row: WeekPlanSummary, comment: string) { return isDepartmentWorkspace.value ? approveDepartmentWeekPlanApi(row.id, row.versionNo, comment) : approveLeaderWeekPlanApi(row.id, row.versionNo, comment) }
function rejectPlan(row: WeekPlanSummary, comment: string) { return isDepartmentWorkspace.value ? rejectDepartmentWeekPlanApi(row.id, row.versionNo, comment) : rejectLeaderWeekPlanApi(row.id, row.versionNo, comment) }
async function approve(row: WeekPlanSummary) { if (actionBusy.value) return; try { const { value } = await ElMessageBox.prompt('可填写审批意见', '通过周计划', { inputValue: '同意，按计划执行', inputValidator: (v) => v.length <= 500 || '审批意见不能超过500个字符' }); await approvePlan(row, value); ElMessage.success('周计划已审批通过'); drawer.value = false; await loadRows() } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(e instanceof Error ? e.message : '审批失败') } }
async function reject(row: WeekPlanSummary) { if (actionBusy.value) return; try { const { value } = await promptRejectComment('请输入驳回原因', '驳回周计划', '请详细说明需要修改、补充或重新拆解的内容'); await rejectPlan(row, value.trim()); ElMessage.success('周计划已驳回'); drawer.value = false; await loadRows() } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(e instanceof Error ? e.message : '驳回失败') } }
async function batchApprove() {
  const targets = [...selectedRows.value]
  if (!targets.length || actionBusy.value) return
  try {
    await ElMessageBox.confirm(`确认批量通过 ${targets.length} 条周计划？`, '批量审批', { confirmButtonText: '批量通过', cancelButtonText: '取消', type: 'success' })
    batchAction.value = 'approve'
    const results = await Promise.allSettled(targets.map((row) => approvePlan(row, '批量审批通过。')))
    const failed = results.filter((result) => result.status === 'rejected').length
    await loadRows()
    if (failed) ElMessage.warning(`${targets.length - failed} 条已通过，${failed} 条处理失败，请刷新后重试`)
    else ElMessage.success(`${targets.length} 条周计划已批量审批通过`)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e instanceof Error ? e.message : '批量审批失败')
  } finally { batchAction.value = '' }
}
async function batchReject() {
  const targets = [...selectedRows.value]
  if (!targets.length || actionBusy.value) return
  try {
    const { value } = await promptRejectComment('请输入批量驳回原因', '批量驳回周计划', '这段意见会应用到所选周计划')
    batchAction.value = 'reject'
    const results = await Promise.allSettled(targets.map((row) => rejectPlan(row, value.trim())))
    const failed = results.filter((result) => result.status === 'rejected').length
    await loadRows()
    if (failed) ElMessage.warning(`${targets.length - failed} 条已驳回，${failed} 条处理失败，请刷新后重试`)
    else ElMessage.success(`${targets.length} 条周计划已批量驳回`)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e instanceof Error ? e.message : '批量驳回失败')
  } finally { batchAction.value = '' }
}
function promptRejectComment(message: string, title: string, placeholder: string) {
  return ElMessageBox.prompt(message, title, {
    confirmButtonText: title.includes('批量') ? '批量驳回' : '确认驳回',
    cancelButtonText: '取消',
    customClass: 'week-plan-reject-message-box',
    inputType: 'textarea',
    inputPlaceholder: placeholder,
    inputValidator: (v) => !!v.trim() && v.length <= 500 || '请输入不超过500个字符的驳回原因',
  })
}
function currentMonday() {
  return mondayOf(formatDate(new Date()))
}
function mondayOf(value: string) {
  const date = parseDate(value)
  const offset = (date.getDay() + 6) % 7
  date.setDate(date.getDate() - offset)
  return formatDate(date)
}
function parseDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}
function formatDate(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
onMounted(async () => { await loadRows(); // BUG-R6-14：周计划主键是字符串 UUID，禁止 `Number(...)`。
const id = String(route.query.id ?? '').trim(); const row = rows.value.find((item) => String(item.id) === id); if (row) await open(row) })
</script>
<style scoped>
.drawer-actions { display:flex; justify-content:flex-end; gap:12px; margin-top:20px; }
.week-approval-batch { margin-top:16px; }
.approval-detail-table :deep(.cell) { white-space: normal; }
.approval-list-table :deep(.el-table__row) { vertical-align: top; }
.approval-list-text {
  max-height: 84px;
  overflow-y: auto;
  color: var(--ink);
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.approval-list-text.compact { max-height: 64px; }
.approval-text-cell,
.approval-parent-cell {
  max-height: 168px;
  overflow-y: auto;
  color: var(--ink);
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.approval-text-cell.compact { max-height: 120px; }
.approval-parent-cell { display: grid; gap: 4px; }
.approval-parent-cell strong { font-size: 13px; font-weight: 650; }
.approval-parent-cell small { color: var(--muted); }
.approval-standard-cell { display:grid; gap:6px; color:var(--ink); line-height:1.65; white-space:pre-wrap; overflow-wrap:anywhere; }
.approval-standard-cell p { margin:0; }
.approval-standard-cell strong { color:#304a42; }
:global(.week-plan-reject-message-box) { width: min(720px, calc(100vw - 32px)); max-width: calc(100vw - 32px); }
:global(.week-plan-reject-message-box .el-message-box__message) { margin-bottom: 12px; color: #3b4f48; line-height: 1.6; }
:global(.week-plan-reject-message-box .el-textarea__inner) { min-height: 180px !important; padding: 12px; line-height: 1.65; resize: vertical; }
</style>
