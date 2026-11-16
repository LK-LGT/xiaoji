<template>
  <EmployeeMonthResultArchiveDetail v-if="type === 'MONTH'" />
  <main v-else class="archive-detail">
    <header class="detail-header">
      <div class="detail-command-row">
        <el-button class="back-command" text :icon="ArrowLeft" @click="backToArchive">返回成果档案</el-button>
        <div class="detail-actions">
          <el-button v-if="detail" :icon="Download" @click="printDetail">打印/下载</el-button>
          <el-button v-if="isRejected" type="primary" @click="handleRejection">前往处理退回</el-button>
        </div>
      </div>
      <div v-if="detail" class="detail-title">
        <h1>{{ title }}</h1>
        <div class="detail-identity">
          <el-tag :type="statusMeta.type" effect="plain">{{ statusMeta.label }}</el-tag>
          <span>{{ resultTypeLabel }}</span>
          <span aria-hidden="true">·</span>
          <span>{{ versionLabel }}</span>
        </div>
        <div class="detail-meta">
          <span><small>所属周期</small>{{ periodLabel || '--' }}</span>
          <span><small>提交时间</small>{{ dateTime(submittedAt) }}</span>
        </div>
      </div>
    </header>
    <el-alert v-if="errorMessage" type="warning" :closable="false" show-icon :title="errorMessage"><el-button link type="primary" @click="loadDetail">重试</el-button></el-alert>
    <el-skeleton v-if="loading" :rows="10" animated />
    <template v-else-if="detail">
      <nav class="detail-tabs"><button v-for="tab in tabs" :key="tab.value" type="button" :class="{ 'is-active': activeTab === tab.value }" @click="activeTab = tab.value">{{ tab.label }}</button></nav>
      <section v-if="activeTab === 'content'" class="detail-content">
        <div class="detail-grid">
          <section class="result-section">
            <h2>本次成果</h2>
            <div v-if="monthDetail" class="result-fields">
              <div class="result-field"><span>完成情况</span><p>{{ monthDetail.completionStatus || '无' }}</p></div>
              <div class="result-field"><span>实际成果</span><p>{{ monthDetail.description || '无' }}</p></div>
              <div class="result-field"><span>备注</span><p>{{ monthDetail.remark || '无' }}</p></div>
              <div class="result-field result-field--compact"><span>个人整体绩效</span><p>{{ monthDetail.personalPerformance ?? '未填写' }}</p></div>
            </div>
            <div v-else-if="weekDetail" class="result-fields">
              <div class="result-field"><span>完成情况</span><p>{{ weekDetail.completionStatus || '无' }}</p></div>
              <div class="result-field"><span>实际交付物</span><p>{{ weekDetail.deliverable || '无' }}</p></div>
              <div class="result-field"><span>补充说明</span><p>{{ weekDetail.remark || '无' }}</p></div>
            </div>
          </section>

          <aside class="archive-info">
            <h2>档案信息</h2>
            <dl>
              <div><dt>任务名称</dt><dd>{{ title }}</dd></div>
              <div><dt>所属周期</dt><dd>{{ periodLabel || '--' }}</dd></div>
              <div><dt>成果类型</dt><dd>{{ resultTypeLabel }}</dd></div>
              <div><dt>当前状态</dt><dd><el-tag size="small" :type="statusMeta.type" effect="plain">{{ statusMeta.label }}</el-tag></dd></div>
              <div><dt>成果版本</dt><dd>{{ versionLabel }}</dd></div>
              <div><dt>提交时间</dt><dd>{{ dateTime(submittedAt) }}</dd></div>
            </dl>
          </aside>
        </div>

        <section class="evidence-section">
          <h2>证明材料</h2>
          <div v-if="detail.evidences?.length" class="evidence-list">
            <div v-for="file in detail.evidences" :key="file.id">
              <span>{{ file.fileName }}</span>
              <small>{{ file.fileType }} · {{ fileSize(file.fileSize) }}</small>
              <el-button link type="primary" @click="downloadEvidence(file.id)">下载</el-button>
            </div>
          </div>
          <p v-else class="muted">未上传证明材料</p>
        </section>
      </section>
      <ResultApprovalTimeline v-else-if="activeTab === 'approval'" :events="approvalEvents" />
      <ResultVersionHistory v-else :versions="versions" @compare="compareVersion" />
    </template>
  </main>
</template>

<script setup lang="ts">
import { ArrowLeft, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEmployeeResultDetailApi, downloadEmployeeResultEvidenceApi, type EmployeeResultDetailResp } from '@/api/employee'
import { getEmployeeWeekResultDetailApi, downloadWeekResultEvidenceApi, type WeekResultRecord } from '@/api/weekResult'
import ResultApprovalTimeline from './components/ResultApprovalTimeline.vue'
import ResultVersionHistory from './components/ResultVersionHistory.vue'
import EmployeeMonthResultArchiveDetail from './components/EmployeeMonthResultArchiveDetail.vue'

type DetailTab = 'content' | 'approval' | 'history'
type ArchiveResultDetail = EmployeeResultDetailResp | WeekResultRecord
const route = useRoute(); const router = useRouter(); const resultBasePath = computed(() => route.path.startsWith('/leader/') ? '/leader/results' : '/employee/results'); const type = computed(() => String(route.params.type).toUpperCase() === 'WEEK' ? 'WEEK' : 'MONTH')
const loading = ref(false); const errorMessage = ref(''); const activeTab = ref<DetailTab>('content'); const detail = ref<ArchiveResultDetail | null>(null)
const tabs = [{ label: '成果内容', value: 'content' as const }, { label: '审批记录', value: 'approval' as const }, { label: '历史版本', value: 'history' as const }]
const monthDetail = computed(() => type.value === 'MONTH' ? detail.value as EmployeeResultDetailResp | null : null)
const weekDetail = computed(() => type.value === 'WEEK' ? detail.value as WeekResultRecord | null : null)
const title = computed(() => detail.value?.title || (type.value === 'MONTH' ? '月度成果' : '周度成果'))
const periodLabel = computed(() => monthDetail.value?.planTitle || detail.value?.resultDate || '')
const resultTypeLabel = computed(() => type.value === 'MONTH' ? '月度绩效成果' : '周度执行成果')
const versionLabel = computed(() => monthDetail.value?.versionNo || 'V1')
const submittedAt = computed(() => monthDetail.value?.submittedAt || weekDetail.value?.submitAt)
const statusMeta = computed(() => {
  const status = String(detail.value?.status || '')
  const values: Record<string, { label: string; type: 'primary' | 'success' | 'danger' | 'info' }> = {
    submitted: { label: '审批中', type: 'primary' }, confirmed: { label: '已确认', type: 'success' }, rejected: { label: '已退回', type: 'danger' },
    SUBMITTED: { label: '审批中', type: 'primary' }, APPROVED: { label: '已通过', type: 'success' }, REJECTED: { label: '已退回', type: 'danger' },
  }
  return values[status] || { label: '成果记录', type: 'info' as const }
})
const isRejected = computed(() => detail.value?.status === 'rejected' || detail.value?.status === 'REJECTED')
const approvalEvents = computed(() => {
  if (!detail.value) return []
  const month = monthDetail.value
  if (month) return [{ label: '员工提交', operatorName: '本人', occurredAt: month.submittedAt, state: 'SUBMITTED', current: month.status === 'submitted' }, ...(month.suggestedAt ? [{ label: '领导处理', operatorName: '直属领导', occurredAt: month.suggestedAt, comment: month.leaderSuggestion, state: month.status === 'rejected' ? 'REJECTED' : 'APPROVED', current: false }] : []), ...(month.confirmedAt ? [{ label: '部门确认', operatorName: '部门负责人', occurredAt: month.confirmedAt, comment: month.confirmComment, state: 'CONFIRMED', current: false }] : [])]
  const week = weekDetail.value
  if (!week) return []
  return [{ label: '员工提交', operatorName: '本人', occurredAt: week.submitAt, state: 'SUBMITTED', current: week.status === 'SUBMITTED' }, ...(week.approveAt ? [{ label: week.status === 'REJECTED' ? '审批退回' : '审批通过', operatorName: '审批人', occurredAt: week.approveAt, comment: week.approvalComment, state: week.status, current: false }] : [])]
})
const versions = computed(() => detail.value ? [{ resultId: detail.value.id, versionNo: monthDetail.value?.versionNo || 'V1', submittedAt: monthDetail.value?.submittedAt || weekDetail.value?.submitAt, changeSummary: '当前成果版本', current: true }] : [])
function dateTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 16) : '--' }
function fileSize(value?: number) { if (!value) return '未知大小'; if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`; return `${(value / 1024 / 1024).toFixed(2)} MB` }
async function loadDetail() { loading.value = true; errorMessage.value = ''; try { detail.value = type.value === 'MONTH' ? await getEmployeeResultDetailApi(String(route.params.resultId)) : await getEmployeeWeekResultDetailApi(String(route.params.resultId)) } catch (error) { errorMessage.value = error instanceof Error ? error.message : '成果详情加载失败' } finally { loading.value = false } }
function backToArchive() {
  const keys = ['view', 'archiveType', 'archiveMonth', 'archiveStatus', 'archiveKeyword', 'archivePage'] as const
  const query = Object.fromEntries(keys.flatMap((key) => route.query[key] == null ? [] : [[key, route.query[key]]]))
  void router.push({ path: resultBasePath.value, query: { ...query, view: 'archive' } })
}
function handleRejection() { void router.push({ path: `${resultBasePath.value}/workspace/${type.value}/${route.query.planId}`, query: { taskId: route.query.taskId, month: route.query.archiveMonth } }) }
async function downloadEvidence(id: string) { const resultId = detail.value?.id; if (!resultId) return; try { const blob = type.value === 'MONTH' ? await downloadEmployeeResultEvidenceApi(resultId, id) : await downloadWeekResultEvidenceApi(resultId, id); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = '成果证明材料'; link.click(); URL.revokeObjectURL(url) } catch (error) { errorMessage.value = error instanceof Error ? error.message : '附件下载失败' } }
function printDetail() { window.print() }; function compareVersion() { ElMessage.info('当前版本暂无可比较的相邻版本') }
onMounted(() => { if (type.value === 'WEEK') void loadDetail() })
</script>

<style scoped>
.archive-detail { width:100%; max-width:1320px; min-height:calc(100vh - 132px); margin:0 auto; padding:16px 24px 64px; box-sizing:border-box; color:#263a34; background:#fff; }
.detail-header { padding-bottom:20px; border-bottom:1px solid #d8e1dc; }
.detail-command-row { display:flex; align-items:center; justify-content:space-between; min-height:40px; }
.back-command { margin-left:-12px; }
.detail-actions { display:flex; align-items:center; gap:8px; }
.detail-title { max-width:980px; padding-top:14px; }
.detail-title h1 { margin:0; color:#20342d; font-size:24px; line-height:1.4; letter-spacing:0; overflow-wrap:anywhere; }
.detail-identity { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-top:10px; color:#61736b; font-size:13px; }
.detail-meta { display:flex; align-items:center; flex-wrap:wrap; gap:10px 28px; margin-top:13px; color:#314941; font-size:13px; }
.detail-meta span { display:flex; align-items:baseline; gap:8px; }
.detail-meta small { color:#7b8983; font-size:12px; }
.archive-detail > :deep(.el-alert),.archive-detail > :deep(.el-skeleton) { margin-top:18px; }
.detail-tabs { display:flex; gap:28px; min-height:52px; border-bottom:1px solid #d8e1dc; }
.detail-tabs button { position:relative; padding:0 6px; border:0; color:#66776f; background:transparent; font-size:15px; font-weight:650; letter-spacing:0; cursor:pointer; }
.detail-tabs button::after { position:absolute; right:0; bottom:-1px; left:0; height:3px; background:#2d776c; content:''; opacity:0; }
.detail-tabs button.is-active { color:#1e5f56; }
.detail-tabs button.is-active::after { opacity:1; }
.detail-content { width:100%; padding-top:22px; }
.detail-grid { display:grid; grid-template-columns:minmax(0,2fr) minmax(280px,1fr); align-items:start; gap:0 34px; }
.result-section,.archive-info,.evidence-section { min-width:0; }
.result-section { padding:0 4px 10px 0; }
.result-section h2,.archive-info h2,.evidence-section h2 { margin:0 0 16px; color:#294139; font-size:17px; line-height:1.4; letter-spacing:0; }
.result-fields { border-top:1px solid #dfe6e1; }
.result-field { padding:15px 2px 17px; border-bottom:1px solid #e3e9e5; }
.result-field span { color:#718078; font-size:12px; }
.result-field p { margin:7px 0 0; color:#20342d; font-size:14px; line-height:1.75; overflow-wrap:anywhere; white-space:pre-wrap; }
.result-field--compact p { font-variant-numeric:tabular-nums; font-weight:650; }
.archive-info { padding:0 0 8px 28px; border-left:1px solid #d8e1dc; }
.archive-info dl { margin:0; }
.archive-info dl > div { display:grid; grid-template-columns:78px minmax(0,1fr); gap:12px; padding:11px 0; border-bottom:1px solid #e5eae7; }
.archive-info dt { color:#718078; font-size:12px; }
.archive-info dd { min-width:0; margin:0; color:#294139; font-size:13px; line-height:1.6; overflow-wrap:anywhere; }
.evidence-section { grid-column:1 / -1; margin-top:22px; padding-top:20px; border-top:1px solid #d8e1dc; }
.evidence-list { border-top:1px solid #dfe6e1; }
.evidence-list > div { display:grid; grid-template-columns:minmax(0,1fr) 180px 72px; align-items:center; min-height:52px; border-bottom:1px solid #e0e6e2; }
.evidence-list > div > span { min-width:0; overflow-wrap:anywhere; }
.evidence-list small,.muted { color:#75847d; }
.evidence-list :deep(.el-button) { justify-self:end; }
.muted { margin:0; padding:14px 0; border-top:1px solid #dfe6e1; border-bottom:1px solid #e0e6e2; }
@media (max-width:1040px) {
  .archive-detail { padding-right:18px; padding-left:18px; }
  .detail-grid { grid-template-columns:1fr; }
  .archive-info { margin-top:24px; padding:20px 0 0; border-top:1px solid #d8e1dc; border-left:0; }
}
@media print {
  .archive-detail { max-width:none; min-height:0; padding:0; color:#111; background:#fff; }
  .detail-command-row,.detail-tabs,.detail-actions,.evidence-list :deep(.el-button) { display:none !important; }
  .detail-header { padding-bottom:16px; }
  .detail-title { max-width:none; padding-top:0; }
  .detail-content { padding-top:18px; }
  .detail-grid { grid-template-columns:minmax(0,2fr) minmax(260px,1fr); gap:24px; }
  .archive-info { padding-left:20px; }
}
</style>
